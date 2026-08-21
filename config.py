"""All tunable constants and configuration for the O/U prediction bot."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── API ───────────────────────────────────────────────────────────────────────
API_BASE_URL = "https://inforadar.live/api/v1"
TOTAL_MARKET_ID = 6          # Over/Under Total market
LIVE_ENDPOINT = "/live_games"
FINISHED_ENDPOINT = "/finished_games"
ODDS_ENDPOINT = "/soccer/game/odds"
VIEW_ENDPOINT = "/soccer/game/view"

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8923430348:AAHDp2u-eHJ2tm5vi2VA3BSoXll6TTlzqxs")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7200809630")

# ── Polling ───────────────────────────────────────────────────────────────────
LIVE_POLL_INTERVAL = 30          # seconds between /live_games polls
ODDS_POLL_INTERVAL = 60          # seconds per match between odds fetches
ODDS_STAGGER_BASE = 60          # divided by N live matches for round-robin
MAX_CONCURRENT_MATCHES_BEFORE_SLOW = 40  # above this, increase odds interval
HTTP_TIMEOUT = 10                # seconds (for live_games)
ODDS_HTTP_TIMEOUT = 20          # seconds (odds payloads are heavier)
MAX_RETRIES = 2
MATCH_FAILURE_COOLDOWN = 120  # skip match for 2 min after repeated failures
COURTESY_DELAY = 0.5            # seconds between consecutive HTTP calls

# ── Prediction Algorithm ─────────────────────────────────────────────────────
MIN_GAME_TIME = 15               # minimum match minute to consider
MAX_GAME_TIME = 80               # maximum match minute to consider
IDEAL_GAME_TIME_LOW = 25         # lower bound for "strong" window
IDEAL_GAME_TIME_HIGH = 55        # upper bound for "strong" window

MIN_MOVEMENT_PCT = 15.0          # minimum % drop to trigger signal
STRONG_MOVEMENT_PCT = 25.0       # threshold for "strong" classification

MIN_ODDS = 1.80                  # lower bound for signal odds
MAX_ODDS = 3.00                  # upper bound for signal odds

MIN_DATA_POINTS = 5              # minimum odds entries required
MIN_CONSECUTIVE_READINGS = 3     # same-direction filter

MAX_SIGNALS_PER_HALF = 1         # anti-spam: max signals per half per match
DIRECTION_FLIP_MIN_GAP = 15      # minutes between opposite-direction signals

# ── Error Handling ─────────────────────────────────────────────────────────────
RATE_LIMIT_INITIAL_BACKOFF = 30  # seconds
RATE_LIMIT_MAX_BACKOFF = 300     # seconds (5 min)
SERVER_ERROR_RETRIES = 3
SERVER_ERROR_BACKOFFS = [5, 15, 30]
CONSECUTIVE_FAILURE_ALERT = 8    # alert after this many consecutive failures
FAILURE_PAUSE_DURATION = 60      # pause all polling for this many seconds
MATCH_MAX_FAILURES = 3          # failures before per-match cooldown

# ── Telegram Limits ───────────────────────────────────────────────────────────
TELEGRAM_MSG_QUEUE_SIZE = 20
TELEGRAM_SEND_DELAY = 0.05       # 50ms between sends
TELEGRAM_MAX_MSG_LENGTH = 4096
