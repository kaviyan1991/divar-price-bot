"""Weekly market report posted to the channel (Friday afternoon, Tehran time)."""
import datetime as dt

import analytics
import config
import db
import stats
import telegram
from textutil import escape_html, fa_num, fmt_price

TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))


def price_at(con, token, when):
    r = con.execute("SELECT price FROM price_history WHERE token=? AND seen_at<=? "
                    "ORDER BY seen_at DESC LIMIT 1", (token, when)).fetchone()
    return r["price"] if r else None


def build(con, now):
    week_ago = (now - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    groups = exact_groups(con)
    moves = []
    for ads in groups:
        bm, year = ads[0]["brand_model"], ads[0]["year"]
        cur = [a["current_price"] for a in ads if a["status"] == "active"]
        old = []
        for a in ads:
            alive = a["first_seen"] <= week_ago and (a["removed_at"] is None or a["removed_at"] > week_ago)
            if alive:
                old.append(price_at(con, a["token"], week_ago))
        m_now, n_now = stats.group_median(cur)
        m_old, n_old = stats.group_median(old)
        if m_now and m_old:
            moves.append(((m_now - m_old) * 100 / m_old, f"{bm} ({analytics.describe(ads[0])})", year, m_now))
    new_ads = con.execute("SELECT COUNT(*) FROM ads WHERE status<>'skipped' AND first_seen>?",
                          (week_ago,)).fetchone()[0]
    gone = con.execute("SELECT COUNT(*) FROM ads WHERE status='removed' AND removed_at>?",
                       (week_ago,)).fetchone()[0]
    lines = ["🗓 <b>گزارش هفتگی بازار خودروهای خارجی گیلان</b>",
             f"آگهی جدید این هفته: {fa_num(new_ads)} | حذف‌شده (احتمالاً فروخته‌شده): {fa_num(gone)}"]
    if not moves:
        lines.append("\nهنوز دادهٔ کافی برای مقایسه با هفتهٔ قبل جمع نشده است.")
        return "\n".join(lines + market_lines(con, now))
    ups = sorted([m for m in moves if m[0] > 0.5], reverse=True)[:5]
    downs = sorted([m for m in moves if m[0] < -0.5])[:5]
    if ups:
        lines.append("\n📈 <b>بیشترین گرانی:</b>")
        lines += [f"• {escape_html(bm)} {fa_num(y)}: {fa_num(round(p, 1))}٪+ ← {fmt_price(int(m))}"
                  for p, bm, y, m in ups]
    if downs:
        lines.append("\n📉 <b>بیشترین ارزانی:</b>")
        lines += [f"• {escape_html(bm)} {fa_num(y)}: {fa_num(round(abs(p), 1))}٪- ← {fmt_price(int(m))}"
                  for p, bm, y, m in downs]
    if not ups and not downs:
        lines.append("\nقیمت‌ها این هفته تقریباً ثابت بودند.")
    lines.append("\n(بر اساس میانهٔ قیمت؛ فقط مدل‌هایی که حداقل ۵ آگهی دارند)")
    lines += market_lines(con, now)
    return "\n".join(lines)


def exact_groups(con):
    """Groups of cars that share every compared spec."""
    groups = {}
    for a in con.execute("SELECT * FROM ads WHERE duplicate_of IS NULL "
                         "AND status IN ('active','removed') AND brand_model IS NOT NULL"):
        key = analytics.spec_key(a)
        if key is None:
            continue
        groups.setdefault(key, []).append(a)
    return list(groups.values())


def _label(ad):
    return f"{ad['brand_model']} {ad['year']} ({analytics.describe(ad)})"


def market_lines(con, now):
    speeds, supplies = [], []
    for grp in exact_groups(con):
        rep = grp[0]
        days, n, label = analytics.sale_speed(con, rep)
        if label:
            speeds.append((days, _label(rep), n))
        cur, old = analytics.supply(con, rep, now)
        if old >= 3:
            supplies.append(((cur - old) * 100 / old, _label(rep), cur))
    out = []
    if speeds:
        speeds.sort()
        out.append("\n🔥 <b>سریع‌ترین فروش:</b>")
        out += [f"• {escape_html(bm)}: {fa_num(int(d))} روز" for d, bm, n in speeds[:3]]
        if len(speeds) > 3:
            out.append("🧊 <b>کندترین فروش:</b>")
            out += [f"• {escape_html(bm)}: {fa_num(int(d))} روز" for d, bm, n in speeds[-3:][::-1]]
    ups = sorted([s for s in supplies if s[0] >= 20], reverse=True)[:3]
    downs = sorted([s for s in supplies if s[0] <= -20])[:3]
    if ups:
        out.append("\n📦 <b>عرضهٔ بیشتر (احتمال ارزانی):</b>")
        out += [f"• {escape_html(bm)}: {fa_num(round(p))}٪+ ({fa_num(c)} آگهی)" for p, bm, c in ups]
    if downs:
        out.append("📦 <b>عرضهٔ کمتر (احتمال گرانی):</b>")
        out += [f"• {escape_html(bm)}: {fa_num(round(abs(p)))}٪- ({fa_num(c)} آگهی)" for p, bm, c in downs]
    return out


def maybe_post(con):
    t = dt.datetime.now(TEHRAN)
    if t.weekday() != 4 or t.hour < 12:   # Friday after 12:00 Tehran
        return
    week = t.strftime("%G-%V")
    if db.get_state(con, "last_report_week") == week:
        return
    text = build(con, dt.datetime.now(dt.timezone.utc))
    if config.DRY_RUN:
        print(text)
    else:
        telegram.send_message(config.CHANNEL_ID, text)
    db.set_state(con, "last_report_week", week)
    con.commit()


# ---------- nightly summary ----------

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def g2j(gy, gm, gd):
    """Gregorian -> Jalali (standard arithmetic algorithm)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
            + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def _group_median(con, ad):
    return analytics.market(con, ad)[0]


def build_daily(con, day):
    """day: a date in Tehran time."""
    start = dt.datetime(day.year, day.month, day.day, tzinfo=TEHRAN).astimezone(dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    s, e = start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    new = con.execute("SELECT * FROM ads WHERE post_eligible=1 AND status IN ('active','removed') "
                      "AND first_seen>=? AND first_seen<?", (s, e)).fetchall()
    gone = con.execute("SELECT COUNT(*) FROM ads WHERE status='removed' AND duplicate_of IS NULL "
                       "AND removed_at>=? AND removed_at<?", (s, e)).fetchone()[0]
    drops = []
    for r in con.execute("SELECT DISTINCT token FROM price_history WHERE seen_at>=? AND seen_at<?", (s, e)):
        pts = con.execute("SELECT price FROM price_history WHERE token=? AND seen_at<? ORDER BY seen_at",
                          (r["token"], e)).fetchall()
        if len(pts) >= 2 and pts[0]["price"] and pts[-1]["price"] and pts[-1]["price"] < pts[0]["price"]:
            ad = con.execute("SELECT * FROM ads WHERE token=?", (r["token"],)).fetchone()
            first, last = pts[0]["price"], pts[-1]["price"]
            if first >= config.PRICE_FLOOR and last >= config.PRICE_FLOOR:
                drops.append(((first - last) * 100 / first, ad, first, last))
    below = []
    for ad in new:
        if ad["status"] != "active":
            continue
        med = _group_median(con, ad)
        if stats.is_below_market(ad["current_price"], med):
            below.append(((med - ad["current_price"]) * 100 / med, ad))
    jy, jm, jd = g2j(day.year, day.month, day.day)
    lines = [f"🌙 <b>خلاصهٔ امروز بازار گیلان — {fa_num(jd)} {MONTHS[jm - 1]}</b>", "",
             f"🆕 آگهی جدید: {fa_num(len(new))}",
             f"❌ حذف‌شده (احتمالاً فروخته‌شده): {fa_num(gone)}",
             f"💰 کاهش قیمت: {fa_num(len(drops))} آگهی"]
    if below:
        lines.append("\n🔻 <b>زیر قیمت بازار امروز:</b>")
        for pct, ad in sorted(below, key=lambda x: x[0], reverse=True)[:5]:
            lines.append(f"• <a href=\"https://divar.ir/v/{ad['token']}\">{escape_html(ad['brand_model'])} "
                         f"{fa_num(ad['year'])}</a> — {fa_num(round(pct))}٪ زیر میانه")
    if drops:
        lines.append("\n📉 <b>بیشترین کاهش قیمت امروز:</b>")
        for pct, ad, first, last in sorted(drops, key=lambda x: x[0], reverse=True)[:3]:
            lines.append(f"• <a href=\"https://divar.ir/v/{ad['token']}\">{escape_html(ad['brand_model'])} "
                         f"{fa_num(ad['year'])}</a>: {fmt_price(first)} ← {fmt_price(last)} "
                         f"({fa_num(round(pct))}٪-)")
    counts = {}
    for ad in new:
        counts[ad["brand_model"]] = counts.get(ad["brand_model"], 0) + 1
    if counts:
        bm = max(counts, key=counts.get)
        lines.append(f"\n🔥 پرآگهی‌ترین مدل امروز: {escape_html(bm)} ({fa_num(counts[bm])} آگهی)")
    return "\n".join(lines)


def maybe_post_daily(con):
    """First run after 23:00 Tehran (or before 02:00 for yesterday if it was missed)."""
    t = dt.datetime.now(TEHRAN)
    if t.hour >= 23:
        day = t.date()
    elif t.hour < 2:
        day = t.date() - dt.timedelta(days=1)
    else:
        return
    if db.get_state(con, "last_daily") == day.isoformat():
        return
    text = build_daily(con, day)
    if config.DRY_RUN:
        print(text)
    else:
        telegram.send_message(config.CHANNEL_ID, text)
    db.set_state(con, "last_daily", day.isoformat())
    con.commit()
