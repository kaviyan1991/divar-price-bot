"""One run: crawl new ads, fetch details, re-check old ads, post/update the channel."""
import datetime as dt
import os
import sys

import admin
import analytics
import config
import db
import divar
import interact
import report
import stats
import telegram
from textutil import escape_html, fmt_int, fmt_price, to_fa_digits

TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_between(a, b):
    fa = dt.datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
    fb = dt.datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
    return max(0, (fb - fa).days)


# ---------- crawling ----------

def crawl_list(con, backfill):
    """Backfill = first run: read all pages once, store ads silently (no posting)."""
    known = {r[0] for r in con.execute("SELECT token FROM ads")}
    max_pages = config.BACKFILL_PAGES_PER_RUN if backfill else config.NEW_PAGES_PER_RUN
    pag = None
    added, pages = 0, 0
    while pages < max_pages:
        rows, nxt = divar.search_page(pag)
        pages += 1
        new_on_page = 0
        for r in rows:
            if r["token"] in known:
                con.execute("UPDATE ads SET last_seen=? WHERE token=?", (now_iso(), r["token"]))
                continue
            known.add(r["token"])
            new_on_page += 1
            if not r["image_url"] or r["image_count"] < 1:
                continue  # ads without photos are ignored
            con.execute(
                "INSERT INTO ads(token,title,list_price,list_mileage,image_url,image_count,city,"
                "status,first_seen,last_seen,post_eligible) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (r["token"], r["title"], r["list_price"], r["list_mileage"], r["image_url"],
                 r["image_count"], r["city"], "pending", now_iso(), now_iso(),
                 0 if backfill else 1))
            added += 1
        con.commit()
        if not nxt:
            break
        if not backfill and new_on_page == 0:
            break  # reached ads we already know
        pag = nxt
    if backfill:
        db.set_state(con, "backfill_done", "1")
    con.commit()
    admin.add_counter(con, "new", added)
    print(f"list: {pages} page(s), {added} new ad(s) stored")


def apply_detail(con, token, d):
    t = now_iso()
    if not d["year"] or d["year"] < config.MIN_YEAR:
        con.execute("UPDATE ads SET status='skipped', year=?, last_checked=? WHERE token=?",
                    (d["year"], t, token))
        return
    row = con.execute("SELECT title, list_price FROM ads WHERE token=?", (token,)).fetchone()
    price = d["price"] if d["price"] is not None else row["list_price"]
    words = d["brand_model"].split()
    customs = divar.customs_status((row["title"] or "") + " " + (d["description"] or ""))
    # Zero-km comes from the mileage field; the title is used only when mileage is missing
    # (titles like "بدنه صفر" were wrongly marking used cars as new).
    zero = 1 if (d["mileage"] == 0 or (d["mileage"] is None and "صفر" in (row["title"] or ""))) else 0
    con.execute(
        "UPDATE ads SET brand_model=?, brand=?, model=?, year=?, mileage=?, fuel=?, gearbox=?,"
        " body=?, customs=?, color=?, zero_km=?, first_price=?, current_price=?, status='active',"
        " last_checked=?, miss_count=0, engine=?, chassis=?, gearbox_cond=? WHERE token=?",
        (d["brand_model"], words[0] if words else "", words[1] if len(words) > 1 else "",
         d["year"], d["mileage"], d["fuel"], d["gearbox"], d["body"], customs, d["color"],
         zero, price, price, t, d.get("engine"), d.get("chassis"), d.get("gearbox_cond"), token))
    con.execute("UPDATE ads SET photo_url=?, seller_type=? WHERE token=?",
                (d.get("photo") or None,
                 analytics.seller_type((row["title"] or "") + " " + (d["description"] or "")), token))
    db.add_price(con, token, t, price)
    mark_duplicate(con, token)


def mark_duplicate(con, token):
    me = con.execute("SELECT * FROM ads WHERE token=?", (token,)).fetchone()
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.DUPLICATE_WINDOW_DAYS)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    others = con.execute(
        "SELECT * FROM ads WHERE token<>? AND brand_model=? AND year=? AND duplicate_of IS NULL "
        "AND (status='active' OR (status='removed' AND removed_at>=?))",
        (token, me["brand_model"], me["year"], cutoff)).fetchall()
    a = dict(me, price=me["current_price"])
    for o in others:
        if stats.is_duplicate(a, dict(o, price=o["current_price"])):
            con.execute("UPDATE ads SET duplicate_of=? WHERE token=?", (o["token"], token))
            return


