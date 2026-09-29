import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DRY_RUN"] = "1"
import stats, divar, db, main
from textutil import parse_int, parse_price, parse_year, fmt_price, hashtag


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
        self.assertEqual(fmt_price(19200000000), "۱۹.۲ میلیارد تومان")
        self.assertEqual(fmt_price(850000000), "۸۵۰ میلیون تومان")
        self.assertEqual(hashtag("لندکروزر 2021"), "#لندکروزر_2021")


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
        self.assertIn("#تویوتا", cap)
        self.assertIn("#لندکروزر_2021", cap)
        self.assertIn("divar.ir/v/t1", cap)

    def test_old_year_skipped(self):
        con = db.connect(":memory:")
        con.execute("INSERT INTO ads(token,status,first_seen) VALUES('t2','pending','x')")
        d = divar.parse_detail(SAMPLE); d["year"] = 2017
        main.apply_detail(con, "t2", d)
        self.assertEqual(con.execute("SELECT status FROM ads").fetchone()[0], "skipped")


if __name__ == "__main__":
    unittest.main()
