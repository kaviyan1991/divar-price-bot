import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DRY_RUN"] = "1"
import stats, divar, db, main, interact, report
from textutil import parse_int, parse_price, parse_year, fmt_price, fmt_int, normalize


class TextTests(unittest.TestCase):
    def test_digits_and_price(self):
        self.assertEqual(parse_int("۱۶۰,۰۰۰ کیلومتر"), 160000)
        self.assertEqual(parse_price("‏۱۹,۲۰۰,۰۰۰,۰۰۰ تومان"), 19200000000)
        self.assertIsNone(parse_price("توافقی"))
        self.assertIsNone(parse_price(""))

    def test_year(self):
        self.assertEqual(parse_year("۲۰۲۱"), 2021)
        self.assertEqual(parse_year("۱۳۹۹"), 2020)
        self.assertEqual(parse_year("۱۴۰۴/۲۰۲۵"), 2025)
        self.assertEqual(parse_year("۱۴۰۴ - 2025"), 2025)

    def test_format(self):
        self.assertEqual(fmt_price(19200000000), "۱۹٫۲ میلیارد تومان")
        self.assertEqual(fmt_price(850000000), "۸۵۰ میلیون تومان")
        self.assertEqual(fmt_int(40000), "۴۰٬۰۰۰")
        self.assertEqual(normalize("هيوندای ‌ النترا"), normalize("هیوندای النترا"))


class StatsTests(unittest.TestCase):
    def test_median_and_outliers(self):
        prices = [5_000_000_000, 5_200_000_000, 5_100_000_000, 4_900_000_000,
                  5_300_000_000, 10_000, 30_000_000_000]
        med, n = stats.group_median(prices)
        self.assertEqual(n, 5)
        self.assertEqual(med, 5_100_000_000)

    def test_not_enough(self):
        self.assertEqual(stats.group_median([5e9, 5.1e9])[0], None)

    def test_below_market(self):
        self.assertTrue(stats.is_below_market(4_200_000_000, 5_000_000_000))
        self.assertFalse(stats.is_below_market(4_500_000_000, 5_000_000_000))

    def test_duplicate(self):
        a = dict(brand_model="تویوتا کمری", year=2022, color="سفید", mileage=50000, price=5e9, zero_km=0)
        b = dict(a, mileage=50500, price=5.1e9)
        self.assertTrue(stats.is_duplicate(a, b))
        self.assertFalse(stats.is_duplicate(a, dict(b, color="مشکی")))
        self.assertFalse(stats.is_duplicate(dict(a, zero_km=1), b))


