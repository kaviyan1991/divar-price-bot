# Feasibility test only: does Divar answer from GitHub's servers?
import json, time, random, urllib.request, urllib.error

SEARCH = "https://api.divar.ir/v8/postlist/w/search"
DETAIL = "https://api.divar.ir/v8/posts-v2/web/"
HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "origin": "https://divar.ir",
    "referer": "https://divar.ir/",
}
BRANDS = ["Toyota", "Kia", "Hyundai", "Nissan"]
GILAN = "889"


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=HEADERS, method="POST" if data else "GET")
    t = time.time()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode()), round(time.time() - t, 2)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace"), round(time.time() - t, 2)
    except Exception as e:
        return None, repr(e), round(time.time() - t, 2)


body = {
    "city_ids": [GILAN],
    "search_data": {
        "form_data": {"data": {
            "category": {"str": {"value": "light"}},
            "brand_model": {"repeated_string": {"value": BRANDS}},
        }},
        "server_payload": {
            "@type": "type.googleapis.com/widgets.SearchData.ServerPayload",
            "additional_form_data": {"data": {"sort": {"str": {"value": "sort_date"}}}},
        },
    },
}

status, result, took = req(SEARCH, body)
print("SEARCH status:", status, "| time:", took, "s")
if status != 200:
    print("BLOCKED or ERROR:", result)
    raise SystemExit(0)

rows = [w["data"] for w in result.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
print("Listings on first page:", len(rows))
for d in rows[:5]:
    print("-", d.get("title"), "|", d.get("middle_description_text"), "|", d.get("top_description_text"))

for d in rows[:3]:
    time.sleep(random.uniform(2, 4))
    token = d["action"]["payload"]["token"]
    status, detail, took = req(DETAIL + token)
    print("DETAIL", token, "status:", status, "| time:", took, "s")
    if status != 200:
        print("BLOCKED or ERROR:", detail)
        continue
    text = json.dumps(detail, ensure_ascii=False)
    for key in ["مدل (سال تولید)", "کارکرد", "برند و تیپ", "رنگ"]:
        i = text.find(key)
        print("   ", key, "->", text[i:i + 90] if i >= 0 else "NOT FOUND")

print("DONE")
