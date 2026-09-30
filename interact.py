"""Replies to people who message the bot: search, price estimate, chart, alerts."""
import datetime as dt
import io
import re

import db
import stats
import telegram
from textutil import escape_html, fa_num, fmt_int, fmt_price, normalize, to_en_digits

ALIASES = {
    "camry": "کمری", "corolla": "کرولا", "prado": "پرادو", "landcruiser": "لندکروز",
    "rav4": "راو", "hilux": "هایلوکس", "fortuner": "فورچونر", "sportage": "اسپورتیج",
    "sorento": "سورنتو", "cerato": "سراتو", "optima": "اپتیما", "rio": "ریو",
    "elantra": "النترا", "tucson": "توسان", "santafe": "سانتافه", "sonata": "سوناتا",
    "accent": "اکسنت", "azera": "آزرا", "patrol": "پاترول", "xtrail": "ایکستریل",
    "qashqai": "قشقایی", "civic": "سیویک", "accord": "آکورد", "vitara": "ویتارا",
    "outlander": "اوتلندر", "macan": "ماکان", "cayenne": "کاین", "panamera": "پانامرا",
    "toyota": "تویوتا", "nissan": "نیسان", "honda": "هوندا", "suzuki": "سوزوکی",
    "mitsubishi": "میتسوبیشی", "hyundai": "هیوندا", "kia": "کیا", "benz": "بنز",
    "mercedes": "بنز", "porsche": "پورشه", "audi": "آئودی", "maserati": "مازراتی",
    "landrover": "لندرور", "rangerover": "رنجرور",
}
STOP = {"مدل", "سال", "کارکرد", "کیلومتر", "هزار", "تخمین", "قیمت", "km"}

HELP = (
    "سلام! 👋 من قیمت ماشین‌های خارجی دیوار گیلان را زیر نظر دارم.\n\n"
    "🔎 <b>جستجو:</b> اسم ماشین و سال را بفرستید، مثلاً:\n"
    "<code>پرادو 2022</code> یا <code>کمری ۱۴۰۱</code> یا <code>sportage 2021</code>\n\n"
    "💡 <b>تخمین قیمت:</b> <code>تخمین پرادو 2022 40000</code>\n"
    "(مدل، سال و کارکرد به کیلومتر)\n\n"
    "📈 <b>نمودار روند قیمت:</b> <code>/chart پرادو 2022</code>\n\n"
    "🔔 <b>اعلان آگهی جدید:</b> <code>/watch پرادو 2022</code>\n"
    "لغو: <code>/unwatch پرادو 2022</code> | لیست: /watches\n\n"
    "⏱ جواب‌ها ممکن است تا ۱۵ دقیقه (شب‌ها تا یک ساعت) طول بکشد."
)


# ---------- query parsing & matching ----------

def parse_query(text):
    t = to_en_digits(text or "").lower()
    year, mileage = None, None
    for n in re.findall(r"\d+", t):
        v = int(n)
        if 1390 <= v <= 1410:
            year = v + 621
        elif 2000 <= v <= 2035 and year is None:
            year = v
        elif v >= 100:
            mileage = v
    words = []
    for w in re.split(r"\s+", re.sub(r"\d+", " ", t)):
        w = w.strip("،,.؟?!")
        if not w or w in STOP:
            continue
        words.append(normalize(ALIASES.get(w, w)))
    joined = "".join(words)
    if joined in ALIASES:  # e.g. "land cruiser"
        words = [normalize(ALIASES[joined])]
    return [w for w in words if w], year, mileage


def find_ads(con, words, year):
    if not words:
        return []
    sql = "SELECT * FROM ads WHERE status IN ('active','removed') AND brand_model IS NOT NULL"
    args = []
    if year:
        sql += " AND year=?"
        args.append(year)
    out = []
    for ad in con.execute(sql, args):
        hay = normalize((ad["brand_model"] or "") + " " + (ad["title"] or ""))
        if all(w in hay for w in words):
            out.append(ad)
    return out


