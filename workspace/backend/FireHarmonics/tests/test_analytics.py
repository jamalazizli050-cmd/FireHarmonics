import unittest
from datetime import date

from fastapi import HTTPException

from FireDetectionAPP import analytics
from FireDetectionAPP.schemas import AnalysisRequest


class AnalyticsTests(unittest.TestCase):
    def test_dateline_poles_and_radius(self):
        east = analytics.bounding_boxes(0, 179.95, 25)
        west = analytics.bounding_boxes(0, -179.95, 25)
        self.assertEqual(len(east), 2)
        self.assertEqual(len(west), 2)
        for boxes in (east, west, analytics.bounding_boxes(90, 0, 25), analytics.bounding_boxes(-90, 0, 25)):
            for left, bottom, right, top in boxes:
                self.assertTrue(-180 <= left < right <= 180)
                self.assertTrue(-90 <= bottom < top <= 90)
        self.assertLess(analytics.distance_km(0, 179.95, 0, -179.95), 12)
        self.assertAlmostEqual(analytics.distance_km(90, 1, 90, 2), 0, places=10)

    def test_normalization_retains_fields_and_rejects_bad_coordinates_dates(self):
        rows = analytics.normalize_rows([{"latitude": "-13", "longitude": "24", "acq_date": "2024-01-01", "acq_time": "930", "frp": "12.5", "custom": "keep"}])
        self.assertEqual(rows[0]["raw"]["custom"], "keep")
        self.assertEqual(rows[0]["acquired_at"], "2024-01-01T09:30:00+00:00")
        self.assertEqual(rows[0]["frp"], 12.5)
        for update in ({"latitude": "nan"}, {"longitude": 200}, {"acq_date": "bad"}, {"acq_time": "2460"}):
            with self.subTest(update=update), self.assertRaises(HTTPException):
                analytics.normalize_rows([{"latitude": 0, "longitude": 0, "acq_date": "2024-01-01", **update}])

    def test_complete_years_zero_years_trend_seasonality_and_anomalies(self):
        request = AnalysisRequest(latitude=0, longitude=0, start_date=date(2020, 1, 1), end_date=date(2023, 12, 31))
        raw = [{"latitude": 0, "longitude": 0, "acq_date": f"{year}-07-{day:02d}"}
               for year, count in ((2021, 2), (2022, 4), (2023, 12)) for day in range(1, count + 1)]
        rows = analytics.normalize_rows(raw)
        result = analytics.describe(rows, request, {"start_date": "2020-01-01", "end_date": "2023-12-31", "complete": True})
        self.assertEqual(result["historical_activity"]["mean_annual_detections"], 4.5)
        self.assertEqual(result["historical_activity"]["annual_counts"][0]["detections"], 0)
        self.assertEqual(result["seasonal_patterns"]["peak_month"], 7)
        self.assertEqual(result["long_term_trends"]["direction"], "increasing")
        self.assertAlmostEqual(result["long_term_trends"]["slope_detections_per_year"], 3.8)
        self.assertEqual(result["anomalies"]["status"], "available")
        self.assertTrue(result["anomalies"]["items"])
        self.assertIsNone(result["prediction"]["probability"])
        self.assertEqual(result["hotspots"][0]["detections"], 18)

    def test_incomplete_archive_does_not_claim_annual_mean_or_seasonality(self):
        request = AnalysisRequest(latitude=0, longitude=0)
        result = analytics.describe([], request, {"start_date": "2020-01-01", "end_date": "2023-12-31", "complete": False})
        self.assertIsNone(result["historical_activity"]["mean_annual_detections"])
        self.assertEqual(result["historical_activity"]["daily_counts"], [])
        self.assertIsNone(result["seasonal_patterns"]["peak_month"])
        self.assertEqual(result["long_term_trends"]["status"], "insufficient_data")
        self.assertEqual(result["anomalies"]["status"], "insufficient_data")

    def test_complete_empty_period_is_observed_zero_not_missing_data(self):
        result = analytics.describe([], AnalysisRequest(latitude=0, longitude=0),
                                    {"start_date": "2020-01-01", "end_date": "2022-12-31", "complete": True})
        self.assertEqual(result["historical_activity"]["mean_annual_detections"], 0)
        self.assertIsNone(result["seasonal_patterns"]["peak_month"])
        self.assertEqual(result["long_term_trends"]["direction"], "flat")
        self.assertEqual(result["anomalies"]["items"], [])


if __name__ == "__main__":
    unittest.main()
