"""Market analytics for Gilan ads. Every comparison uses cars that are identical in
every recorded spec: model, year, zero-km, plate type, fuel, gearbox, colour and the
condition of body, engine, chassis and gearbox. Mileage must be close (used cars).
Analyses that study one factor (mileage, year, plate, seller) keep all other specs equal."""
import datetime as dt

import config
import stats

HOT_DAYS, NORMAL_DAYS = 7, 21
DEALER_WORDS = ("نمایشگاه", "اتوگالری", "اتو گالری", "گالری", "نمایندگی", "autogallery")
SPEC_FIELDS = ("brand_model", "year", "zero_km", "customs", "fuel", "gearbox", "color",
               "body", "engine", "chassis", "gearbox_cond")
LABELS = {"customs": "نوع پلاک", "fuel": "سوخت", "gearbox": "گیربکس", "color": "رنگ",
          "body": "وضعیت بدنه", "engine": "وضعیت موتور", "chassis": "وضعیت شاسی",
          "gearbox_cond": "وضعیت گیربکس", "brand_model": "مدل", "year": "سال"}
UNKNOWN = (None, "", "نامشخص", "؟")
MIN_EXACT = config.MIN_GROUP_SAMPLES


def seller_type(text):
    t = (text or "").lower()
    return "نمایشگاه" if any(w in t for w in DEALER_WORDS) else "شخصی"


def _get(ad, f):
    try:
        return ad[f]
    except (KeyError, IndexError):
        return None


def missing_fields(ad, skip=()):
    return [f for f in SPEC_FIELDS if f not in skip and f != "zero_km" and _get(ad, f) in UNKNOWN]


def spec_key(ad, skip=()):
    """None if any required spec is unknown: such ads are never compared."""
    if missing_fields(ad, skip):
        return None
    return tuple(_get(ad, f) for f in SPEC_FIELDS if f not in skip)


def mileage_close(a, b):
    if a["zero_km"] or b["zero_km"]:
        return bool(a["zero_km"]) == bool(b["zero_km"])
    ma, mb = a["mileage"], b["mileage"]
    if ma is None or mb is None:
        return False
    return abs(ma - mb) <= max(15000, 0.25 * max(ma, mb))


def similar(con, ad, skip=(), statuses=("active", "removed"), include_self=False):
    """Ads identical to ad in every spec except those in skip ('mileage' skips the km check)."""
    key = spec_key(ad, skip)
    if key is None:
        return []
    marks = ",".join("?" * len(statuses))
    rows = con.execute(f"SELECT * FROM ads WHERE brand_model=? AND duplicate_of IS NULL "
                       f"AND status IN ({marks})", (ad["brand_model"], *statuses)).fetchall()
    out = []
    for r in rows:
        if r["token"] == ad["token"] and not include_self:
            continue
        if spec_key(r, skip) != key:
            continue
        if "mileage" not in skip and not mileage_close(ad, r):
            continue
        out.append(r)
    return out


def _ok(p):
    return p is not None and p >= config.PRICE_FLOOR


def _median_min(prices, n_min):
    vp = stats.valid_prices(prices)
    return (stats.median(vp), len(vp)) if len(vp) >= n_min else (None, len(vp))


def market(con, ad):
    """(median, n) of identical cars, or (None, n)."""
    return _median_min([r["current_price"] for r in similar(con, ad)], MIN_EXACT)


def market_status(con, ad):
    """Human-readable reason when no median can be shown."""
    miss = missing_fields(ad)
    if miss:
        return "مقایسه نشد (نامشخص: " + "، ".join(LABELS[f] for f in miss[:3]) + ")"
    med, n = market(con, ad)
    if med is None:
        return f"کمتر از {MIN_EXACT} آگهی کاملاً مشابه ({n})"
    return None