SAMPLE = {"sections": [
    {"section_name": "IMAGE", "widgets": [
        {"widget_type": "IMAGE_CAROUSEL", "data": {"items": [{"image": {"url": "https://img/big.webp"}}]}}]},
    {"section_name": "DESCRIPTION", "widgets": [
        {"widget_type": "DESCRIPTION_ROW", "data": {"text": "پلاک موقت منطقه آزاد انزلی"}}]},
    {"section_name": "LIST_DATA", "widgets": [
        {"widget_type": "GROUP_INFO_ROW", "data": {"items": [
            {"title": "کارکرد", "value": "۱۶۰۰۰۰"},
            {"title": "مدل (سال تولید)", "value": "۲۰۲۱"},
            {"title": "رنگ", "value": "مشکی"}]}},
        {"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "برند و مدل", "value": "تویوتا لندکروزر ۴ در 4000cc"}},
        {"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "گیربکس", "value": "اتوماتیک"}},
        {"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "نوع سوخت", "value": "بنزین"}},
        {"widget_type": "UNEXPANDABLE_ROW", "data": {"title": "قیمت پایه", "value": "‏۱۹,۲۰۰,۰۰۰,۰۰۰ تومان"}},
        {"widget_type": "SCORE_ROW", "data": {"title": "بدنه", "descriptive_score": "سالم و بی‌خط و خش"}}]}]}


class DetailTests(unittest.TestCase):
    def test_parse_detail(self):
        d = divar.parse_detail(SAMPLE)
        self.assertEqual(d["year"], 2021)
        self.assertEqual(d["mileage"], 160000)
        self.assertEqual(d["price"], 19200000000)
        self.assertEqual(d["brand_model"], "تویوتا لندکروزر ۴ در 4000cc")
        self.assertEqual(d["body"], "سالم و بی‌خط و خش")
        self.assertEqual(divar.customs_status(d["description"]), "پلاک منطقه آزاد")
        self.assertEqual(divar.customs_status("فول کم کارکرد"), "پلاک منطقه آزاد")
        self.assertEqual(divar.customs_status("پلاک ملی رشت"), "پلاک ملی")

    def test_pipeline_in_memory(self):
        con = db.connect(":memory:")
        con.execute("INSERT INTO ads(token,title,list_price,image_url,image_count,city,status,"
                    "first_seen,last_seen,post_eligible) VALUES('t1','لندکروز',NULL,'x',3,'رشت',"
                    "'pending','2026-09-30T00:00:00Z','2026-09-30T00:00:00Z',1)")
        main.apply_detail(con, "t1", divar.parse_detail(SAMPLE))
        ad = con.execute("SELECT * FROM ads WHERE token='t1'").fetchone()
        self.assertEqual(ad["status"], "active")
        self.assertEqual(ad["brand"], "تویوتا")
        cap = main.caption(con, ad)
        self.assertNotIn("#", cap)
        self.assertIn("📅 سال ساخت: ۲۰۲۱ (۱۴۰۰)", cap)
        self.assertNotIn(" | ", cap)
        self.assertEqual(main.ad_url(ad), "https://divar.ir/v/t1")
        self.assertEqual(ad["photo_url"], "https://img/big.webp")

    def test_crawl_stores_rows(self):
        con = db.connect(":memory:")
        rows = [{"token": "a1", "title": "کمری", "list_price": 5e9, "list_mileage": 1000,
                 "image_url": "x", "image_count": 2, "city": "رشت"},
                {"token": "a2", "title": "بی عکس", "list_price": 5e9, "list_mileage": 0,
                 "image_url": "", "image_count": 0, "city": "رشت"}]
        old = divar.search_page
        divar.search_page = lambda pag=None: (rows, None)
        try:
            main.crawl_list(con, backfill=True)
        finally:
            divar.search_page = old
        self.assertEqual([r[0] for r in con.execute("SELECT token FROM ads")], ["a1"])
        self.assertEqual(db.get_state(con, "backfill_done"), "1")

    def test_old_year_skipped(self):
        con = db.connect(":memory:")
        con.execute("INSERT INTO ads(token,status,first_seen) VALUES('t2','pending','x')")
        d = divar.parse_detail(SAMPLE); d["year"] = 2017
        main.apply_detail(con, "t2", d)
        self.assertEqual(con.execute("SELECT status FROM ads").fetchone()[0], "skipped")


SPECS = dict(customs="پلاک ملی", fuel="بنزین", gearbox="اتوماتیک", color="سفید",
             body="سالم و بی‌خط و خش", engine="سالم", chassis="سالم و پلمپ", gearbox_cond="سالم و پلمپ")


def _add(con, token, **kw):
    row = dict(token=token, title="پرادو فول", brand_model="تویوتا پرادو ۴ در", year=2022,
               mileage=40000, zero_km=0, status="active", first_price=5_000_000_000,
               current_price=5_000_000_000, first_seen="2026-09-01T00:00:00Z", post_eligible=1)
    row.update(SPECS)
    row.update(kw)
    cols = ",".join(row)
    con.execute(f"INSERT INTO ads({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
    con.execute("INSERT INTO price_history VALUES(?,?,?)", (token, row["first_seen"], row["current_price"]))


def _fill(con, n=6, base=5_000_000_000):
    for i in range(n):
        _add(con, f"p{i}", mileage=40000 + i * 1000, first_price=base + i * 100_000_000,
             current_price=base + i * 100_000_000)


class BotTests(unittest.TestCase):
    def test_parse_query(self):
        self.assertEqual(interact.parse_query("پرادو ۱۴۰۱ ۴۰۰۰۰"), (["پرادو"], 2022, 40000))
        self.assertEqual(interact.parse_query("Prado 2022")[0], ["پرادو"])

    def test_search_and_estimate(self):
        con = db.connect(":memory:")
        _fill(con)
        txt = interact.search_text(con, "پرادو 2022")
        self.assertIn("میانگین قیمت فعلی", txt)
        self.assertIn("divar.ir/v/p0", txt)
        est = interact.estimate_text(con, "تخمین پرادو 2022 41000 سفید")
        self.assertIn("حدود", est)
        self.assertIn("پیدا نکردم", interact.estimate_text(con, "تخمین پرادو 2022 41000 مشکی"))
        self.assertNotIn("کارکرد نزدیک", txt)

    def test_watch_match(self):
        con = db.connect(":memory:")
        _fill(con, 1)
        ad = con.execute("SELECT * FROM ads").fetchone()
        self.assertTrue(interact.watch_matches("پرادو 2022", ad))
        self.assertFalse(interact.watch_matches("پرادو 2021", ad))

    def test_weekly_series_and_report(self):
        con = db.connect(":memory:")
        _fill(con)
        self.assertTrue(len(interact.weekly_series(con, [f"p{i}" for i in range(6)])) >= 1)
        import datetime as dt
        txt = report.build(con, dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc))
        self.assertIn("گزارش هفتگی", txt)


class ExactMatchTests(unittest.TestCase):
    """Comparisons must only use cars identical in every spec."""
    def setUp(self):
        import analytics
        self.an = analytics
        self.con = db.connect(":memory:")
        for i in range(4):
            _add(self.con, f"w{i}", mileage=40000 + i * 2000, current_price=5_000_000_000 + i * 50_000_000)
        _add(self.con, "black", color="مشکی", current_price=9_000_000_000)
        _add(self.con, "free", customs="پلاک منطقه آزاد", current_price=4_000_000_000)
        _add(self.con, "painted", body="رنگ‌شدگی در ۱ ناحیه", current_price=4_200_000_000)
        _add(self.con, "far_km", mileage=150000, current_price=3_000_000_000)
        _add(self.con, "unknown", color="")

    def test_only_identical_cars_are_compared(self):
        ad = self.con.execute("SELECT * FROM ads WHERE token='w0'").fetchone()
        tokens = {r["token"] for r in self.an.similar(self.con, ad)}
        # body condition and mileage are not compared any more
        self.assertEqual(tokens, {"w1", "w2", "w3", "painted", "far_km"})
        med, n = self.an.market(self.con, ad)
        self.assertEqual(n, 5)

    def test_unknown_spec_is_never_compared(self):
        ad = self.con.execute("SELECT * FROM ads WHERE token='unknown'").fetchone()
        self.assertEqual(self.an.market(self.con, ad), (None, 0))
        self.assertIn("نامشخص: رنگ", self.an.market_status(self.con, ad))
        w0 = self.con.execute("SELECT * FROM ads WHERE token='w0'").fetchone()
        self.assertNotIn("unknown", {r["token"] for r in self.an.similar(self.con, w0)})

    def test_too_few_identical(self):
        ad = self.con.execute("SELECT * FROM ads WHERE token='black'").fetchone()
        self.assertIsNone(self.an.market(self.con, ad)[0])
        self.assertIn("کمتر از", self.an.market_status(self.con, ad))

    def test_caption_uses_exact_median(self):
        ad = self.con.execute("SELECT * FROM ads WHERE token='w0'").fetchone()
        cap = main.caption(self.con, ad)
        self.assertIn("آگهی کاملاً مشابه", cap)

    def test_factor_analyses_vary_one_thing_only(self):
        w0 = self.con.execute("SELECT * FROM ads WHERE token='w0'").fetchone()
        mf, nf, mn, nn = self.an.plate_gap(self.con, w0)
        self.assertEqual(nf, 1)   # only 'free' differs by plate; 'unknown' is excluded
        self.assertEqual(nn, 6)
        pct, n = self.an.mileage_effect(self.con, w0)
        self.assertEqual(n, 6)    # every white national-plate 2022 Prado, any mileage/condition
        self.assertLess(pct, 0)
        self.assertIsNotNone(self.an.fair_price(self.con, w0))
        self.assertEqual(self.an.seller_type("فروش در نمایشگاه اتو پارس"), "نمایشگاه")

    def test_sale_speed_and_search(self):
        for j, days in enumerate((3, 5, 6)):
            _add(self.con, f"r{j}", status="removed", removed_at=f"2026-09-{1 + days:02d}T00:00:00Z")
        w0 = self.con.execute("SELECT * FROM ads WHERE token='w0'").fetchone()
        self.assertEqual(self.an.sale_speed(self.con, w0)[2], "🔥 داغ")
        txt = interact.search_text(self.con, "پرادو 2022")
        self.assertIn("تحلیل بازار گیلان", txt)
        self.assertIn("در هیچ مقایسه‌ای حساب نشدند", txt)
        self.assertIn("سرعت فروش این مدل: داغ", main.caption(self.con, w0))


class NoPriceTests(unittest.TestCase):
    def test_only_priced_ads_are_posted(self):
        con = db.connect(":memory:")
        _add(con, "priced")
        _add(con, "negotiable", current_price=None, first_price=None)
        _add(con, "zero", current_price=0, first_price=0)
        self.assertEqual([r["token"] for r in main.postable(con)], ["priced"])
        con.execute("UPDATE ads SET current_price=4800000000 WHERE token='negotiable'")
        self.assertEqual({r["token"] for r in main.postable(con)}, {"priced", "negotiable"})


class DailyTests(unittest.TestCase):
    def test_jalali(self):
        self.assertEqual(report.g2j(2026, 10, 1), (1405, 7, 9))
        self.assertEqual(report.g2j(2026, 3, 21), (1405, 1, 1))

    def test_daily_summary(self):
        import datetime as dt
        con = db.connect(":memory:")
        for i in range(4):
            _add(con, f"d{i}", first_seen="2026-10-01T08:00:00Z", mileage=40000 + i * 1000)
        _add(con, "cheap", first_seen="2026-10-01T09:00:00Z", current_price=4_000_000_000,
             first_price=4_000_000_000)
        con.execute("INSERT INTO price_history VALUES('d3','2026-10-01T10:00:00Z',4500000000)")
        txt = report.build_daily(con, dt.date(2026, 10, 1))
        self.assertIn("۹ مهر", txt)
        self.assertIn("زیر قیمت بازار امروز", txt)
        self.assertIn("بیشترین کاهش قیمت امروز", txt)


if __name__ == "__main__":
    unittest.main()
