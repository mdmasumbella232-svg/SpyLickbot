"""Async HTTP client for the inforadar.live API."""
from __future__ import annotations
import asyncio
from typing import Optional
import httpx
from loguru import logger
from config import (
    API_BASE_URL,
    COURTESY_DELAY,
    HTTP_TIMEOUT,
    MAX_RETRIES,
    SERVER_ERROR_BACKOFFS,
)
from models import LiveGame, TotalMarket, parse_live_game, parse_total_market


class APIError(Exception):
    """Raised when an API call fails after all retries."""
    def __init__(self, url: str, status: int, body: str):
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"API {status} for {url}: {body[:200]}")


class RateLimitError(APIError):
    pass


class APIClient:
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limit_backoff: float = 30
        self._consecutive_failures: int = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(HTTP_TIMEOUT),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "OU-Prediction-Bot/1.0",
                },
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _reset_failures(self):
        self._consecutive_failures = 0

    async def _request(self, url: str) -> dict | list:
        """Make a GET request with retry logic."""
        client = await self._get_client()
        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await client.get(url)
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    raise APIError(url, resp.status_code, "Got HTML instead of JSON")

                if resp.status_code == 429:
                    self._consecutive_failures += 1
                    logger.warning(
                        f"Rate limited on {url}, "
                        f"backing off {self._rate_limit_backoff:.0f}s"
                    )
                    await asyncio.sleep(self._rate_limit_backoff)
                    self._rate_limit_backoff = min(
                        self._rate_limit_backoff * 2, 300
                    )
                    continue

                if resp.status_code >= 500:
                    self._consecutive_failures += 1
                    if attempt < len(SERVER_ERROR_BACKOFFS):
                        wait = SERVER_ERROR_BACKOFFS[attempt]
                        logger.warning(
                            f"Server error {resp.status_code} on {url}, "
                            f"retry in {wait}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise APIError(url, resp.status_code, resp.text[:500])

                resp.raise_for_status()
                self._consecutive_failures = 0
                self._rate_limit_backoff = 30
                data = resp.json()
                return data

            except httpx.TimeoutException as e:
                last_exc = e
                self._consecutive_failures += 1
                logger.warning(
                    f"Timeout on {url} (attempt {attempt + 1}/{MAX_RETRIES + 1})"
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(5)

            except httpx.HTTPStatusError as e:
                self._consecutive_failures += 1
                raise APIError(url, e.response.status_code, str(e)) from e

        self._consecutive_failures += 1
        raise APIError(url, 0, f"All retries failed: {last_exc}")

    async def fetch_live_games(self, sport_id: int = 1) -> list[LiveGame]:
        """Fetch all currently live football matches."""
        url = (
            f"{API_BASE_URL}/live_games?"
            f"sport_id={sport_id}&page=1&per_page=1000"
        )
        logger.debug("Fetching live games")
        await asyncio.sleep(COURTESY_DELAY)
        data = await self._request(url)
        games = [parse_live_game(g) for g in data.get("results", [])]
        total = data.get("pager", {}).get("total", 0)
        logger.info(f"Fetched {len(games)} live games (total: {total})")
        return games

    async def fetch_finished_games(
        self, sport_id: int = 1, page: int = 1, per_page: int = 50
    ) -> tuple[list[LiveGame], int]:
        """Fetch finished games for backtesting."""
        url = (
            f"{API_BASE_URL}/finished_games/?"
            f"sport_id={sport_id}&page={page}&per_page={per_page}"
        )
        logger.debug(f"Fetching finished games page {page}")
        await asyncio.sleep(COURTESY_DELAY)
        data = await self._request(url)
        games = [parse_live_game(g) for g in data.get("results", [])]
        total = data.get("pager", {}).get("total", 0)
        return games, total

    async def fetch_odds(
        self, event_id: str, market_ids: str = "6"
    ) -> Optional[TotalMarket]:
        """Fetch odds for a specific match.

        Args:
            event_id: The match ID.
            market_ids: Comma-separated market IDs. Default "6" = Total only.

        Returns:
            TotalMarket if found, None otherwise.
        """
        url = (
            f"{API_BASE_URL}/soccer/game/odds?"
            f"event_id={event_id}&odds_market={market_ids}"
        )
        logger.debug(f"Fetching odds for event {event_id}")
        await asyncio.sleep(COURTESY_DELAY)
        data = await self._request(url)

        if not isinstance(data, list):
            logger.warning(
                f"Unexpected odds response type for {event_id}: {type(data)}"
            )
            return None

        market = parse_total_market(data)
        if market is None:
            logger.debug(f"No Total market found for event {event_id}")
        else:
            logger.debug(
                f"Fetched {len(market.odds)} odds entries for event {event_id}"
            )
        return market

    async def fetch_match_view(self, event_id: str) -> dict:
        """Fetch full match details including events timeline."""
        url = f"{API_BASE_URL}/soccer/game/view?event_id={event_id}"
        await asyncio.sleep(COURTESY_DELAY)
        data = await self._request(url)
        return data