def _pct(values, q):
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _days(a, b):
    try:
        fa = dt.datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        fb = dt.datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
        return max(0, (fb - fa).days)
    except (TypeError, ValueError):
        return None


# ---------- answers ----------

def search_text(con, query):
    words, year, _ = parse_query(query)
    ads = find_ads(con, words, year)
    if not ads:
        return (f"برای «{escape_html(query)}» آگهی‌ای پیدا نشد. 🤔\n"
                "اسم مدل را کوتاه‌تر بنویسید (مثلاً «پرادو» به‌جای «تویوتا پرادو آفرود»).")
    used = [a for a in ads if not a["zero_km"]]
    zero = [a for a in ads if a["zero_km"]]
    lines = [f"📊 <b>نتیجه برای «{escape_html(query)}»</b>"]
    for label, group in (("کارکرده", used), ("صفر کیلومتر", zero)):
        if not group:
            continue
        dups = {a["token"] for a in group if a["duplicate_of"]}
        clean = [a for a in group if a["token"] not in dups]
        active = [a for a in clean if a["status"] == "active"]
        removed = [a for a in clean if a["status"] == "removed"]
        cur = stats.valid_prices([a["current_price"] for a in active])
        first = stats.valid_prices([a["first_price"] for a in active])
        last = stats.valid_prices([a["current_price"] for a in removed])
        days = [d for d in (_days(a["first_seen"], a["removed_at"]) for a in removed) if d is not None]
        disc = [(a["current_price"] - a["first_price"]) * 100 / a["first_price"] for a in removed
                if a["first_price"] and a["current_price"]]
        lines.append(f"\n<b>{label}</b> — فعال: {fa_num(len(active))} | حذف‌شده: {fa_num(len(removed))}")
        if cur:
            lines.append(f"• میانه قیمت فعلی: {fmt_price(int(stats.median(cur)))}")
        if first:
            lines.append(f"• میانه قیمت اولیه: {fmt_price(int(stats.median(first)))}")
        if last:
            lines.append(f"• میانه آخرین قیمت آگهی‌های حذف‌شده: {fmt_price(int(stats.median(last)))}")
        if days:
            lines.append(f"• میانه ماندگاری روی سایت: {fa_num(int(stats.median(days)))} روز")
        if disc:
            lines.append(f"• میانه تغییر قیمت تا حذف: {fa_num(round(stats.median(disc)))}٪")
        if len(cur) < 5:
            lines.append("⚠️ تعداد نمونه کم است؛ آمار را با احتیاط نگاه کنید.")
    lines.append("\n<b>آخرین آگهی‌های فعال:</b>")
    active_all = sorted([a for a in ads if a["status"] == "active"],
                        key=lambda a: a["first_seen"] or "", reverse=True)[:10]
    for a in active_all:
        tags = []
        if a["below_market"]:
            tags.append("🔻زیر بازار")
        if a["duplicate_of"]:
            tags.append("🔁تکراری")
        if a["zero_km"]:
            tags.append("🆕صفر")
        price = fmt_price(a["current_price"])
        if a["first_price"] and a["current_price"] and a["first_price"] != a["current_price"]:
            price = f"{fmt_price(a['first_price'])} ← {price}"
        lines.append(f"• <a href=\"https://divar.ir/v/{a['token']}\">{escape_html(a['title'][:40])}</a>"
                     f" | {fa_num(a['year'])} | {fmt_int(a['mileage'])} کم | {price}"
                     + (" | " + " ".join(tags) if tags else ""))
    if not active_all:
        lines.append("فعلاً آگهی فعالی نیست.")
    return "\n".join(lines)