def fetch_pending(con):
    rows = con.execute("SELECT token FROM ads WHERE status='pending' ORDER BY first_seen DESC "
                       "LIMIT ?", (config.DETAILS_PER_RUN,)).fetchall()
    for r in rows:
        status, d = divar.fetch_detail(r["token"])
        if status == 404:
            con.execute("UPDATE ads SET status='removed', removed_at=? WHERE token=?",
                        (now_iso(), r["token"]))
        elif d:
            apply_detail(con, r["token"], d)
        con.commit()
    print(f"details: {len(rows)} fetched")


def recheck(con):
    rows = con.execute("SELECT * FROM ads WHERE status='active' ORDER BY last_checked ASC "
                       "LIMIT ?", (config.RECHECKS_PER_RUN,)).fetchall()
    changed = 0
    for r in rows:
        status, d = divar.fetch_detail(r["token"])
        t = now_iso()
        if status == 404:
            misses = r["miss_count"] + 1
            if misses >= config.REMOVAL_MISSES:
                con.execute("UPDATE ads SET status='removed', removed_at=?, miss_count=?, "
                            "last_checked=? WHERE token=?",
                            (t, misses, t, r["token"]))
                changed += 1
                update_post(con, r["token"])
            else:
                con.execute("UPDATE ads SET miss_count=?, last_checked=? WHERE token=?",
                            (misses, t, r["token"]))
        elif d:
            price = d["price"]
            con.execute("UPDATE ads SET last_checked=?, miss_count=0 WHERE token=?", (t, r["token"]))
            text = (r["title"] or "") + " " + (d["description"] or "")
            con.execute(  # refresh specs so older ads get every comparison field
                "UPDATE ads SET fuel=?, gearbox=?, color=?, body=?, engine=?, chassis=?, gearbox_cond=?,"
                " customs=?, seller_type=?, photo_url=COALESCE(photo_url, ?) WHERE token=?",
                (d["fuel"], d["gearbox"], d["color"], d["body"], d.get("engine"), d.get("chassis"),
                 d.get("gearbox_cond"), divar.customs_status(text), analytics.seller_type(text),
                 d.get("photo") or None, r["token"]))
            if price is not None and price != r["current_price"]:
                notify_price_drop(con, r, price)
                con.execute("UPDATE ads SET current_price=?, first_price=CASE WHEN first_price IS NULL "
                            "OR first_price < ? THEN ? ELSE first_price END WHERE token=?",
                            (price, config.PRICE_FLOOR, price, r["token"]))
                db.add_price(con, r["token"], t, price)
                changed += 1
            update_post(con, r["token"])  # specs/market may have changed
        con.commit()
    print(f"recheck: {len(rows)} checked, {changed} changed")


# ---------- channel ----------

def notify_price_drop(con, ad, new_price):
    """Short reply under the channel post when the price drops (members don't notice edits)."""
    old = ad["current_price"]
    if (config.DRY_RUN or not ad["message_id"] or not old or old < config.PRICE_FLOOR
            or new_price < config.PRICE_FLOOR or new_price >= old):
        return
    pct = (old - new_price) * 100 / old
    if pct < config.PRICE_DROP_NOTIFY_PCT:
        return
    telegram.send_message(config.CHANNEL_ID,
                          f"📉 <b>کاهش قیمت</b>: {fmt_price(old)} ← {fmt_price(new_price)} "
                          f"(−{to_fa_digits(round(pct))}٪)", reply_to=ad["message_id"])

def market(con, ad):
    return analytics.market(con, ad)


def ad_url(ad):
    return f"https://divar.ir/v/{ad['token']}"


