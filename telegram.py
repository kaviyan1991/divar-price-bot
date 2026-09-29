"""Minimal Telegram Bot API client (no external libraries)."""
import json
import time
import urllib.error
import urllib.request

import config


def _call(method, params):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode() or "{}")
            retry = (body.get("parameters") or {}).get("retry_after")
            if e.code == 429 and retry:
                time.sleep(int(retry) + 1)
                continue
            return body
        except Exception as e:  # network hiccup
            if attempt == 2:
                return {"ok": False, "description": repr(e)}
            time.sleep(3)
    return {"ok": False}


def post_ad(caption, image_url):
    """Posts with photo; falls back to text. Returns message_id or None."""
    if image_url:
        r = _call("sendPhoto", {"chat_id": config.CHANNEL_ID, "photo": image_url,
                                "caption": caption, "parse_mode": "HTML"})
        if r.get("ok"):
            return r["result"]["message_id"]
        print("sendPhoto failed:", r.get("description"))
    r = _call("sendMessage", {"chat_id": config.CHANNEL_ID, "text": caption,
                              "parse_mode": "HTML", "disable_web_page_preview": True})
    if r.get("ok"):
        return r["result"]["message_id"]
    print("sendMessage failed:", r.get("description"))
    return None


def edit_ad(message_id, caption, has_photo):
    method = "editMessageCaption" if has_photo else "editMessageText"
    key = "caption" if has_photo else "text"
    r = _call(method, {"chat_id": config.CHANNEL_ID, "message_id": message_id,
                       key: caption, "parse_mode": "HTML"})
    if not r.get("ok") and "not modified" not in (r.get("description") or ""):
        print("edit failed:", r.get("description"))
    return bool(r.get("ok"))
