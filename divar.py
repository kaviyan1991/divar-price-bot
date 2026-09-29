"""Talks to Divar's web API (the same one divar.ir uses) and parses ads."""
import json
import random
import time
import urllib.error
import urllib.request

import config
from textutil import parse_int, parse_price, parse_year

SEARCH_URL = "https://api.divar.ir/v8/postlist/w/search"
DETAIL_URL = "https://api.divar.ir/v8/posts-v2/web/"
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
    "origin": "https://divar.ir",
    "referer": "https://divar.ir/",
}


class Blocked(Exception):
    """Divar answered 403/429: stop crawling for now."""


def _pause():
    time.sleep(random.uniform(config.SLEEP_MIN, config.SLEEP_MAX))


def _request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Blocked(f"HTTP {e.code}")
        return e.code, None


def search_page(pagination_data=None):
    """One page of newest ads for our brands. Returns (rows, next_pagination_or_None)."""
    body = {
        "city_ids": config.CITY_IDS,
        "search_data": {
            "form_data": {"data": {
                "category": {"str": {"value": config.CATEGORY}},
                "brand_model": {"repeated_string": {"value": config.BRANDS}},
                "production-year": {"number_range": {"minimum": config.MIN_JALALI_YEAR_FILTER}},
            }},
            "server_payload": {
                "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
                "additional_form_data": {"data": {"sort": {"str": {"value": "sort_date"}}}},
            },
        },
    }
    if pagination_data:
        body["pagination_data"] = pagination_data
    _pause()
    status, j = _request(SEARCH_URL, body)
    if status != 200 or not j:
        raise RuntimeError(f"search failed: HTTP {status}")
    rows = [parse_row(w["data"]) for w in j.get("list_widgets", [])
            if w.get("widget_type") == "POST_ROW"]
    pag = j.get("pagination") or {}
    nxt = pag.get("data") if pag.get("has_next_page") else None
    return [r for r in rows if r], nxt


def parse_row(d):
    try:
        payload = d["action"]["payload"]
        token = payload["token"]
    except (KeyError, TypeError):
        return None
    return {
        "token": token,
        "title": d.get("title", ""),
        "list_price": parse_price(d.get("middle_description_text")),
        "list_mileage": parse_int(d.get("top_description_text")),
        "image_url": d.get("image_url") or "",
        "image_count": d.get("image_count") or 0,
        "city": (payload.get("web_info") or {}).get("city_persian", ""),
    }


def fetch_detail(token):
    """Returns (status, parsed_dict_or_None). 404 means the ad is gone."""
    _pause()
    status, j = _request(DETAIL_URL + token)
    if status != 200 or not j:
        return status, None
    return 200, parse_detail(j)


def _walk(o, pairs, scores, texts):
    if isinstance(o, list):
        for x in o:
            _walk(x, pairs, scores, texts)
    elif isinstance(o, dict):
        t = o.get("title")
        if isinstance(t, str):
            if isinstance(o.get("value"), str):
                pairs.setdefault(t, o["value"])
            if isinstance(o.get("descriptive_score"), str):
                scores.setdefault(t, o["descriptive_score"])
        for v in o.values():
            _walk(v, pairs, scores, texts)


def parse_detail(j):
    pairs, scores, texts = {}, {}, []
    _walk(j.get("sections", []), pairs, scores, texts)
    desc = ""
    for s in j.get("sections", []):
        if s.get("section_name") == "DESCRIPTION":
            for w in s.get("widgets", []):
                if w.get("widget_type") == "DESCRIPTION_ROW":
                    desc = (w.get("data") or {}).get("text", "")
    brand_model = pairs.get("برند و مدل", "")
    mileage = parse_int(pairs.get("کارکرد"))
    return {
        "brand_model": brand_model.strip(),
        "year": parse_year(pairs.get("مدل (سال تولید)")),
        "mileage": mileage,
        "color": pairs.get("رنگ", ""),
        "gearbox": pairs.get("گیربکس", ""),
        "fuel": pairs.get("نوع سوخت", ""),
        "price": parse_price(pairs.get("قیمت پایه") or pairs.get("قیمت")),
        "body": scores.get("بدنه", ""),
        "description": desc,
    }


def customs_status(text):
    t = text or ""
    if any(k in t for k in ("منطقه آزاد", "منطقه ازاد", "گذر موقت", "پلاک موقت", "پلاک منطقه")):
        return "منطقه آزاد / گذر موقت"
    if "پلاک ملی" in t:
        return "پلاک ملی"
    return "نامشخص"
