"""One run: crawl new ads, fetch details, re-check old ads, post/update the channel."""
import datetime as dt
import os
import sys

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
    zero = 1 if (d["mileage"] == 0 or "صفر" in (row["title"] or "")) else 0
    con.execute(
        "UPDATE ads SET brand_model=?, brand=?, model=?, year=?, mileage=?, fuel=?, gearbox=?,"
        " body=?, customs=?, color=?, zero_km=?, first_price=?, current_price=?, status='active',"
        " last_checked=?, miss_count=0 WHERE token=?",
        (d["brand_model"], words[0] if words else "", words[1] if len(words) > 1 else "",
         d["year"], d["mileage"], d["fuel"], d["gearbox"], d["body"], customs, d["color"],
         zero, price, price, t, token))
    con.execute("UPDATE ads SET photo_url=? WHERE token=?", (d.get("photo") or None, token))
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
            if price is not None and price != r["current_price"]:
                con.execute("UPDATE ads SET current_price=? WHERE token=?", (price, r["token"]))
                db.add_price(con, r["token"], t, price)
                changed += 1
                update_post(con, r["token"])
        con.commit()
    print(f"recheck: {len(rows)} checked, {changed} changed")


# ---------- channel ----------

def market(con, ad):
    rows = con.execute(
        "SELECT current_price FROM ads WHERE brand_model=? AND year=? AND zero_km=? "
        "AND status IN ('active','removed') AND duplicate_of IS NULL AND token<>?",
        (ad["brand_model"], ad["year"], ad["zero_km"], ad["token"])).fetchall()
    return stats.group_median([r[0] for r in rows])


def caption(con, ad):
    med, n = market(con, ad)
    price = ad["current_price"]
    lines = [f"🚗 <b>{escape_html(ad['title'])}</b>",
             f"🏷 {escape_html(ad['brand_model'])} | 📅 {to_fa_digits(ad['year'])}"
             + (" | 🆕 صفر" if ad["zero_km"] else ""),
             f"🛣 {fmt_int(ad['mileage'])} کیلومتر | ⛽ {escape_html(ad['fuel'] or '؟')}"
             f" | ⚙️ {escape_html(ad['gearbox'] or '؟')}",
             f"🛡 بدنه: {escape_html(ad['body'] or '؟')} | 📄 {escape_html(ad['customs'] or '؟')}",
             f"💰 قیمت اولیه: {fmt_price(ad['first_price'])}"]
    if price != ad["first_price"] and price and ad["first_price"]:
        pct = round((price - ad["first_price"]) * 100 / ad["first_price"])
        arrow = "🔻" if pct < 0 else "🔺"
        lines.append(f"💰 قیمت فعلی: {fmt_price(price)} ({arrow}{to_fa_digits(abs(pct))}٪)")
    if med:
        diff = round((price - med) * 100 / med) if price else None
        tail = f" ← {'+' if diff and diff > 0 else ''}{to_fa_digits(diff)}٪" if diff is not None else ""
        lines.append(f"📊 میانه بازار ({to_fa_digits(n)} آگهی): {fmt_price(int(med))}{tail}")
    else:
        lines.append("📊 میانه بازار: هنوز دادهٔ کافی نیست")
    tags = []
    if stats.is_below_market(price, med):
        tags.append("🔻 زیر قیمت بازار")
    if ad["duplicate_of"]:
        tags.append("🔁 احتمالاً تکراری")
    if tags:
        lines.append(" | ".join(tags))
    if ad["status"] == "removed":
        days = days_between(ad["first_seen"], ad["removed_at"])
        lines.append(f"❌ آگهی حذف شد — احتمالاً فروخته شد بعد از {to_fa_digits(days)} روز."
                     f" آخرین قیمت: {fmt_price(price)}")
    lines.append(f"📍 {escape_html(ad['city'] or 'گیلان')} | "
                 f"<a href=\"https://divar.ir/v/{ad['token']}\">مشاهده در دیوار</a>")
    return "\n".join(lines)[:1024]


def update_post(con, token):
    ad = con.execute("SELECT * FROM ads WHERE token=?", (token,)).fetchone()
    if not ad or not ad["message_id"] or config.DRY_RUN:
        return
    telegram.edit_ad(ad["message_id"], caption(con, ad), bool(ad["has_photo"]))


def post_new(con):
    rows = con.execute("SELECT * FROM ads WHERE status='active' AND post_eligible=1 AND "
                       "message_id IS NULL ORDER BY first_seen ASC LIMIT ?",
                       (config.POSTS_PER_RUN,)).fetchall()
    for ad in rows:
        cap = caption(con, ad)
        med, _ = market(con, ad)
        con.execute("UPDATE ads SET below_market=? WHERE token=?",
                    (1 if stats.is_below_market(ad["current_price"], med) else 0, ad["token"]))
        if config.DRY_RUN:
            print("---- would post ----\n" + cap)
            continue
        photo = ad["photo_url"] or ad["image_url"]
        mid = telegram.post_ad(cap, photo)
        if mid:
            con.execute("UPDATE ads SET message_id=?, has_photo=? WHERE token=?",
                        (mid, 1 if photo else 0, ad["token"]))
            interact.notify_watchers(con, ad, cap)
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
    if os.environ.get("FORCE_POST_ONE") == "1":  # manual test: post the newest ad now
        con.execute("UPDATE ads SET post_eligible=1 WHERE token=(SELECT token FROM ads WHERE "
                    "status='active' AND message_id IS NULL ORDER BY first_seen DESC LIMIT 1)")
        interact.process_updates(con)
        post_new(con)
        con.commit()
        con.close()
        return 0
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
    con.commit()
    post_new(con)
    report.maybe_post(con)
    interact.process_updates(con)
    con.commit()
    total = con.execute("SELECT status, COUNT(*) FROM ads GROUP BY status").fetchall()
    print("database:", {r[0]: r[1] for r in total})
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
