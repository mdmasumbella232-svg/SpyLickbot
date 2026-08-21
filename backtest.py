"""Backtesting engine — run the prediction algorithm on finished games."""
from __future__ import annotations
import asyncio
import csv
import sys
from pathlib import Path
from loguru import logger
from config import (
    IDEAL_GAME_TIME_HIGH,
    IDEAL_GAME_TIME_LOW,
    MAX_GAME_TIME,
    MAX_ODDS,
    MIN_CONSECUTIVE_READINGS,
    MIN_DATA_POINTS,
    MIN_GAME_TIME,
    MIN_MOVEMENT_PCT,
    STRONG_MOVEMENT_PCT,
)
from api_client import APIClient
from models import (
    LiveGame,
    MatchState,
    Signal,
    SignalDirection,
    SignalStrength,
    TotalOddsRow,
    TotalMarket,
    parse_live_game,
    parse_total_market,
)

logger.remove()
logger.add(sys.stderr, level="INFO")


def _pct_change(opening: float, current: float) -> float:
    if opening <= 0:
        return 0.0
    return (opening - current) / opening * 100.0


def _is_consecutive(odds_list: list[float], min_count: int) -> tuple[bool, int]:
    if len(odds_list) < min_count:
        return False, 0
    recent = odds_list[-min_count:]
    for i in range(1, len(recent)):
        if recent[i] > recent[i - 1]:
            return False, 0
    count = 1
    for i in range(len(odds_list) - 2, -1, -1):
        if odds_list[i + 1] <= odds_list[i]:
            count += 1
        else:
            break
    return True, count


def backtest_single_match(
    odds_entries: list[TotalOddsRow], final_total_goals: int
) -> list[dict]:
    signals = []
    if not odds_entries:
        return signals
    opening = odds_entries[0]
    opening_over = opening.over_odds
    opening_under = opening.under_odds
    line = opening.line
    last_signal_half = None
    signals_this_half = 0

    for i in range(1, len(odds_entries)):
        entry = odds_entries[i]
        game_time = int(entry.game_time) if entry.game_time.isdigit() else 0
        if game_time <= 45:
            half = 1
        elif game_time <= 90:
            half = 2
        else:
            continue
        if half != last_signal_half:
            signals_this_half = 0
            last_signal_half = half
        if game_time < MIN_GAME_TIME or game_time > MAX_GAME_TIME:
            continue
        if i + 1 < MIN_DATA_POINTS:
            continue
        if entry.line != line:
            line = entry.line
            opening_over = entry.over_odds
            opening_under = entry.under_odds
            continue
        current_over = entry.over_odds
        current_under = entry.under_odds
        if current_over is None or current_under is None:
            continue
        if current_over <= 0 or current_under <= 0:
            continue
        score_total = entry.home_score + entry.away_score
        if score_total > line:
            continue
        if score_total == line and game_time > 70:
            continue
        over_drop = _pct_change(opening_over, current_over)
        under_drop = _pct_change(opening_under, current_under)
        if over_drop > under_drop:
            direction = "OVER"
            signal_odds = current_over
            opening_odds = opening_over
            movement = over_drop
            series = [o.over_odds for o in odds_entries[: i + 1]]
        elif under_drop > over_drop:
            direction = "UNDER"
            signal_odds = current_under
            opening_odds = opening_under
            movement = under_drop
            series = [o.under_odds for o in odds_entries[: i + 1]]
        else:
            continue
        if movement < MIN_MOVEMENT_PCT:
            continue
        if signal_odds < MIN_ODDS or signal_odds > MAX_ODDS:
            continue
        is_consec, consec = _is_consecutive(series, MIN_CONSECUTIVE_READINGS)
        if not is_consec:
            continue
        if signals_this_half >= 1:
            continue
        if (
            movement >= STRONG_MOVEMENT_PCT
            and consec >= 5
            and IDEAL_GAME_TIME_LOW <= game_time <= IDEAL_GAME_TIME_HIGH
        ):
            strength = "strong"
        elif movement >= MIN_MOVEMENT_PCT and consec >= 3:
            strength = "medium"
        else:
            strength = "weak"
        if direction == "OVER":
            result = "WIN" if final_total_goals > line else "LOSS"
        else:
            result = "WIN" if final_total_goals < line else "LOSS"
        payout = signal_odds if result == "WIN" else -1
        signals.append({
            "game_time": game_time,
            "half": half,
            "direction": direction,
            "line": line,
            "signal_odds": round(signal_odds, 3),
            "opening_odds": round(opening_odds, 3),
            "movement_pct": round(movement, 2),
            "consecutive": consec,
            "strength": strength,
            "final_goals": final_total_goals,
            "result": result,
            "payout": round(payout, 3),
        })
        signals_this_half += 1
    return signals


