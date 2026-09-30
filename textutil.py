"""Small text helpers: digits, prices, formatting, hashtags."""
import re

_FA = "۰۱۲۳۴۵۶۷۸۹"
_AR = "٠١٢٣٤٥٦٧٨٩"
_TO_EN = {ord(c): str(i) for i, c in enumerate(_FA)}
_TO_EN.update({ord(c): str(i) for i, c in enumerate(_AR)})
_TO_FA = {ord(str(i)): c for i, c in enumerate(_FA)}


def to_en_digits(s):
    return (s or "").translate(_TO_EN)


def to_fa_digits(s):
    return str(s).translate(_TO_FA)


def parse_int(s):
    """'۱۶۰,۰۰۰ کیلومتر' -> 160000 ; returns None if no digits."""
    t = re.sub(r"[^\d]", "", to_en_digits(s))
    return int(t) if t else None


def parse_price(s):
    """Returns toman as int, or None for negotiable/missing/garbage."""
    if not s or "توافق" in s:
        return None
    return parse_int(s)


def jalali_to_gregorian_year(y):
    """Approximate: 1399 -> 2020 (Divar pairs them this way)."""
    return y + 621 if 1300 <= y <= 1500 else y


def parse_year(s):
    y = parse_int(s)
    if y is None:
        return None
    return jalali_to_gregorian_year(y)


def fa_num(s):
    """Latin digits/separators -> Persian digits with Persian separators."""
    return to_fa_digits(str(s).replace(",", "\u066c").replace(".", "\u066b"))


def fmt_price(p):
    if p is None:
        return "توافقی"
    if p >= 1_000_000_000:
        v = f"{p / 1_000_000_000:.2f}".rstrip("0").rstrip(".")
        return fa_num(v) + " میلیارد تومان"
    v = f"{p / 1_000_000:,.0f}"
    return fa_num(v) + " میلیون تومان"


def fmt_int(n):
    return fa_num(f"{n:,}") if n is not None else "؟"


def normalize(s):
    """For matching: unify Arabic/Persian letters, digits, drop spaces and ZWNJ."""
    t = to_en_digits(s or "").lower()
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").replace("آ", "ا")
    return re.sub(r"[\s\-_]+", "", t)


def hashtag(s):
    t = re.sub(r"[^\w]+", "_", (s or "").strip()).strip("_")
    return "#" + t if t else ""


def escape_html(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
