"""Main entry point — async polling loops for the O/U prediction bot."""
from __future__ import annotations
import asyncio
import signal as sig_module
import sys
import time
from loguru import logger
from config import (
    CONSECUTIVE_FAILURE_ALERT,
    FAILURE_PAUSE_DURATION,
    LIVE_POLL_INTERVAL,
    MATCH_FAILURE_COOLDOWN,
    MATCH_MAX_FAILURES,
    MAX_CONCURRENT_MATCHES_BEFORE_SLOW,
    ODDS_POLL_INTERVAL,
    ODDS_STAGGER_BASE,
)
from api_client import APIClient
from state import MatchStateStore
from analyzer import analyze_match
from telegram_sender import TelegramSender

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
    ),
)
logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG",
)


class OUPredictionBot:
    def __init__(self):
        self.api = APIClient()
        self.state = MatchStateStore()
        self.telegram = TelegramSender()
        self._running = False
        self._pause_until = 0.0
        self._live_task = None
        self._odds_task = None
        self._match_fetch_times: dict[str, float] = {}
        self._match_failures: dict[str, int] = {}  # match_id -> consecutive failure count

    async def start(self):
        self._running = True
        logger.info("O/U Prediction Bot starting...")
        try:
            await self.telegram.send_startup_message()
        except Exception as e:
            logger.warning(f"Could not send startup message: {e}")

        self._live_task = asyncio.create_task(self._live_poll_loop())
        self._odds_task = asyncio.create_task(self._odds_poll_loop())
        try:
            await asyncio.gather(self._live_task, self._odds_task)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")
        finally:
            await self.stop()

    async def stop(self):
        self._running = False
        if self._live_task:
            self._live_task.cancel()
        if self._odds_task:
            self._odds_task.cancel()
        await self.api.close()
        logger.info("Bot stopped")

    async def _live_poll_loop(self):
        while self._running:
            try:
                await self._check_pause()
                games = await self.api.fetch_live_games()
                live_ids = {g.id for g in games}
                for game in games:
                    self.state.add_or_update_game(game)
                self.state.remove_non_live(live_ids)
                stale = set(self._match_fetch_times.keys()) - live_ids
                for mid in stale:
                    del self._match_fetch_times[mid]
                    self._match_failures.pop(mid, None)
                logger.debug(f"Tracking {self.state.count} matches, {len(games)} live")
            except Exception as e:
                logger.error(f"Error in live poll loop: {e}")
                await self._handle_failure(str(e))
            await asyncio.sleep(LIVE_POLL_INTERVAL)

    async def _odds_poll_loop(self):
        while self._running:
            try:
                await self._check_pause()
                tracked = self.state.get_all()
                if not tracked:
                    await asyncio.sleep(5)
                    continue
                n = len(tracked)
                if n > MAX_CONCURRENT_MATCHES_BEFORE_SLOW:
                    interval = ODDS_POLL_INTERVAL * (n / MAX_CONCURRENT_MATCHES_BEFORE_SLOW)
                else:
                    interval = ODDS_STAGGER_BASE
                stagger = max(interval / n, 1.0)

                for ms in tracked:
                    if not self._running:
                        break
                    now = time.time()
                    last = self._match_fetch_times.get(ms.match_id, 0)
                    if now - last < ODDS_POLL_INTERVAL:
                        continue
                    # Skip matches in failure cooldown
                    fails = self._match_failures.get(ms.match_id, 0)
                    if fails >= MATCH_MAX_FAILURES:
                        cooldown_end = self._match_fetch_times.get(ms.match_id, 0) + MATCH_FAILURE_COOLDOWN
                        if now < cooldown_end:
                            continue
                        # Cooldown expired, reset
                        self._match_failures[ms.match_id] = 0
                    try:
                        await self._fetch_and_analyze(ms.match_id)
                        self._match_fetch_times[ms.match_id] = now
                        self._match_failures[ms.match_id] = 0  # reset on success
                    except Exception as e:
                        self._match_failures[ms.match_id] = fails + 1
                        if self._match_failures[ms.match_id] >= MATCH_MAX_FAILURES:
                            logger.warning(
                                f"Match {ms.match_id} in cooldown ({MATCH_FAILURE_COOLDOWN}s) after {MATCH_MAX_FAILURES} failures"
                            )
                        else:
                            logger.debug(f"Odds fetch failed for {ms.match_id}: {e}")
                    await asyncio.sleep(stagger)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in odds poll loop: {e}")
                await self._handle_failure(str(e))
            await asyncio.sleep(2)

    async def _fetch_and_analyze(self, match_id: str):
        market = await self.api.fetch_odds(match_id)
        if market is None or not market.odds:
            return
        self.state.update_odds(match_id, market)
        ms = self.state.get(match_id)
        if ms is None:
            return
        signal = analyze_match(ms)
        if signal is None:
            return
        half = ms.game.current_half
        minute = ms.game.current_minute
        if not self.state.can_send_signal(match_id, half, signal.direction.value, minute or 0):
            logger.debug(f"Signal blocked by anti-spam for {match_id}")
            return
        success = await self.telegram.send_signal(signal)
        if success:
            self.state.record_signal(match_id, half, signal.direction.value, minute or 0)

    async def _check_pause(self):
        if self._pause_until > 0:
            remaining = self._pause_until - time.time()
            if remaining > 0:
                logger.warning(f"In failure pause, sleeping {remaining:.0f}s")
                await asyncio.sleep(remaining)
            self._pause_until = 0

    async def _handle_failure(self, error_msg: str):
        if self.api.consecutive_failures >= CONSECUTIVE_FAILURE_ALERT:
            self._pause_until = time.time() + FAILURE_PAUSE_DURATION
            await self.telegram.send_text(
                f"\u26a0\ufe0f <b>Bot Paused</b>\n\n"
                f"{self.api.consecutive_failures} consecutive API failures.\n"
                f"Pausing for {FAILURE_PAUSE_DURATION}s.\n\n"
                f"Last error: {error_msg[:200]}"
            )


async def main():
    bot = OUPredictionBot()
    loop = asyncio.get_running_loop()
    for s in (sig_module.SIGINT, sig_module.SIGTERM):
        loop.add_signal_handler(s, lambda: asyncio.create_task(bot.stop()))
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