def caption(con, ad):
    med, n = market(con, ad)
    price = ad["current_price"]
    ok_price = price is not None and price >= config.PRICE_FLOOR
    year = ad["year"]
    year_txt = f"{to_fa_digits(year)} ({to_fa_digits(year - 621)})" if year else "؟"
    lines = [f"🚗 <b>{escape_html(ad['title'])}</b>", ""]
    specs = [
        ("🏷", "مدل", to_fa_digits(ad["brand_model"] or "؟")),
        ("📅", "سال ساخت", year_txt),
        ("🛣", "کارکرد", "صفر کیلومتر 🆕" if ad["zero_km"] else f"{fmt_int(ad['mileage'])} کیلومتر"),
        ("🎨", "رنگ", ad["color"] or "؟"),
        ("⛽", "سوخت", ad["fuel"] or "؟"),
        ("⚙️", "گیربکس", (ad["gearbox"] or "؟") + (f" ({ad['gearbox_cond']})" if ad["gearbox_cond"] else "")),
        ("🛡", "بدنه", ad["body"] or "؟"),
        ("🔧", "موتور", ad["engine"] or "؟"),
        ("🔩", "شاسی", ad["chassis"] or "؟"),
        ("📄", "پلاک", ad["customs"] or "؟"),
        ("📍", "شهر", ad["city"] or "گیلان"),
    ]
    hidden = ("؟", "", "تعیین‌نشده", "تعیین نشده")   # skip unknown specs to keep the card tidy
    lines += [f"{icon} {label}: {escape_html(str(value))}" for icon, label, value in specs
              if str(value).strip() not in hidden]
    lines += ["", f"💰 قیمت اولیه: {fmt_price(ad['first_price'])}"]
    first = ad["first_price"]
    if ok_price and first and first >= config.PRICE_FLOOR and price != first:
        pct = round((price - first) * 100 / first)
        arrow = "🔻" if pct < 0 else "🔺"
        lines.append(f"💰 قیمت فعلی: {fmt_price(price)} ({arrow}{to_fa_digits(abs(pct))}٪)")
    if med and analytics.unrealistic(price, med):
        lines.append(f"📊 میانگین بازار: {fmt_price(int(med))}")
        lines.append("⚠️ قیمت این آگهی با بازار جور نیست (احتمالاً قیمت واقعی نیست)")
    elif med:
        tail = ""
        if ok_price:
            diff = round((price - med) * 100 / med)
            tail = f" ({'+' if diff > 0 else ''}{to_fa_digits(diff)}٪)"
        same, other = analytics.color_split(con, ad)
        lines.append(f"📊 میانگین بازار: {fmt_price(int(med))}{tail}")
        lines.append(f"🔎 از {to_fa_digits(n)} آگهی مشابه ({to_fa_digits(same)} هم‌رنگ، "
                     f"{to_fa_digits(other)} رنگ دیگر)")
        stars, label = analytics.deal_stars(price, med)
        if stars:
            lines.append(f"🏅 امتیاز معامله: {'⭐' * stars} ({label})")
    else:
        lines.append(f"📊 میانگین بازار: {to_fa_digits(analytics.market_status(con, ad) or 'دادهٔ کافی نیست')}")
    fair = analytics.fair_price(con, ad)
    if fair:
        lines.append(f"⚖️ قیمت منصفانه با این کارکرد: {fmt_price(fair)}")
    days, _, label = analytics.sale_speed(con, ad)
    if label:
        lines.append(f"{label[:1]} سرعت فروش این مدل: {label[2:]} (معمولاً {to_fa_digits(int(days))} روزه)")
    if stats.is_below_market(price, med):
        lines.append("🔻 زیر قیمت بازار")
    if ad["duplicate_of"]:
        orig = con.execute("SELECT current_price, first_seen FROM ads WHERE token=?",
                           (ad["duplicate_of"],)).fetchone()
        if orig and orig["current_price"] and orig["current_price"] >= config.PRICE_FLOOR:
            lines.append(f"🔁 این ماشین قبلاً با قیمت {fmt_price(orig['current_price'])} آگهی شده بود")
        else:
            lines.append("🔁 این ماشین قبلاً هم آگهی شده بود")
    if ad["status"] == "removed":
        days = days_between(ad["first_seen"], ad["removed_at"])
        lines.append(f"❌ آگهی حذف شد (احتمالاً فروخته شد) بعد از {to_fa_digits(days)} روز")
        lines.append(f"آخرین قیمت: {fmt_price(price)}")
    return "\n".join(lines)[:1024]


