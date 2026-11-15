import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import shapefile
from fastapi.testclient import TestClient

from FireDetectionAPP import main
from FireDetectionAPP.archives import parse_archive

PARAMS = {"latitude": -13, "longitude": 24, "start_date": "2020-01-01", "end_date": "2023-12-31"}
CSV = b"latitude,longitude,acq_date,acq_time,frp\n-13,24,2021-07-01,0930,12.5\n"


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"FIREHARMONICS_DATA_DIR": folder, "NASA_FIRMS_MAP_KEY": "",
                                                     "FIREHARMONICS_ML_ENGINE": "disabled"}))
        self.get = self.enterContext(patch.object(main.httpx, "get"))
        self.client = self.enterContext(TestClient(main.app))

    def upload(self, filename="fires.csv", data=CSV, params=None):
        return self.client.post("/api/v1/archives/import", params=params or PARAMS,
                                files={"file": (filename, data, "application/octet-stream")})

    def test_csv_import_and_cached_analysis_without_key(self):
        response = self.upload(params={**PARAMS, "coverage_complete": "true"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["result"]["detection_count"], 1)
        self.assertEqual(response.json()["result"]["historical_activity"]["mean_annual_detections"], 0.25)
        location = self.client.get("/api/v1/locations/analysis", params=PARAMS)
        self.assertEqual(location.json()["status"], "available")
        self.assertFalse(location.json()["stale"])
        rows = self.client.get("/api/v1/harmonized", params={**PARAMS, "limit": 1}).json()["rows"]
        self.assertEqual(rows[0]["raw"]["frp"], "12.5")
        response = self.client.post("/api/v1/analyses", json=PARAMS)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()["cache_hit"])
        self.assertEqual(response.json()["status"], "completed")
        regions = self.client.get("/api/v1/regions").json()["regions"]
        self.assertEqual(regions[0]["status"], "available")
        self.get.assert_not_called()

    def test_partial_import_is_explicit(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["result"]["coverage"]["complete"])
        self.assertIsNone(response.json()["result"]["historical_activity"]["mean_annual_detections"])

    def test_json_geojson_and_zip_csv(self):
        row = {"latitude": -13, "longitude": 24, "acq_date": "2021-07-01"}
        for value in ([row], {"rows": [row]}, {"data": [row]},
                      {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [24, -13]}, "properties": {"acq_date": "2021-07-01"}}]}):
            with self.subTest(value=value):
                response = self.upload("fires.json", json.dumps(value).encode())
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["result"]["detection_count"], 1)
        archive = io.BytesIO()
        with ZipFile(archive, "w") as output:
            output.writestr("../../fires.csv", CSV)
        response = self.upload("fires.zip", archive.getvalue())
        self.assertEqual(response.status_code, 201, response.text)
        self.get.assert_not_called()

    def test_zipped_shapefile_is_parsed_with_attributes(self):
        shp, dbf = io.BytesIO(), io.BytesIO()
        with shapefile.Writer(shp=shp, dbf=dbf, shapeType=shapefile.POINT) as writer:
            writer.field("ACQ_DATE", "C", 10)
            writer.field("ACQ_TIME", "C", 4)
            writer.field("FRP", "N", 12, 1)
            writer.point(24, -13)
            writer.record("2021-07-01", "0930", 12.5)
        archive = io.BytesIO()
        with ZipFile(archive, "w") as output:
            output.writestr("fires.shp", shp.getvalue())
            output.writestr("fires.dbf", dbf.getvalue())
        response = self.upload("fires.zip", archive.getvalue())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["result"]["hotspots"][0]["max_frp_mw"], 12.5)

    def test_bad_imports_are_rejected_with_status_codes(self):
        for filename, data, expected in (("fires.txt", CSV, 415), ("fires.csv", b"invalid", 422),
                                         ("fires.json", b"{broken", 422), ("fires.zip", b"not a zip", 422),
                                         ("fires.json", b'[{"latitude":NaN}]', 422),
                                         ("fires.csv", b"latitude,longitude,acq_date\n0,200,bad\n", 422)):
            with self.subTest(filename=filename, data=data):
                response = self.upload(filename, data)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(response.json()["status_code"], expected)
        missing_dates = self.upload(params={"latitude": 0, "longitude": 0})
        self.assertEqual(missing_dates.status_code, 422)
        with patch.object(main, "MAX_UPLOAD_BYTES", 2):
            self.assertEqual(self.upload().status_code, 413)
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
