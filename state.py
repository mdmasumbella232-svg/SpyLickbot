"""In-memory state management for tracked matches."""
from __future__ import annotations
import time
from collections import OrderedDict
from typing import Optional
from loguru import logger
from models import (
    LiveGame,
    MatchState,
    SignalDirection,
    TotalOddsRow,
    TotalMarket,
)
from config import MAX_SIGNALS_PER_HALF


class MatchStateStore:
    """Asyncio-safe in-memory store for match tracking state."""
    MAX_TRACKED = 200

    def __init__(self):
        self._matches: OrderedDict[str, MatchState] = OrderedDict()

    def get(self, match_id: str) -> Optional[MatchState]:
        return self._matches.get(match_id)

    def get_all(self) -> list[MatchState]:
        return list(self._matches.values())

    def get_live_match_ids(self) -> set[str]:
        return {mid for mid, ms in self._matches.items() if ms.game.is_live}

    def add_or_update_game(self, game: LiveGame) -> MatchState:
        existing = self._matches.get(game.id)
        if existing:
            existing.game = game
            self._matches.move_to_end(game.id)
            return existing
        if len(self._matches) >= self.MAX_TRACKED:
            evicted_id, _ = self._matches.popitem(last=False)
            logger.debug(f"Evicted match {evicted_id} from state store")
        state = MatchState(match_id=game.id, game=game)
        self._matches[game.id] = state
        logger.debug(f"Added {game.id}: {game.home.name} vs {game.away.name}")
        return state

    def update_odds(self, match_id: str, market: TotalMarket):
        state = self._matches.get(match_id)
        if state is None:
            logger.warning(f"Received odds for untracked match {match_id}")
            return
        if not market.odds:
            return
        if state.opening_over_odds is None and market.odds:
            # Find first valid entry for opening odds
            first_valid = next(
                (o for o in market.odds if o.is_valid and o.over_odds and o.under_odds),
                None
            )
            if first_valid:
                state.opening_over_odds = first_valid.over_odds
                state.opening_under_odds = first_valid.under_odds
                state.opening_line = first_valid.line
                logger.info(
                    f"Opening odds for {match_id}: "
                    f"Over {first_valid.line} @ {first_valid.over_odds}, Under @ {first_valid.under_odds}"
                )
        state.odds_history = market.odds
        import time as _t
        state.last_odds_fetch = _t.time()

    def record_signal(self, match_id: str, half: int, direction_str: str, game_time: int):
        state = self._matches.get(match_id)
        if state:
            state.last_signal_half = half
            state.last_signal_direction = SignalDirection(direction_str)
            state.last_signal_time = game_time
            state.signals_sent += 1

    def can_send_signal(self, match_id: str, half: int, direction_str: str, game_time: int) -> bool:
        state = self._matches.get(match_id)
        if state is None:
            return True
        if (
            state.last_signal_half == half
            and state.signals_sent >= MAX_SIGNALS_PER_HALF
        ):
            return False
        if (
            state.last_signal_direction is not None
            and state.last_signal_direction.value != direction_str
            and state.last_signal_time is not None
        ):
            from config import DIRECTION_FLIP_MIN_GAP
            gap = game_time - state.last_signal_time
            if gap < DIRECTION_FLIP_MIN_GAP:
                return False
        return True

    def remove_match(self, match_id: str):
        self._matches.pop(match_id, None)

    def remove_non_live(self, live_ids: set[str]):
        to_remove = [mid for mid in self._matches if mid not in live_ids]
        for mid in to_remove:
            self.remove_match(mid)
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} non-live matches")

    @property
    def count(self) -> int:
        return len(self._matches)