async def run_backtest(max_pages: int = 5, per_page: int = 50):
    api = APIClient()
    all_signals = []
    total_matches_checked = 0
    total_with_odds = 0
    try:
        for page in range(1, max_pages + 1):
            logger.info(f"Fetching finished games page {page}/{max_pages}")
            games, total = await api.fetch_finished_games(page=page, per_page=per_page)
            if not games:
                logger.info("No more games, stopping")
                break
            for game in games:
                total_matches_checked += 1
                try:
                    market = await api.fetch_odds(game.id)
                    if market is None or len(market.odds) < MIN_DATA_POINTS:
                        continue
                    total_with_odds += 1
                    signals = backtest_single_match(market.odds, game.total_goals)
                    for s in signals:
                        s["match_id"] = game.id
                        s["home"] = game.home.name
                        s["away"] = game.away.name
                        s["league"] = game.league.name
                        s["final_score"] = game.scores
                        all_signals.append(s)
                    if signals:
                        logger.info(
                            f"  {game.home.name} vs {game.away.name}: "
                            f"{len(signals)} signal(s)"
                        )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"  Skipping {game.id}: {e}")
                    continue
            logger.info(f"Page {page} done. Signals so far: {len(all_signals)}")
    finally:
        await api.close()
    print_results(all_signals, total_matches_checked, total_with_odds)
    save_csv(all_signals)


def print_results(signals: list[dict], total_checked: int, total_with_odds: int):
    print("\n" + "=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Matches checked:   {total_checked}")
    print(f"  Matches with odds: {total_with_odds}")
    print(f"  Total signals:     {len(signals)}")
    print()
    if not signals:
        print("  No signals generated.")
        return
    wins = [s for s in signals if s["result"] == "WIN"]
    losses = [s for s in signals if s["result"] == "LOSS"]
    win_rate = len(wins) / len(signals) * 100
    total_staked = len(signals)
    total_returned = sum(s["payout"] for s in signals)
    roi = (total_returned / total_staked) * 100
    print(f"  Wins:   {len(wins)}")
    print(f"  Losses: {len(losses)}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  ROI:      {roi:.1f}%")
    avg_odds = sum(s["signal_odds"] for s in signals) / len(signals)
    print(f"  Avg Odds: {avg_odds:.2f}")
    print()
    print("  --- By Strength ---")
    for strength in ["strong", "medium", "weak"]:
        subset = [s for s in signals if s["strength"] == strength]
        if not subset:
            print(f"  {strength}: no signals")
            continue
        sw = len([s for s in subset if s["result"] == "WIN"])
        sroi = sum(s["payout"] for s in subset) / len(subset) * 100
        print(f"  {strength:8s}: {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%)  ROI: {sroi:.1f}%")
    print()
    print("  --- By Direction ---")
    for direction in ["OVER", "UNDER"]:
        subset = [s for s in signals if s["direction"] == direction]
        if not subset:
            continue
        sw = len([s for s in subset if s["result"] == "WIN"])
        sroi = sum(s["payout"] for s in subset) / len(subset) * 100
        print(f"  {direction:6s}: {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%)  ROI: {sroi:.1f}%")
    print()
    print("  --- By Game Time ---")
    for lo, hi in [(15, 30), (30, 50), (50, 65), (65, 80)]:
        subset = [s for s in signals if lo <= s["game_time"] <= hi]
        if not subset:
            continue
        sw = len([s for s in subset if s["result"] == "WIN"])
        sroi = sum(s["payout"] for s in subset) / len(subset) * 100
        print(f"  {lo:2d}-{hi:2d}': {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%)  ROI: {sroi:.1f}%")
    print("=" * 60)


def save_csv(signals: list[dict]):
    if not signals:
        return
    out_path = Path("backtest_results.csv")
    fieldnames = [
        "match_id", "home", "away", "league", "final_score",
        "game_time", "half", "direction", "line", "signal_odds",
        "opening_odds", "movement_pct", "consecutive", "strength",
        "final_goals", "result", "payout",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in signals:
            writer.writerow({k: s.get(k, "") for k in fieldnames})
    print(f"\n  CSV saved to: {out_path.resolve()}")


if __name__ == "__main__":
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(run_backtest(max_pages=pages))
