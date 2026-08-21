"""Pydantic data models for API responses and internal state."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass
class TeamInfo:
    id: str
    name: str
    image_id: Optional[str] = None
    cc: Optional[str] = None


@dataclass
class LeagueInfo:
    id: str
    name: str
    cc: Optional[str] = None


@dataclass
class MatchTime:
    tm: Optional[int] = None
    ts: Optional[int] = None
    tt: Optional[str] = None
    ta: Optional[int] = None
    md: Optional[int] = None
    qTime: Optional[str] = None


@dataclass
class LiveGame:
    id: str
    home: TeamInfo
    away: TeamInfo
    league: LeagueInfo
    scores: str
    time: MatchTime
    start_time: int
    time_status: str
    stats: dict = field(default_factory=dict)

    @property
    def home_score(self) -> int:
        parts = self.scores.split("-")
        return int(parts[0]) if parts else 0

    @property
    def away_score(self) -> int:
        parts = self.scores.split("-")
        return int(parts[1]) if len(parts) > 1 else 0

    @property
    def total_goals(self) -> int:
        return self.home_score + self.away_score

    @property
    def is_live(self) -> bool:
        return self.time_status == "1"

    @property
    def is_finished(self) -> bool:
        return self.time_status == "3"

    @property
    def current_minute(self) -> Optional[int]:
        return self.time.tm

    @property
    def current_half(self) -> Optional[int]:
        if self.time.tt == "1":
            return 1
        elif self.time.tt == "2":
            return 2
        return None


@dataclass
class TotalOddsRow:
    home_score: int
    away_score: int
    over_odds: Optional[float]
    line: float
    under_odds: Optional[float]
    game_time: str
    world_time: int
    over_rating: Optional[float] = None
    under_rating: Optional[float] = None

    @property
    def is_valid(self) -> bool:
        return (
            self.over_odds is not None
            and self.under_odds is not None
            and self.over_odds > 0
            and self.under_odds > 0
            and self.game_time.isdigit()
        )

    @property
    def is_in_play(self) -> bool:
        return self.game_time.isdigit() and int(self.game_time) > 0


@dataclass
class TotalMarket:
    name: str
    full_time: int
    rows_names: list[str]
    odds: list[TotalOddsRow]


class SignalDirection(Enum):
    OVER = "OVER"
    UNDER = "UNDER"


class SignalStrength(Enum):
    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"


@dataclass
class Signal:
    match_id: str
    home_name: str
    away_name: str
    league_name: str
    home_score: int
    away_score: int
    game_time: int
    direction: SignalDirection
    line: float
    signal_odds: float
    opening_odds: float
    current_over_odds: float
    current_under_odds: float
    opening_over_odds: float
    opening_under_odds: float
    movement_pct: float
    strength: SignalStrength
    consecutive_readings: int


@dataclass
class MatchState:
    match_id: str
    game: LiveGame
    odds_history: list[TotalOddsRow] = field(default_factory=list)
    opening_over_odds: Optional[float] = None
    opening_under_odds: Optional[float] = None
    opening_line: Optional[float] = None
    last_signal_half: Optional[int] = None
    last_signal_direction: Optional[SignalDirection] = None
    last_signal_time: Optional[int] = None
    signals_sent: int = 0
    last_odds_fetch: float = 0.0


# ── Parsers ─────────────────────────────────────────────────────────


def parse_team(data: dict) -> TeamInfo:
    return TeamInfo(
        id=str(data.get("id", "")),
        name=data.get("name", "Unknown"),
        image_id=data.get("image_id"),
        cc=data.get("cc"),
    )


def parse_league(data: dict) -> LeagueInfo:
    return LeagueInfo(
        id=str(data.get("id", "")),
        name=data.get("name", "Unknown"),
        cc=data.get("cc"),
    )


def parse_time(data: dict) -> MatchTime:
    return MatchTime(
        tm=data.get("tm"),
        ts=data.get("ts"),
        tt=data.get("tt"),
        ta=data.get("ta"),
        md=data.get("md"),
        qTime=data.get("qTime"),
    )


def parse_live_game(data: dict) -> LiveGame:
    return LiveGame(
        id=str(data.get("id", "")),
        home=parse_team(data.get("home", {})),
        away=parse_team(data.get("away", {})),
        league=parse_league(data.get("league", {})),
        scores=data.get("scores", "0-0"),
        time=parse_time(data.get("time", {})),
        start_time=int(data.get("startTime", 0)),
        time_status=str(data.get("timeStatus", "")),
        stats=data.get("stats", {}),
    )


def parse_total_odds_row(data: dict) -> Optional[TotalOddsRow]:
    """Parse a single odds row, returning None if data is invalid."""
    ss = data.get("ss") or [None, None]
    rating = data.get("rating") or [None, None]

    row1 = data.get("row1")
    row2 = data.get("row2")
    row3 = data.get("row3")

    # Skip if critical fields are None
    if row2 is None:
        return None

    over_odds = float(row1) if row1 is not None else None
    line = float(row2)
    under_odds = float(row3) if row3 is not None else None

    home_score = ss[0] if ss[0] is not None else 0
    away_score = ss[1] if ss[1] is not None else 0

    over_rating = None
    under_rating = None
    if isinstance(rating[0], dict) and rating[0].get("rating") is not None:
        over_rating = rating[0]["rating"]
    if isinstance(rating[1], dict) and rating[1].get("rating") is not None:
        under_rating = rating[1]["rating"]

    return TotalOddsRow(
        home_score=home_score,
        away_score=away_score,
        over_odds=over_odds,
        line=line,
        under_odds=under_odds,
        game_time=str(data.get("game_time", "")),
        world_time=int(data.get("world_time", 0)),
        over_rating=over_rating,
        under_rating=under_rating,
    )


def parse_total_market(data: list[dict], target_line: Optional[float] = None) -> Optional[TotalMarket]:
    """Extract the Total market from the odds response array.

    Args:
        data: The full odds response (list of market objects).
        target_line: If specified, only return odds for this line value.
                       If None, auto-detect the most common in-play line.
    """
    if not data:
        return None

    for market in data:
        if market.get("name") != "Total":
            continue

        # Parse all rows, filtering out None results
        all_rows = []
        for r in market.get("odds", []):
            parsed = parse_total_odds_row(r)
            if parsed is not None:
                all_rows.append(parsed)

        if not all_rows:
            return None

        # If target_line specified, filter to that line
        if target_line is not None:
            filtered = [r for r in all_rows if r.line == target_line]
            if filtered:
                return TotalMarket(
                    name=market["name"],
                    full_time=market.get("fullTime", 90),
                    rows_names=market.get("rowsNames", []),
                    odds=filtered,
                )

        # Auto-detect: prefer standard European lines (2.0-3.5)
        # Exclude micro-lines (<1.5) and extreme lines (>4.0)
        from collections import Counter
        in_play_valid = [r for r in all_rows if r.is_valid]
        if not in_play_valid:
            in_play_valid = all_rows

        line_counts = Counter(r.line for r in in_play_valid)
        if not line_counts:
            return None

        # Priority: prefer lines between 2.0 and 3.5
        standard_lines = {k: v for k, v in line_counts.items() if 1.75 <= k <= 3.5}
        if standard_lines:
            best_line = max(standard_lines, key=standard_lines.get)
        else:
            # Fall back to any line with enough data
            best_line = line_counts.most_common(1)[0][0]
        filtered = [r for r in all_rows if r.line == best_line]

        # Reverse to chronological order (oldest first)
        filtered.reverse()
        return TotalMarket(
            name=market["name"],
            full_time=market.get("fullTime", 90),
            rows_names=market.get("rowsNames", []),
            odds=filtered,
        )

    return None
