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
    groups = {}
    for a in con.execute("SELECT * FROM ads WHERE zero_km=0 AND duplicate_of IS NULL "
                         "AND status IN ('active','removed') AND brand_model IS NOT NULL"):
        groups.setdefault((a["brand_model"], a["year"]), []).append(a)
    moves = []
    for (bm, year), ads in groups.items():
        cur = [a["current_price"] for a in ads if a["status"] == "active"]
        old = []
        for a in ads:
            alive = a["first_seen"] <= week_ago and (a["removed_at"] is None or a["removed_at"] > week_ago)
            if alive:
                old.append(price_at(con, a["token"], week_ago))
        m_now, n_now = stats.group_median(cur)
        m_old, n_old = stats.group_median(old)
        if m_now and m_old:
            moves.append(((m_now - m_old) * 100 / m_old, bm, year, m_now))
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


def market_lines(con, now):
    models = [r[0] for r in con.execute("SELECT DISTINCT brand_model FROM ads WHERE brand_model IS NOT NULL")]
    speeds, supplies = [], []
    for bm in models:
        days, n, label = analytics.sale_speed(con, bm)
        if label:
            speeds.append((days, bm, n))
        cur, old = analytics.supply(con, bm, now)
        if old >= 3:
            supplies.append(((cur - old) * 100 / old, bm, cur))
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
