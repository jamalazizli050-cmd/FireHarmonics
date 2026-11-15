import json
from pathlib import Path

from FireDetectionAPP.satellites import NORMALIZED_MIN_MONTHS, normalized_view

EXAMPLE = json.loads((Path(__file__).resolve().parents[1] / "docs" / "examples" / "analysis_result.synthetic.json")
                     .read_text(encoding="utf-8"))


def test_relative_index_of_ml_record():
    view = normalized_view(EXAMPLE["ml_analysis"], EXAMPLE, "VIIRS_NOAA20_NRT")
    assert view["status"] == "available" and view["validated"] is False
    assert view["label"].startswith("Normalized")
    ml = view["series"][0]
    assert ml["series"] == "ml_record" and ml["status"] == "available"
    observed = [m for m in EXAMPLE["ml_analysis"]["historical_activity"]["monthly"] if m["observed"]]
    mean = sum(m["detections"] for m in observed) / len(observed)
    assert ml["reference_period"]["months"] == len(observed)
    assert abs(ml["reference_mean_monthly_detections"] - round(mean, 4)) < 1e-9
    by_month = {m["month"]: m for m in ml["monthly"]}
    assert abs(by_month["2024-07"]["index"] - 31 / mean) < 1e-3
    assert by_month["2024-01"]["index"] == 0.0              # observed zero stays zero
    assert by_month["2026-11"]["index"] is None              # unobserved month: no index, not zero
    # the 10-day descriptive period is too short for its own index
    assert [s["series"] for s in view["series"]] == ["ml_record"]


def test_insufficient_and_no_activity():
    short = {"historical_activity": {"monthly": [
        {"month": f"2024-{m:02d}", "detections": 1, "observed": True} for m in range(1, NORMALIZED_MIN_MONTHS)]}}
    assert normalized_view(short, {}, "MODIS_SP")["status"] == "insufficient_data"
    quiet = {"historical_activity": {"monthly": [
        {"month": f"2024-{m:02d}", "detections": 0, "observed": True} for m in range(1, 13)]}}
    view = normalized_view(quiet, {}, "MODIS_SP")
    assert view["status"] == "insufficient_data" and view["series"][0]["status"] == "no_activity"


def test_descriptive_series_uses_complete_months_only():
    rows = [{"month": f"2023-{m:02d}", "detections": m, "complete": True} for m in range(1, 13)]
    rows.append({"month": "2024-01", "detections": 99, "complete": False})
    view = normalized_view({}, {"historical_activity": {"monthly_counts": rows}}, "MODIS_SP")
    desc = view["series"][0]
    assert desc["series"] == "descriptive" and desc["instrument"] == "MODIS"
    assert desc["reference_mean_monthly_detections"] == 6.5
    assert desc["monthly"][-1]["index"] is None              # partial month is not indexed
