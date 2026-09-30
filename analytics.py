"""Market analytics for Gilan ads only (no other provinces mixed in)."""
import datetime as dt

import config
import stats

HOT_DAYS, NORMAL_DAYS = 7, 21
DEALER_WORDS = ("نمایشگاه", "اتوگالری", "اتو گالری", "گالری", "نمایندگی", "autogallery")


def seller_type(text):
    t = (text or "").lower()
    return "نمایشگاه" if any(w in t for w in DEALER_WORDS) else "شخصی"


def _used(con, brand_model, year=None):
    sql = ("SELECT * FROM ads WHERE brand_model=? AND zero_km=0 AND duplicate_of IS NULL "
           "AND status IN ('active','removed')")
    args = [brand_model]
    if year:
        sql += " AND year=?"
        args.append(year)
    return con.execute(sql, args).fetchall()


def _ok(p):
    return p is not None and p >= config.PRICE_FLOOR


def _median_min(prices, n_min):
    vp = stats.valid_prices(prices)
    return (stats.median(vp), len(vp)) if len(vp) >= n_min else (None, len(vp))


def mileage_effect(con, brand_model):
    """% price change per 10,000 km (negative), pooled over years. Returns (pct, n)."""
    rows = [r for r in _used(con, brand_model) if _ok(r["current_price"]) and r["mileage"] is not None]
    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)
    xs, ys = [], []
    for year, grp in by_year.items():
        med, n = _median_min([r["current_price"] for r in grp], 3)
        if not med:
            continue
        valid = set(stats.valid_prices([r["current_price"] for r in grp]))
        for r in grp:
            if r["current_price"] in valid:
                xs.append(r["mileage"] / 10000)
                ys.append(r["current_price"] / med)
    n = len(xs)
    if n < 8:
        return None, n
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return None, n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    if slope >= 0:
        return None, n  # noisy data: no reliable effect
    return max(slope * 100, -15.0), n


def fair_price(con, ad):
    """Gilan median of this model/year, adjusted for this car's mileage."""
    if ad["zero_km"] or ad["mileage"] is None or not ad["brand_model"]:
        return None
    grp = [r for r in _used(con, ad["brand_model"], ad["year"]) if r["token"] != ad["token"]]
    med, n = _median_min([r["current_price"] for r in grp], config.MIN_GROUP_SAMPLES)
    pct, _ = mileage_effect(con, ad["brand_model"])
    if not med or pct is None:
        return None
    kms = [r["mileage"] for r in grp if r["mileage"] is not None]
    if not kms:
        return None
    delta = (ad["mileage"] - stats.median(kms)) / 10000
    value = med * (1 + pct / 100 * delta)
    return int(round(value / 100_000_000) * 100_000_000)


def year_depreciation(con, brand_model):
    """Average % cheaper per year older. Returns (pct, number_of_years)."""
    rows = [r for r in _used(con, brand_model) if _ok(r["current_price"])]
    meds = {}
    for y in {r["year"] for r in rows}:
        m, _ = _median_min([r["current_price"] for r in rows if r["year"] == y], 3)
        if m:
            meds[y] = m
    years = sorted(meds)
    if len(years) < 2:
        return None, len(years)
    rates = []
    for older, newer in zip(years, years[1:]):
        gap = newer - older
        rates.append(1 - (meds[older] / meds[newer]) ** (1 / gap))
    return sum(rates) / len(rates) * 100, len(years)


def plate_gap(con, brand_model, year=None):
    rows = [r for r in _used(con, brand_model, year) if r["status"] == "active"]
    free = [r["current_price"] for r in rows if (r["customs"] or "").startswith("منطقه")]
    nat = [r["current_price"] for r in rows if r["customs"] == "پلاک ملی"]
    mf, nf = _median_min(free, 3)
    mn, nn = _median_min(nat, 3)
    return mf, nf, mn, nn


def sale_speed(con, brand_model):
    """Median days until removal, using only ads we saw from their first day."""
    rows = con.execute(
        "SELECT first_seen, removed_at FROM ads WHERE brand_model=? AND status='removed' "
        "AND post_eligible=1 AND duplicate_of IS NULL", (brand_model,)).fetchall()
    days = []
    for r in rows:
        try:
            a = dt.datetime.strptime(r["first_seen"], "%Y-%m-%dT%H:%M:%SZ")
            b = dt.datetime.strptime(r["removed_at"], "%Y-%m-%dT%H:%M:%SZ")
            days.append(max(0, (b - a).days))
        except (TypeError, ValueError):
            continue
    if len(days) < 3:
        return None, len(days), None
    med = stats.median(days)
    label = "🔥 داغ" if med <= HOT_DAYS else ("🙂 معمولی" if med <= NORMAL_DAYS else "🧊 راکد")
    return med, len(days), label


def supply(con, brand_model, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = con.execute("SELECT COUNT(*) FROM ads WHERE brand_model=? AND status='active' "
                      "AND duplicate_of IS NULL", (brand_model,)).fetchone()[0]
    old = con.execute("SELECT COUNT(*) FROM ads WHERE brand_model=? AND status IN ('active','removed') "
                      "AND duplicate_of IS NULL AND first_seen<=? AND (removed_at IS NULL OR removed_at>?)",
                      (brand_model, t, t)).fetchone()[0]
    return cur, old


def seller_split(con, brand_model, year=None):
    rows = [r for r in _used(con, brand_model, year) if r["status"] == "active"]
    dealer = [r["current_price"] for r in rows if r["seller_type"] == "نمایشگاه"]
    private = [r["current_price"] for r in rows if r["seller_type"] == "شخصی"]
    md, nd = _median_min(dealer, 3)
    mp, np_ = _median_min(private, 3)
    return md, nd, mp, np_
