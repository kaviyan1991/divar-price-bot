"""Market statistics: medians, outliers, duplicates, below-market tag."""
import config


def median(values):
    v = sorted(values)
    n = len(v)
    if n == 0:
        return None
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2


def valid_prices(prices):
    """Drop fake prices: below the floor, then outliers vs the median."""
    base = [p for p in prices if p is not None and p >= config.PRICE_FLOOR]
    if len(base) < config.MIN_GROUP_SAMPLES:
        return base
    m = median(base)
    return [p for p in base if config.OUTLIER_LOW * m <= p <= config.OUTLIER_HIGH * m]


def group_median(prices):
    vp = valid_prices(prices)
    if len(vp) < config.MIN_GROUP_SAMPLES:
        return None, len(vp)
    return median(vp), len(vp)


def is_below_market(price, med):
    if price is None or med is None or price < config.PRICE_FLOOR:
        return False
    return price <= config.BELOW_MARKET_RATIO * med


def is_duplicate(a, b):
    """a, b: dicts with brand_model, year, color, mileage, price, zero_km."""
    if a.get("zero_km") or b.get("zero_km"):
        return False  # dealers list many identical zero-km cars
    for k in ("brand_model", "year", "color"):
        if not a.get(k) or a.get(k) != b.get(k):
            return False
    ma, mb = a.get("mileage"), b.get("mileage")
    if ma is None or mb is None or max(ma, mb) == 0:
        return False
    if abs(ma - mb) / max(ma, mb) > config.DUPLICATE_MILEAGE_TOL:
        return False
    pa, pb = a.get("price"), b.get("price")
    if pa and pb and abs(pa - pb) / max(pa, pb) > config.DUPLICATE_PRICE_TOL:
        return False
    return True
