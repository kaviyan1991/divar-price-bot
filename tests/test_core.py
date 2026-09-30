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
        self.assertEqual(divar.customs_status(d["description"]), "منطقه آزاد / گذر موقت")

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
        self.assertIn("divar.ir/v/t1", cap)
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


def _fill(con, n=6, base=5_000_000_000):
    for i in range(n):
        con.execute(
            "INSERT INTO ads(token,title,brand_model,year,mileage,zero_km,status,first_price,"
            "current_price,first_seen) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"p{i}", "پرادو فول", "تویوتا پرادو ۴ در", 2022, 40000 + i * 1000, 0, "active",
             base + i * 100_000_000, base + i * 100_000_000, "2026-09-01T00:00:00Z"))
        con.execute("INSERT INTO price_history VALUES(?,?,?)",
                    (f"p{i}", "2026-09-01T00:00:00Z", base + i * 100_000_000))


class BotTests(unittest.TestCase):
    def test_parse_query(self):
        self.assertEqual(interact.parse_query("پرادو ۱۴۰۱ ۴۰۰۰۰"), (["پرادو"], 2022, 40000))
        self.assertEqual(interact.parse_query("Prado 2022")[0], ["پرادو"])

    def test_search_and_estimate(self):
        con = db.connect(":memory:")
        _fill(con)
        txt = interact.search_text(con, "پرادو 2022")
        self.assertIn("میانه قیمت فعلی", txt)
        self.assertIn("divar.ir/v/p0", txt)
        est = interact.estimate_text(con, "تخمین پرادو 2022 41000")
        self.assertIn("بازهٔ معقول", est)
        self.assertIn("دادهٔ کافی", interact.estimate_text(con, "کمری 2022"))

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


if __name__ == "__main__":
    unittest.main()