def has_price(ad, con=None):
    """A real price: above the floor and not a placeholder far from similar cars."""
    p = ad["current_price"]
    if p is None or p < config.PRICE_FLOOR:
        return False
    if con is not None:
        avg, _ = analytics.market(con, ad)
        if analytics.unrealistic(p, avg):
            return False
    return True


def update_post(con, token):
    ad = con.execute("SELECT * FROM ads WHERE token=?", (token,)).fetchone()
    if not ad or not ad["message_id"] or config.DRY_RUN:
        return
    if not has_price(ad, con):
        # No real price: take the post down; it is posted again once a price is added.
        if telegram.delete_ad(ad["message_id"]):
            con.execute("UPDATE ads SET message_id=NULL, has_photo=0 WHERE token=?", (token,))
            return True
        # Telegram only lets bots delete posts younger than 48 hours: mark it clearly instead.
        note = "⛔ <b>قیمت ندارد</b> — تا وقتی فروشنده قیمت نگذارد، این آگهی معتبر نیست.\n\n"
        telegram.edit_ad(ad["message_id"], (note + caption(con, ad))[:1024],
                         bool(ad["has_photo"]), ad_url(ad))
        return False
    telegram.edit_ad(ad["message_id"], caption(con, ad), bool(ad["has_photo"]), ad_url(ad))


def fixups(con):
    """One-time repairs of data written by older versions."""
    con.execute("UPDATE ads SET year = year % 10000 WHERE year > 9999")
    con.execute("UPDATE ads SET zero_km=0 WHERE zero_km=1 AND mileage > 0")
    # "First price" must be the first real price, never a fake one like 1,000 toman.
    con.execute("UPDATE ads SET first_price=(SELECT price FROM price_history h WHERE h.token=ads.token "
                "AND h.price >= ? ORDER BY seen_at LIMIT 1) WHERE first_price IS NULL OR first_price < ?",
                (config.PRICE_FLOOR, config.PRICE_FLOOR))
    # Plate rule: anything not explicitly national is a free-zone (Anzali) plate.
    con.execute("UPDATE ads SET customs='پلاک منطقه آزاد' WHERE customs IS NULL OR "
                "customs IN ('نامشخص', 'منطقه آزاد / گذر موقت')")
    con.execute("UPDATE ads SET status='skipped' WHERE status IN ('active','pending') "
                "AND year IS NOT NULL AND year < ?", (config.MIN_YEAR,))
    con.commit()
    if db.get_state(con, "layout_version") != "2" and not config.DRY_RUN:
        rows = con.execute("SELECT token FROM ads WHERE message_id IS NOT NULL").fetchall()
        for r in rows:
            update_post(con, r["token"])
        db.set_state(con, "layout_version", "2")
        con.commit()
        print(f"fixups: {len(rows)} old post(s) re-formatted")
    if db.get_state(con, "noprice_cleanup") != "2" and not config.DRY_RUN:
        rows = con.execute("SELECT token FROM ads WHERE message_id IS NOT NULL AND "
                           "(current_price IS NULL OR current_price < ?)", (config.PRICE_FLOOR,)).fetchall()
        deleted = sum(1 for r in rows if update_post(con, r["token"]))
        db.set_state(con, "noprice_cleanup", "2")
        con.commit()
        print(f"fixups: {deleted} of {len(rows)} post(s) without price deleted; the rest were marked")


def postable(con):
    """Active new ads with a real price that are not in the channel yet."""
    return con.execute("SELECT * FROM ads WHERE status='active' AND post_eligible=1 AND "
                       "message_id IS NULL AND current_price >= ? ORDER BY first_seen ASC LIMIT ?",
                       (config.PRICE_FLOOR, config.POSTS_PER_RUN * 10)).fetchall()


