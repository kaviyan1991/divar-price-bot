"""All settings in one place. Change values here, not in the code."""
import os

# --- Divar search ---
CITY_IDS = ["889"]            # 889 = Gilan province (verified on divar.ir)
CATEGORY = "light"            # passenger cars
# Divar's own brand filter values (verified 2026-09-30).
# Range Rover is listed under "Land Rover" on Divar. Bentley has no brand option on Divar.
BRANDS = [
    "Toyota", "Nissan", "Honda", "Suzuki", "Mitsubishi", "Hyundai", "Kia",
    "Mercedes-Benz", "BMW", "Porsche", "Land Rover", "Audi", "Maserati",
]
MIN_JALALI_YEAR_FILTER = "1398"   # server-side pre-filter (Divar uses Jalali years)
MIN_YEAR = 2020                   # final filter on Gregorian production year

# --- Politeness / limits per run ---
SLEEP_MIN, SLEEP_MAX = 2.0, 4.0   # seconds between Divar requests
BACKFILL_PAGES_PER_RUN = 60   # first run only
NEW_PAGES_PER_RUN = 5
DETAILS_PER_RUN = 60
RECHECKS_PER_RUN = 25
POSTS_PER_RUN = 20
BACKOFF_MINUTES = 60              # pause crawling after a 403/429

# --- Night schedule (Tehran time): crawl only once per hour ---
NIGHT_START_HOUR, NIGHT_END_HOUR = 1, 7

# --- Price rules ---
PRICE_FLOOR = 50_000_000          # toman; lower prices are treated as fake
OUTLIER_LOW, OUTLIER_HIGH = 0.40, 2.50
MIN_GROUP_SAMPLES = 5
BELOW_MARKET_RATIO = 0.85         # 15% or more below the median
DUPLICATE_MILEAGE_TOL = 0.02
DUPLICATE_PRICE_TOL = 0.10
DUPLICATE_WINDOW_DAYS = 30
REMOVAL_MISSES = 2                # consecutive "not found" checks before marking removed

# --- Telegram ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1004396296582")   # "Divar Car Price"

DB_PATH = os.environ.get("DB_PATH", "ads.db")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"
