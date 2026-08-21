"""Pure prediction logic — no I/O. Takes MatchState, returns Signal or None."""
from __future__ import annotations
from typing import Optional
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
    MIN_ODDS,
    STRONG_MOVEMENT_PCT,
)
from models import (
    MatchState,
    Signal,
    SignalDirection,
    SignalStrength,
    TotalOddsRow,
)


def _pct_change(opening: float, current: float) -> float:
    """Calculate percentage drop from opening to current. Positive = dropped."""
    if opening <= 0:
        return 0.0
    return (opening - current) / opening * 100.0


def _is_same_direction_consecutive(
    odds_list: list[float], min_count: int
) -> tuple[bool, int]:
    """Check if the last `min_count` entries all move in the same direction (decreasing).
    Returns (is_consecutive, actual_consecutive_count).
    """
    if len(odds_list) < min_count:
        return False, 0

    # Take the last N values
    recent = odds_list[-min_count:]

    # Check all decreasing (or equal) — odds dropping = side being backed
    for i in range(1, len(recent)):
        if recent[i] > recent[i - 1]:
            return False, 0

    # Count how many consecutive from the end
    count = 1
    for i in range(len(odds_list) - 2, -1, -1):
        if odds_list[i + 1] <= odds_list[i]:
            count += 1
        else:
            break

    return True, count


def _classify_strength(
    movement_pct: float,
    consecutive_count: int,
    game_time: int,
) -> SignalStrength:
    """Classify signal strength based on movement, consistency, and timing."""
    if (
        movement_pct >= STRONG_MOVEMENT_PCT
        and consecutive_count >= 5
        and IDEAL_GAME_TIME_LOW <= game_time <= IDEAL_GAME_TIME_HIGH
    ):
        return SignalStrength.STRONG
    elif movement_pct >= MIN_MOVEMENT_PCT and consecutive_count >= 3:
        return SignalStrength.MEDIUM
    else:
        return SignalStrength.WEAK


def analyze_match(state: MatchState) -> Optional[Signal]:
    """Run the full prediction algorithm on a tracked match.

    Returns a Signal if all conditions are met, None otherwise.
    """
    game = state.game

    # ── Pre-checks ─────────────────────────────────────────────────────────
    if not game.is_live:
        return None

    minute = game.current_minute
    if minute is None:
        return None

    # Condition A: Game time window
    if minute < MIN_GAME_TIME or minute > MAX_GAME_TIME:
        return None

    # Need opening odds set
    if (
        state.opening_over_odds is None
        or state.opening_under_odds is None
        or state.opening_line is None
    ):
        return None

    odds = state.odds_history

    # Condition F: Minimum data points
    if len(odds) < MIN_DATA_POINTS:
        return None

    # ── Extract current odds ─────────────────────────────────────────────
    current = odds[-1]
    line = current.line

    # Only analyze if the current line matches the opening line
    if line != state.opening_line:
        logger.debug(
            f"Match {state.match_id}: line shifted "
            f"({state.opening_line} -> {line}), skipping"
        )
        return None

    opening_over = state.opening_over_odds
    opening_under = state.opening_under_odds
    current_over = current.over_odds
    current_under = current.under_odds

    # Sanity checks - skip if current odds are None or invalid
    if current_over is None or current_under is None:
        return None
    if current_over <= 0 or current_under <= 0:
        return None
    if opening_over is None or opening_under is None:
        return None
    if opening_over <= 0 or opening_under <= 0:
        return None

    # Condition E: Score context — line not already beaten
    total_goals = game.total_goals
    if total_goals > line:
        return None
    if total_goals == line and minute > 70:
        return None

    # ── Calculate movement ──────────────────────────────────────────────
    over_drop = _pct_change(opening_over, current_over)
    under_drop = _pct_change(opening_under, current_under)

    if over_drop > under_drop:
        direction = SignalDirection.OVER
        signal_odds = current_over
        opening_signal_odds = opening_over
        movement_pct = over_drop
        side_odds_series = [o.over_odds for o in odds]
    elif under_drop > over_drop:
        direction = SignalDirection.UNDER
        signal_odds = current_under
        opening_signal_odds = opening_under
        movement_pct = under_drop
        side_odds_series = [o.under_odds for o in odds]
    else:
        return None

    # Condition B: Minimum movement threshold
    if movement_pct < MIN_MOVEMENT_PCT:
        return None

    # Condition C: Odds range filter
    if signal_odds < MIN_ODDS or signal_odds > MAX_ODDS:
        logger.debug(
            f"Match {state.match_id}: signal odds {signal_odds:.2f} "
            f"outside range [{MIN_ODDS}, {MAX_ODDS}]"
        )
        return None

    # Condition D: Directional consistency
    is_consec, consec_count = _is_same_direction_consecutive(
        side_odds_series, MIN_CONSECUTIVE_READINGS
    )
    if not is_consec:
        logger.debug(
            f"Match {state.match_id}: odds not consistently decreasing "
            f"(consecutive: {consec_count})"
        )
        return None

    # ── Anti-spam check ───────────────────────────────────────────────────
    half = game.current_half
    if half is None:
        return None

    # ── Classify strength ─────────────────────────────────────────────────
    strength = _classify_strength(movement_pct, consec_count, minute)

    logger.info(
        f"SIGNAL DETECTED: {game.home.name} vs {game.away.name} | "
        f"{direction.value} {line} @ {signal_odds:.2f} | "
        f"Movement: {movement_pct:.1f}% | Strength: {strength.value} | "
        f"Minute: {minute}'"
    )

    return Signal(
        match_id=state.match_id,
        home_name=game.home.name,
        away_name=game.away.name,
        league_name=game.league.name,
        home_score=game.home_score,
        away_score=game.away_score,
        game_time=minute,
        direction=direction,
        line=line,
        signal_odds=signal_odds,
        opening_odds=opening_signal_odds,
        current_over_odds=current_over,
        current_under_odds=current_under,
        opening_over_odds=opening_over,
        opening_under_odds=opening_under,
        movement_pct=movement_pct,
        strength=strength,
        consecutive_readings=consec_count,
    )