def estimate_text(con, query):
    words, year, mileage = parse_query(query)
    if not words or not year:
        return "برای تخمین، مدل و سال را بنویسید. مثال: <code>تخمین پرادو 2022 40000</code>"
    ads = [a for a in find_ads(con, words, year) if not a["zero_km"] and not a["duplicate_of"]]
    pool = ads
    note = ""
    if mileage:
        near = [a for a in ads if a["mileage"] and abs(a["mileage"] - mileage) <= 0.4 * mileage]
        if len(stats.valid_prices([a["current_price"] for a in near])) >= 5:
            pool, note = near, f" با کارکرد نزدیک به {fmt_int(mileage)} کیلومتر"
        else:
            note = " (نمونهٔ کافی با کارکرد مشابه نبود؛ همهٔ کارکردها حساب شد)"
    prices = stats.valid_prices([a["current_price"] for a in pool])
    if len(prices) < 5:
        return (f"برای «{escape_html(query)}» هنوز دادهٔ کافی ندارم "
                f"({fa_num(len(prices))} آگهی؛ حداقل ۵ لازم است).")
    lo, mid, hi = _pct(prices, 0.25), stats.median(prices), _pct(prices, 0.75)
    return (f"💡 <b>تخمین قیمت منصفانه</b>\n{escape_html(query)}{note}\n\n"
            f"بازهٔ معقول: {fmt_price(int(lo))} تا {fmt_price(int(hi))}\n"
            f"میانه: {fmt_price(int(mid))}\n"
            f"بر اساس {fa_num(len(prices))} آگهی واقعی دیوار گیلان.\n"
            "⚠️ این تخمین فقط از روی آگهی‌هاست؛ وضعیت فنی، رنگ و پلاک قیمت را جابه‌جا می‌کند.")


def weekly_series(con, tokens):
    """Median price per week (Monday) for the given ads, carrying prices forward."""
    if not tokens:
        return []
    marks = ",".join("?" * len(tokens))
    hist = {}
    for r in con.execute(f"SELECT token, seen_at, price FROM price_history WHERE token IN ({marks}) "
                         "ORDER BY seen_at", tokens):
        if r["price"]:
            hist.setdefault(r["token"], []).append((r["seen_at"], r["price"]))
    life = {r["token"]: (r["first_seen"], r["removed_at"]) for r in
            con.execute(f"SELECT token, first_seen, removed_at FROM ads WHERE token IN ({marks})", tokens)}
    if not hist:
        return []
    start = min(v[0][0] for v in hist.values())[:10]
    week = dt.date.fromisoformat(start)
    week -= dt.timedelta(days=week.weekday())
    today = dt.date.today()
    out = []
    while week <= today:
        end = (week + dt.timedelta(days=7)).isoformat()
        prices = []
        for tok, pts in hist.items():
            first, removed = life.get(tok, (None, None))
            if first and first[:10] >= end:
                continue
            if removed and removed[:10] < week.isoformat():
                continue
            p = [price for when, price in pts if when[:10] < end]
            if p:
                prices.append(p[-1])
        med, n = stats.group_median(prices)
        if med:
            out.append((week, med, n))
        week += dt.timedelta(days=7)
    return out


def chart_png(series, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    xs = [w for w, _, _ in series]
    ys = [m / 1e9 for _, m, _ in series]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=130)
    ax.plot(xs, ys, marker="o", linewidth=2)
    ax.set_ylabel("Median price (billion toman)")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def send_chart(con, chat_id, query):
    words, year, _ = parse_query(query)
    ads = [a for a in find_ads(con, words, year) if not a["zero_km"] and not a["duplicate_of"]]
    series = weekly_series(con, [a["token"] for a in ads])
    if len(series) < 2:
        telegram.send_message(chat_id, f"📈 برای «{escape_html(query)}» هنوز دادهٔ کافی برای نمودار نیست "
                                       "(حداقل دو هفته با ۵ آگهی لازم است). چند روز دیگر دوباره امتحان کنید.")
        return
    title = " ".join(words + ([str(year)] if year else []))
    try:
        png = chart_png(series, "Price trend")
    except Exception as e:  # matplotlib missing or failed: send a text table instead
        print("chart failed:", e)
        rows = [f"{fa_num(w.isoformat())}: {fmt_price(int(m))} ({fa_num(n)} آگهی)" for w, m, n in series]
        telegram.send_message(chat_id, f"📈 روند قیمت «{escape_html(query)}»\n" + "\n".join(rows))
        return
    telegram.send_photo_bytes(chat_id, png, f"📈 روند میانهٔ قیمت هفتگی «{escape_html(query)}» "
                                            f"({escape_html(title)})")