def post_new(con):
    if db.get_state(con, "force_one") == "1":  # /testpost from the admin
        con.execute("UPDATE ads SET post_eligible=1 WHERE token=(SELECT token FROM ads WHERE "
                    "status='active' AND message_id IS NULL AND current_price >= ? "
                    "ORDER BY first_seen DESC LIMIT 1)", (config.PRICE_FLOOR,))
        db.set_state(con, "force_one", "0")
    elif db.get_state(con, "paused") == "1":
        print("channel: paused by admin")
        return
    rows = [ad for ad in postable(con) if has_price(ad, con)][:config.POSTS_PER_RUN]
    for ad in rows:
        cap = caption(con, ad)
        med, _ = market(con, ad)
        con.execute("UPDATE ads SET below_market=? WHERE token=?",
                    (1 if stats.is_below_market(ad["current_price"], med) else 0, ad["token"]))
        if config.DRY_RUN:
            print("---- would post ----\n" + cap)
            continue
        photo = ad["photo_url"] or ad["image_url"]
        mid = telegram.post_ad(cap, photo, ad_url(ad))
        if mid:
            con.execute("UPDATE ads SET message_id=?, has_photo=? WHERE token=?",
                        (mid, 1 if photo else 0, ad["token"]))
            interact.notify_watchers(con, ad, cap)
            admin.add_counter(con, "posts")
        con.commit()
    print(f"channel: {len(rows)} new post(s)")


# ---------- run ----------

def should_crawl(con):
    until = db.get_state(con, "backoff_until")
    if until and now_iso() < until:
        print("backing off until", until)
        return False
    t = dt.datetime.now(TEHRAN)
    if config.NIGHT_START_HOUR <= t.hour < config.NIGHT_END_HOUR and t.minute >= 15:
        print("night mode: skipping this run")
        return False
    return True


def main():
    if not config.DRY_RUN and not config.BOT_TOKEN:
        print("BOT_TOKEN is missing")
        return 1
    con = db.connect(config.DB_PATH)
    admin.count_run(con)
    if os.environ.get("FORCE_POST_ONE") == "1":  # manual test: post the newest ad now
        con.execute("UPDATE ads SET post_eligible=1 WHERE token=(SELECT token FROM ads WHERE "
                    "status='active' AND message_id IS NULL ORDER BY first_seen DESC LIMIT 1)")
        interact.process_updates(con)
        post_new(con)
        con.commit()
        con.close()
        return 0
    fixups(con)
    interact.process_updates(con)
    if not should_crawl(con):
        con.close()
        return 0
    try:
        crawl_list(con, backfill=db.get_state(con, "backfill_done") != "1")
        fetch_pending(con)
        recheck(con)
    except divar.Blocked as e:
        until = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=config.BACKOFF_MINUTES)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_state(con, "backoff_until", until)
        print("BLOCKED by Divar:", e, "- pausing until", until)
        admin.add_counter(con, "blocks")
        admin.notify(f"⚠️ دیوار درخواست‌های ربات را رد کرد ({e}). تا {until} جمع‌آوری متوقف می‌شود.")
    except divar.Unreachable as e:
        # Temporary network problem: keep what this run collected, retry on the next run.
        streak = int(db.get_state(con, "net_fail_streak", "0") or 0) + 1
        db.set_state(con, "net_fail_streak", streak)
        print("network problem, will retry next run:", e)
        if streak == 3:  # only bother the owner when it keeps happening
            admin.notify(f"⚠️ سه اجرای پشت‌سرهم به دیوار وصل نشد ({escape_html(str(e))[:150]}). "
                         "ربات خودش دوباره تلاش می‌کند.")
    else:
        db.set_state(con, "net_fail_streak", 0)
    con.commit()
    post_new(con)
    report.maybe_post(con)
    report.maybe_post_daily(con)
    admin.maybe_daily_health(con)
    interact.process_updates(con)
    con.commit()
    total = con.execute("SELECT status, COUNT(*) FROM ads GROUP BY status").fetchall()
    print("database:", {r[0]: r[1] for r in total})
    con.close()
    return 0


def run():
    try:
        return main()
    except Exception as e:  # tell the owner, then fail the run so it shows red in GitHub
        try:
            con = db.connect(config.DB_PATH)
            admin.add_counter(con, "errors")
            con.commit()
        except Exception:
            pass
        admin.notify(f"❌ خطا در اجرای ربات: <code>{escape_html(repr(e))[:300]}</code>")
        raise


if __name__ == "__main__":
    sys.exit(run())
