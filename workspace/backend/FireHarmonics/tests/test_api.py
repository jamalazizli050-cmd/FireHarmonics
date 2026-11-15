import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from FireDetectionAPP import main


class APITests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"NASA_FIRMS_MAP_KEY": "test-key", "FIREHARMONICS_ML_ENGINE": "disabled"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.upstream = patch.object(main.httpx, "get")
        self.get = self.upstream.start()
        self.addCleanup(self.upstream.stop)

    def test_fire_routes_return_all_rows_and_metadata(self):
        csv_data = "latitude,longitude,confidence\n" + "48.1,-120.2,n\n" * 106
        self.get.return_value = httpx.Response(200, text=csv_data)
        for route in ("/", "/fires", "/test"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "application/json")
                body = response.json()
                self.assertEqual(body["status_code"], 200)
                self.assertEqual(body["count"], 106)
                self.assertEqual(len(body["rows"]), 106)
                self.assertEqual(body["rows"][-1], {
                    "latitude": "48.1", "longitude": "-120.2", "confidence": "n"
                })
                self.assertEqual(body["source"], main.SOURCE)
                self.assertEqual(body["area"], main.AREA)
                self.assertEqual(body["date"], main.DATE)
                self.assertEqual(body["day_range"], main.DAY_RANGE)
                self.get.assert_called_with(
                    f"{main.FIRMS_BASE_URL}/test-key/{main.SOURCE}/"
                    f"{main.AREA}/{main.DAY_RANGE}",
                    timeout=60.0,
                )

    def test_availability_preserves_columns(self):
        self.get.return_value = httpx.Response(
            200,
            text="data_id,min_date,max_date\nVIIRS_SNPP_NRT,2026-11-01,2026-11-02\n",
        )
        response = self.client.get("/availability")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status_code": 200,
            "source": main.SOURCE,
            "count": 1,
            "rows": [{
                "data_id": "VIIRS_SNPP_NRT",
                "min_date": "2026-11-01",
                "max_date": "2026-11-02",
            }],
        })
        self.get.assert_called_once_with(
            f"{main.AVAILABILITY_BASE_URL}/test-key/{main.SOURCE}", timeout=30.0
        )

    def test_empty_and_header_only_csv_return_success(self):
        for text in ("", " \n", "latitude,longitude\n"):
            for route in ("/fires", "/availability"):
                with self.subTest(text=text, route=route):
                    self.get.return_value = httpx.Response(200, text=text)
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["count"], 0)
                    self.assertEqual(response.json()["rows"], [])

    def test_quoted_values_and_utf8_bom_are_parsed(self):
        self.get.return_value = httpx.Response(
            200, text='\ufefflatitude,longitude,note\r\n48.1,-120.2,"smoke, fire\nobserved"\r\n'
        )
        response = self.client.get("/fires")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [
            {"latitude": "48.1", "longitude": "-120.2", "note": "smoke, fire\nobserved"}
        ])

    def test_missing_or_blank_key_returns_500_without_a_request(self):
        for value in (None, "", "  "):
            for route in ("/fires", "/availability"):
                with self.subTest(value=value, route=route):
                    if value is None:
                        os.environ.pop("NASA_FIRMS_MAP_KEY", None)
                    else:
                        os.environ["NASA_FIRMS_MAP_KEY"] = value
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 500)
                    self.assertEqual(response.json()["status_code"], 500)
                    self.assertIn("not configured", response.json()["detail"])
        self.get.assert_not_called()

    def test_upstream_http_errors_return_502_without_private_content(self):
        for code in (301, 400, 403, 429, 500, 503):
            for route in ("/fires", "/availability"):
                with self.subTest(code=code, route=route):
                    self.get.return_value = httpx.Response(code, text="test-key")
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(response.json()["status_code"], 502)
                    self.assertIn(str(code), response.json()["detail"])
                    self.assertNotIn("test-key", response.text)

    def test_timeouts_and_connection_failures_have_distinct_status_codes(self):
        for exception, expected_status in (
            (httpx.ReadTimeout("private URL test-key"), 504),
            (httpx.ConnectTimeout("private URL test-key"), 504),
            (httpx.ConnectError("private URL test-key"), 502),
        ):
            for route in ("/fires", "/availability"):
                with self.subTest(exception=type(exception), route=route):
                    self.get.side_effect = exception
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, expected_status)
                    self.assertEqual(response.json()["status_code"], expected_status)
                    self.assertNotIn("test-key", response.text)

    def test_malformed_csv_and_upstream_text_errors_return_502(self):
        for text in (
            "Invalid MAP_KEY: test-key",
            "Invalid request, test-key",
            "<html>Service unavailable</html>",
            "Error: service unavailable",
            "latitude,longitude\n48.1\n",
            "latitude,longitude\n48.1,-120.2,extra\n",
            'latitude,longitude\n48.1,"unterminated',
            "latitude,latitude\n48.1,49.1\n",
            "latitude,\n48.1,-120.2\n",
        ):
            for route in ("/fires", "/availability"):
                with self.subTest(text=text, route=route):
                    self.get.return_value = httpx.Response(200, text=text)
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(response.json()["status_code"], 502)
                    self.assertNotIn("test-key", response.text)

    def test_documentation_and_openapi_are_available_without_nasa(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        schema = self.client.get("/openapi.json").json()
        for route in ("/", "/fires", "/test", "/availability"):
            self.assertEqual(
                set(schema["paths"][route]["get"]["responses"]),
                {"200", "422", "500", "502", "504"},
            )
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
