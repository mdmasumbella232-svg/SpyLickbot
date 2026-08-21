"""Telegram message formatting and sending."""
from __future__ import annotations
import asyncio
from collections import deque
from loguru import logger
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_MAX_MSG_LENGTH,
    TELEGRAM_MSG_QUEUE_SIZE,
    TELEGRAM_SEND_DELAY,
)
from models import Signal, SignalDirection, SignalStrength

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
    raise


STRENGTH_EMOJI = {
    SignalStrength.STRONG: "\U0001f525",
    SignalStrength.MEDIUM: "\u26a1",
    SignalStrength.WEAK: "\U0001f4ca",
}

DIRECTION_EMOJI = {
    SignalDirection.OVER: "\U0001f7e2",
    SignalDirection.UNDER: "\U0001f534",
}


def _truncate(text: str, max_len: int = TELEGRAM_MAX_MSG_LENGTH) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 20] + "\n...[truncated]"


def format_signal_message(signal: Signal) -> str:
    """Format a prediction signal into a Telegram-friendly message."""
    strength_emoji = STRENGTH_EMOJI[signal.strength]
    direction_emoji = DIRECTION_EMOJI[signal.direction]
    direction_label = signal.direction.value

    msg = (
        f"{strength_emoji} {signal.strength.value.upper()} SIGNAL — OVER/UNDER PREDICTION\n\n"
        f"\u26bd {signal.home_name} vs {signal.away_name}\n"
        f"\U0001f3c6 {signal.league_name}\n"
        f"\u23f1 {signal.game_time}'  |  Score: {signal.home_score}-{signal.away_score}\n\n"
        f"\U0001f4c8 PREDICTION: {direction_label} {signal.line}\n"
        f"\U0001f4b0 Current Odds: {signal.signal_odds:.2f}\n"
        f"\U0001f4ca Opening Odds: {signal.opening_odds:.2f}\n"
        f"\U0001f4c9 Movement: -{signal.movement_pct:.1f}% \u2b07\ufe0f\n\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"{DIRECTION_EMOJI[SignalDirection.OVER]} Over {signal.line}: "
        f"{signal.current_over_odds:.2f} (was {signal.opening_over_odds:.2f})\n"
        f"{DIRECTION_EMOJI[SignalDirection.UNDER]} Under {signal.line}: "
        f"{signal.current_under_odds:.2f} (was {signal.opening_under_odds:.2f})\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        f"\u23f0 Signal at {signal.game_time}' | "
        f"Consecutive readings: {signal.consecutive_readings}\n"
        f"\U0001f3af Strength: {strength_emoji} {signal.strength.value}\n\n"
        f"\u26a0\ufe0f Analysis signal, not financial advice."
    )
    return _truncate(msg)


class TelegramSender:
    """Manages Telegram message queue and sending."""

    def __init__(self):
        self._bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self._queue: deque[str] = deque(maxlen=TELEGRAM_MSG_QUEUE_SIZE)
        self._sending = False

    async def send_signal(self, signal: Signal) -> bool:
        text = format_signal_message(signal)
        self._queue.append(text)
        return await self._flush_queue()

    async def send_text(self, text: str) -> bool:
        self._queue.append(_truncate(text))
        return await self._flush_queue()

    async def _flush_queue(self) -> bool:
        if self._sending:
            return True
        self._sending = True
        success = True
        try:
            while self._queue:
                msg = self._queue.popleft()
                try:
                    await self._bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=msg,
                    )
                    logger.info(f"Sent Telegram message ({len(msg)} chars)")
                except TelegramError as e:
                    logger.error(f"Telegram send failed: {e}")
                    success = False
                    if "429" in str(e) or "timeout" in str(e).lower():
                        self._queue.appendleft(msg)
                        await asyncio.sleep(2)
                        continue
                await asyncio.sleep(TELEGRAM_SEND_DELAY)
        finally:
            self._sending = False
        return success

    async def send_startup_message(self):
        msg = (
            "\U0001f7e2 <b>O/U Prediction Bot Started</b>\n\n"
            "Monitoring live football matches for Total Over/Under signals.\n"
            "Odds range: 1.80 - 3.00\n"
            "Movement threshold: 15%+\n\n"
            "You will receive signals here when suspicious odds movement is detected."
        )
        await self.send_text(msg)
