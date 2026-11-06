"""Owner-only tools: private health report, error alerts and control commands.
Works only when the ADMIN_ID secret (the owner's Telegram user id) is set."""
import datetime as dt

import config
import db
import telegram
from textutil import fa_num

TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

HELP = ("🛠 <b>پنل مدیریت</b>\n"
        "/stats — آمار لحظه‌ای ربات\n"
        "/users — تعداد کاربران و اعلان‌ها\n"
        "/pause — توقف ارسال آگهی به کانال\n"
        "/resume — ادامهٔ ارسال آگهی\n"
        "/testpost — ارسال یک پست تست در اجرای فعلی")


def is_admin(chat_id):
    return bool(config.ADMIN_ID) and str(chat_id) == config.ADMIN_ID


def notify(text):
    if config.ADMIN_ID and not config.DRY_RUN:
        telegram.send_message(config.ADMIN_ID, text)


def today():
    return dt.datetime.now(TEHRAN).date().isoformat()


def count_run(con):
    key = "runs_" + today()
    db.set_state(con, key, int(db.get_state(con, key, "0") or 0) + 1)


def add_counter(con, name, n=1):
    key = f"{name}_{today()}"
    db.set_state(con, key, int(db.get_state(con, key, "0") or 0) + n)


def stats_text(con, d=None):
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    d = d or today()
    get = lambda k: fa_num(db.get_state(con, f"{k}_{d}", "0") or "0")
    paused = db.get_state(con, "paused") == "1"
    return "\n".join([
        "📊 <b>وضعیت ربات</b>",
        f"وضعیت ارسال به کانال: {'⏸ متوقف' if paused else '▶️ فعال'}",
        f"اجراهای امروز: {get('runs')}",
        f"آگهی جدید امروز: {get('new')} | پست امروز: {get('posts')}",
        f"خطاهای امروز: {get('errors')} | مسدودی دیوار امروز: {get('blocks')}",
        "",
        f"آگهی فعال: {fa_num(q('SELECT COUNT(*) FROM ads WHERE status=?', 'active'))}",
        f"حذف‌شده (فروخته‌شده): {fa_num(q('SELECT COUNT(*) FROM ads WHERE status=?', 'removed'))}",
        f"در صف بررسی: {fa_num(q('SELECT COUNT(*) FROM ads WHERE status=?', 'pending'))}",
        f"پست‌های فعلی کانال: {fa_num(q('SELECT COUNT(*) FROM ads WHERE message_id IS NOT NULL'))}",
    ])


def users_text(con):
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join([
        "👥 <b>کاربران ربات</b>",
        f"کل کاربران: {fa_num(q('SELECT COUNT(*) FROM users'))}",
        f"فعال در ۷ روز اخیر: {fa_num(q('SELECT COUNT(*) FROM users WHERE last_seen>=?', week))}",
        f"اعلان‌های فعال: {fa_num(q('SELECT COUNT(*) FROM watches'))}",
    ])


def handle(con, chat_id, text):
    """Returns True when the message was an admin command (and handled)."""
    if not is_admin(chat_id):
        return False
    cmd = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    if cmd == "/admin":
        telegram.send_message(chat_id, HELP)
    elif cmd == "/stats":
        telegram.send_message(chat_id, stats_text(con))
    elif cmd == "/users":
        telegram.send_message(chat_id, users_text(con))
    elif cmd == "/pause":
        db.set_state(con, "paused", "1")
        telegram.send_message(chat_id, "⏸ ارسال آگهی به کانال متوقف شد. جمع‌آوری و آمار ادامه دارد.")
    elif cmd == "/resume":
        db.set_state(con, "paused", "0")
        telegram.send_message(chat_id, "▶️ ارسال آگهی به کانال دوباره فعال شد.")
    elif cmd == "/testpost":
        db.set_state(con, "force_one", "1")
        telegram.send_message(chat_id, "🧪 در همین اجرا یک آگهی قیمت‌دار به کانال فرستاده می‌شود.")
    else:
        return False
    con.commit()
    return True


def maybe_daily_health(con):
    """Private health report after 23:00 Tehran (or before 02:00 for yesterday)."""
    if not config.ADMIN_ID:
        return
    t = dt.datetime.now(TEHRAN)
    if t.hour >= 23:
        day = t.date()
    elif t.hour < 2:
        day = t.date() - dt.timedelta(days=1)
    else:
        return
    if db.get_state(con, "last_health") == day.isoformat():
        return
    runs = int(db.get_state(con, f"runs_{day.isoformat()}", "0") or 0)
    warn = "\n⚠️ تعداد اجراها کم است؛ cron-job.org را بررسی کنید." if runs < 60 else ""
    notify("🩺 <b>گزارش سلامت روزانه</b>\n" + stats_text(con, day.isoformat()) + warn)
    db.set_state(con, "last_health", day.isoformat())
    con.commit()
