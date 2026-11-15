"""API export: schema, scoping of forecasts to validated regions, no secrets."""
from __future__ import annotations

import json

import pandas as pd

from firelens import api_export
from firelens.io import write_parquet


def _fixture_outputs(settings):
    res = settings.reports_dir / "results"
    (res / "patterns").mkdir(parents=True)
    (res / "forecast").mkdir(parents=True)
    (res / "harmonization").mkdir(parents=True)
    cols = dict(viirs_raw_count=40.0, viirs_raw_frp=300.0, modis_obs_count=9.0, modis_obs_frp=200.0, full_count=8.5,
                full_frp=210.0, count_conf_lo=5.0, count_conf_hi=14.0, frp_conf_lo=120.0, frp_conf_hi=380.0)
    pd.DataFrame([{"fold": "loyo:california:2020", "experiment": "loyo", "region": "california", "month": "2020-01", **cols},
                  {"fold": "loro:california", "experiment": "loro", "region": "california", "month": "2020-01", **cols}]
                 ).to_csv(res / "harmonization" / "monthly_predictions.csv", index=False)
    pd.DataFrame({"month": ["2020-01", "2020-02"] * 2, "count": [3, 5, 2, 4], "frp": [10.0, 20.0, 8.0, 9.5],
                  "series": ["viirs_snpp"] * 2 + ["modis_equivalent"] * 2, "region": "california"}
                 ).to_csv(res / "patterns" / "regional_monthly_series.csv", index=False)
    (res / "patterns" / "patterns.json").write_text(json.dumps({"california": {"trend_agreement": {"same_sign": True}}}))
    (res / "forecast" / "forecast_metrics.json").write_text(json.dumps({"temporal": {"metrics": {"ever_burned_cells": {
        "xgboost_calibrated": {"roc_auc": 0.9, "pr_auc": 0.6, "brier": 0.1, "ece": 0.02}}}}}))
    write_parquet(pd.DataFrame({"cell_id": [1, 2], "region": "california", "lat": [36.1, 36.4], "lon": [-120.1, -120.4],
                                "p_fire": [0.2, float("nan")], "p_seasonal": [0.1, 0.3], "month": "2023-01"}),
                  res / "forecast" / "forecast_2023-01.parquet")


def test_export_all(settings):
    _fixture_outputs(settings)
    status = {"regions": ["california", "punjab"],
              "harmonization": {"california": {"status": "validated"}, "punjab": {"status": "partial"}},
              "forecast_regions": ["california"], "forecast_limitations": ["no weather features"]}
    written = api_export.export_all(settings, status)
    root = settings.data_dir.parent / "exports" / "api_v1"
    regions = json.loads((root / "regions.json").read_text())
    assert regions["schema"] == api_export.SCHEMA_VERSION
    by_id = {r["region_id"]: r for r in regions["regions"]}
    assert by_id["california"]["forecast_available"] and not by_id["punjab"]["forecast_available"]
    assert by_id["punjab"]["harmonization_status"] == "partial"

    act = json.loads((root / "activity" / "california_monthly.json").read_text())
    # full-domain series is never labelled validated (only the jointly observed product was validated)
    assert act["series"]["modis_equivalent"]["harmonization_status"] == "not_validated"
    assert act["series"]["viirs_snpp"]["harmonization_status"] == "observed"
    assert act["series"]["viirs_snpp"]["data"][1] == {"month": "2020-02", "count": 5, "frp_mw": 20.0}

    hz = json.loads((root / "harmonized" / "california_monthly.json").read_text())
    assert len(hz["data"]) == 1                      # LOYO rows only
    assert hz["data"][0]["modis_equivalent"]["count_interval_90"] == [5.0, 14.0]
    assert hz["validation"]["overall"] == "validated"

    fc = json.loads((root / "forecast" / "california_2023-01.json").read_text())
    assert fc["cells"][1]["p_fire"] is None          # NaN -> null, valid JSON
    assert fc["limitations"] == ["no weather features"]
    assert not (root / "forecast" / "punjab_2023-01.json").exists()

    for p in written:   # strict JSON and no secret leakage
        txt = open(p, encoding="utf-8").read()
        json.loads(txt)
        assert "NaN" not in txt and settings.map_key not in txt