def mileage_effect(con, ad):
    """% price change per 10,000 km among cars identical except mileage. (pct, n)."""
    if ad["zero_km"]:
        return None, 0
    rows = [r for r in similar(con, ad, skip=("mileage",), include_self=True)
            if _ok(r["current_price"]) and r["mileage"] is not None]
    valid = set(stats.valid_prices([r["current_price"] for r in rows]))
    rows = [r for r in rows if r["current_price"] in valid]
    n = len(rows)
    if n < 4:
        return None, n
    med = stats.median([r["current_price"] for r in rows])
    xs = [r["mileage"] / 10000 for r in rows]
    ys = [r["current_price"] / med for r in rows]
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None, n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    if slope >= 0:
        return None, n
    return max(slope * 100, -15.0), n


def fair_price(con, ad):
    """Median of identical cars (any mileage), adjusted to this car's mileage."""
    if ad["zero_km"] or ad["mileage"] is None:
        return None
    grp = [r for r in similar(con, ad, skip=("mileage",))]
    med, n = _median_min([r["current_price"] for r in grp], MIN_EXACT)
    pct, _ = mileage_effect(con, ad)
    if not med or pct is None:
        return None
    kms = [r["mileage"] for r in grp if r["mileage"] is not None]
    delta = (ad["mileage"] - stats.median(kms)) / 10000
    return int(round(med * (1 + pct / 100 * delta) / 100_000_000) * 100_000_000)


def year_depreciation(con, ad):
    """% cheaper per year older, among cars identical except year (and mileage)."""
    rows = [r for r in similar(con, ad, skip=("year", "mileage"), include_self=True)
            if _ok(r["current_price"])]
    meds = {}
    for y in {r["year"] for r in rows}:
        m, _ = _median_min([r["current_price"] for r in rows if r["year"] == y], 2)
        if m:
            meds[y] = m
    years = sorted(meds)
    if len(years) < 2:
        return None, len(years)
    rates = [1 - (meds[o] / meds[n]) ** (1 / (n - o)) for o, n in zip(years, years[1:])]
    return sum(rates) / len(rates) * 100, len(years)


def plate_gap(con, ad):
    """Medians for free-zone vs national plate, all other specs identical."""
    rows = similar(con, ad, skip=("customs",), statuses=("active",), include_self=True)
    free = [r["current_price"] for r in rows if (r["customs"] or "").startswith("منطقه")]
    nat = [r["current_price"] for r in rows if r["customs"] == "پلاک ملی"]
    mf, nf = _median_min(free, 2)
    mn, nn = _median_min(nat, 2)
    return mf, nf, mn, nn


def sale_speed(con, ad):
    """Median days until removal for identical cars seen from their first day."""
    days = []
    for r in similar(con, ad, statuses=("removed",), include_self=True):
        if not r["post_eligible"]:
            continue
        try:
            a = dt.datetime.strptime(r["first_seen"], "%Y-%m-%dT%H:%M:%SZ")
            b = dt.datetime.strptime(r["removed_at"], "%Y-%m-%dT%H:%M:%SZ")
            days.append(max(0, (b - a).days))
        except (TypeError, ValueError):
            continue
    if len(days) < MIN_EXACT:
        return None, len(days), None
    med = stats.median(days)
    label = "🔥 داغ" if med <= HOT_DAYS else ("🙂 معمولی" if med <= NORMAL_DAYS else "🧊 راکد")
    return med, len(days), label


def supply(con, ad, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = similar(con, ad, include_self=True)
    cur = sum(1 for r in rows if r["status"] == "active")
    old = sum(1 for r in rows if r["first_seen"] and r["first_seen"] <= t
              and (r["removed_at"] is None or r["removed_at"] > t))
    return cur, old


def seller_split(con, ad):
    rows = similar(con, ad, statuses=("active",), include_self=True)
    dealer = [r["current_price"] for r in rows if r["seller_type"] == "نمایشگاه"]
    private = [r["current_price"] for r in rows if r["seller_type"] == "شخصی"]
    md, nd = _median_min(dealer, 2)
    mp, np_ = _median_min(private, 2)
    return md, nd, mp, np_


def describe(ad):
    """Short spec line of a comparison group."""
    parts = [ad["color"], ad["customs"], "بدنه: " + (ad["body"] or "؟")]
    if ad["fuel"] and ad["fuel"] != "بنزین":
        parts.append(ad["fuel"])
    return " | ".join(p for p in parts if p)