# ---------- alerts ----------

def watch_matches(query, ad):
    words, year, _ = parse_query(query)
    if not words or (year and ad["year"] != year):
        return False
    hay = normalize((ad["brand_model"] or "") + " " + (ad["title"] or ""))
    return all(w in hay for w in words)


def notify_watchers(con, ad, caption):
    for w in con.execute("SELECT chat_id, query FROM watches").fetchall():
        if watch_matches(w["query"], ad):
            telegram.send_message(w["chat_id"], f"🔔 آگهی جدید برای «{escape_html(w['query'])}»:\n\n{caption}")


# ---------- dispatcher ----------

def _cb_query(q):
    """callback_data is limited to 64 bytes."""
    b = q.encode()[:52]
    return b.decode("utf-8", "ignore")


def handle_text(con, chat_id, text):
    t = (text or "").strip()
    low = t.lower()
    if low in ("/start", "/help", "راهنما"):
        telegram.send_message(chat_id, HELP)
    elif low.startswith("/watches"):
        rows = con.execute("SELECT query FROM watches WHERE chat_id=?", (chat_id,)).fetchall()
        msg = "\n".join("• " + escape_html(r["query"]) for r in rows) or "هیچ اعلانی ثبت نکرده‌اید."
        telegram.send_message(chat_id, "🔔 اعلان‌های شما:\n" + msg)
    elif low.startswith("/watch"):
        q = t[len("/watch"):].strip()
        if not parse_query(q)[0]:
            telegram.send_message(chat_id, "مثال: <code>/watch پرادو 2022</code>")
            return
        con.execute("INSERT OR IGNORE INTO watches(chat_id,query,created_at) VALUES(?,?,?)",
                    (chat_id, q, dt.datetime.utcnow().isoformat()))
        telegram.send_message(chat_id, f"✅ از این به بعد آگهی‌های جدید «{escape_html(q)}» را برایتان می‌فرستم.")
    elif low.startswith("/unwatch"):
        q = t[len("/unwatch"):].strip()
        con.execute("DELETE FROM watches WHERE chat_id=? AND query=?", (chat_id, q))
        telegram.send_message(chat_id, f"❎ اعلان «{escape_html(q)}» لغو شد.")
    elif low.startswith("/chart"):
        send_chart(con, chat_id, t[len("/chart"):].strip())
    elif low.startswith("/estimate") or t.startswith("تخمین"):
        q = re.sub(r"^(/estimate|تخمین)", "", t).strip()
        telegram.send_message(chat_id, estimate_text(con, q))
    elif t.startswith("/"):
        telegram.send_message(chat_id, HELP)
    else:
        q = _cb_query(t)
        telegram.send_message(chat_id, search_text(con, t), keyboard=[[
            {"text": "📈 نمودار", "callback_data": "c|" + q},
            {"text": "🔔 اعلان آگهی جدید", "callback_data": "w|" + q}]])


def process_updates(con):
    offset = int(db.get_state(con, "tg_offset", "0") or 0)
    updates = telegram.get_updates(offset)
    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        try:
            if "message" in u and u["message"].get("chat", {}).get("type") == "private":
                handle_text(con, u["message"]["chat"]["id"], u["message"].get("text", ""))
            elif "callback_query" in u:
                cq = u["callback_query"]
                telegram.answer_callback(cq["id"])
                chat_id = cq["message"]["chat"]["id"]
                kind, _, q = (cq.get("data") or "").partition("|")
                handle_text(con, chat_id, ("/chart " if kind == "c" else "/watch ") + q)
        except Exception as e:  # one bad message must not stop the bot
            print("update failed:", repr(e))
        db.set_state(con, "tg_offset", offset)
        con.commit()
    if updates:
        print(f"telegram: {len(updates)} update(s) handled")
