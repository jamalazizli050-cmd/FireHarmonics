"""Sensor provenance and the precomputed FireLens harmonization products.

NASA FIRMS is the *distribution service*. MODIS, VIIRS and OLI are the
observing instruments; Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21 and Landsat
are the platforms. "Harmonized" data is a derived analytical product and only
exists where FireLens produced and validated it (five regions, regional scale).
Nothing here combines raw MODIS and VIIRS counts.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from .ml_engine import DEFAULT_FIRELENS_ROOT

PROVIDER = {"name": "NASA FIRMS", "role": "data distribution service (not a sensor)",
            "url": "https://firms.modaps.eosdis.nasa.gov"}
_SOURCES = {
    "MODIS_NRT": ("MODIS", ["Terra", "Aqua"], "near_real_time", 1000),
    "MODIS_SP": ("MODIS", ["Terra", "Aqua"], "standard", 1000),
    "VIIRS_NOAA20_NRT": ("VIIRS", ["NOAA-20"], "near_real_time", 375),
    "VIIRS_NOAA20_SP": ("VIIRS", ["NOAA-20"], "standard", 375),
    "VIIRS_NOAA21_NRT": ("VIIRS", ["NOAA-21"], "near_real_time", 375),
    "VIIRS_NOAA21_SP": ("VIIRS", ["NOAA-21"], "standard", 375),
    "VIIRS_SNPP_NRT": ("VIIRS", ["Suomi NPP"], "near_real_time", 375),
    "VIIRS_SNPP_SP": ("VIIRS", ["Suomi NPP"], "standard", 375),
    "LANDSAT_NRT": ("OLI", ["Landsat 8", "Landsat 9"], "near_real_time", 30),
}
REGION_ID = re.compile(r"^[a-z_]{1,40}$")


def source_provenance(source: str) -> dict:
    instrument, platforms, processing, resolution = _SOURCES[source]
    return {"provider": PROVIDER["name"], "product": source, "instrument": instrument, "platforms": platforms,
            "processing": processing, "nominal_resolution_m": resolution, "product_kind": "raw_detections"}


# ------------------------------------------------------------------ harmonization files
def firelens_root() -> Path:
    return Path(os.getenv("FIRELENS_ROOT") or DEFAULT_FIRELENS_ROOT)


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=4)
def _regions(root: str) -> dict:
    data = _read(Path(root) / "exports" / "api_v1" / "regions.json") or {}
    return {item["region_id"]: item for item in data.get("regions", []) if REGION_ID.fullmatch(item.get("region_id", ""))}


@lru_cache(maxsize=4)
def _validation(root: str) -> dict:
    return _read(Path(root) / "reports" / "results" / "harmonization" / "validation_status.json") or {}


def regions() -> dict:
    return _regions(str(firelens_root()))


def region_for(latitude: float, longitude: float) -> str | None:
    for region_id, item in regions().items():
        box = item.get("bbox") or {}
        if box and box["south"] <= latitude <= box["north"] and box["west"] <= longitude <= box["east"]:
            return region_id
    return None


def overview() -> dict:
    items = []
    validation = _validation(str(firelens_root()))
    for region_id, item in regions().items():
        status = validation.get(region_id, {})
        items.append({"region_id": region_id, "description": item.get("description"), "bbox": item.get("bbox"),
                      "status": item.get("harmonization_status"),
                      "count_status": (status.get("count") or {}).get("status"),
                      "frp_status": (status.get("frp") or {}).get("status"),
                      "notes": item.get("harmonization_notes"),
                      "series_url": f"/api/v1/harmonization/regions/{region_id}"})
    available = bool(items)
    return {"available": available, "status": "regional_only" if available else "unavailable",
            "product": "MODIS-equivalent monthly series derived from VIIRS (FireLens)",
            "scale": "regional (training-region bounding boxes); not validated at 25 km location scale",
            "domain": "jointly observed: VIIRS fire clusters that Aqua-MODIS also observed within 20 min",
            "regions": items,
            "limitations": [
                "Harmonization was validated only regionally and only partly; it is not globally established.",
                "FRP harmonization was not comprehensively validated (no region has a fully validated FRP model).",
                "Series are held-out predictions for 2012-2021 and are not updated by new analyses.",
            ] if available else ["The FireLens harmonization exports are not present in this deployment."]}


def regional_series(region_id: str) -> dict | None:
    if not REGION_ID.fullmatch(region_id) or region_id not in regions():
        return None
    return _read(firelens_root() / "exports" / "api_v1" / "harmonized" / f"{region_id}_monthly.json")


def satellite_analysis(request_source: str, latitude: float, longitude: float, detection_count: int,
                       ml_ran: bool, engine_harmonization: dict | None) -> dict:
    """Per-result view of what each data series is, and what is genuinely available."""
    current = source_provenance(request_source)
    region_id = (engine_harmonization or {}).get("project_region") or region_for(latitude, longitude)
    validation = _validation(str(firelens_root())).get(region_id, {}) if region_id else {}
    if region_id and region_id in regions():
        harmonized = {"status": "regional_only", "region_id": region_id, "location_scale": "not_validated",
                      "validation": {"overall": validation.get("status"),
                                     "count": (validation.get("count") or {}).get("status"),
                                     "frp": (validation.get("frp") or {}).get("status")},
                      "series_url": f"/api/v1/harmonization/regions/{region_id}",
                      "explanation": "A regional MODIS-equivalent series exists for the training region that "
                                     "contains this point. It is not a harmonized series for the 25 km disc."}
    else:
        harmonized = {"status": "unavailable", "region_id": None, "location_scale": "not_validated",
                      "validation": None, "series_url": None,
                      "explanation": "No harmonized product exists for this location: FireLens harmonization "
                                     "covers only five training regions at regional scale."}
    def view(instrument, default_source):
        if current["instrument"] == instrument:
            return {"status": "this_result", "source": request_source, "detection_count": detection_count}
        return {"status": "separate_request", "source": default_source, "detection_count": None,
                "explanation": f"POST /api/v1/analyses with source={default_source} for raw {instrument} detections."}
    return {
        "provider": PROVIDER,
        "descriptive_series": current,
        "forecast_series": {"products": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"], "instrument": "VIIRS",
                            "platforms": ["Suomi NPP"], "used": ml_ran},
        "views": {"raw_modis": view("MODIS", "MODIS_SP"), "raw_viirs": view("VIIRS", "VIIRS_NOAA20_SP"),
                  "harmonized": harmonized},
        "modis_viirs_mixed": False,
        "warnings": [
            "Raw MODIS (1 km) and VIIRS (375 m) detection counts are not comparable and are never summed.",
            "The descriptive statistics use the requested source; the ML analysis and forecast use S-NPP VIIRS.",
        ],
    }


# ----------------------------------------------------------------- normalized (relative) view
NORMALIZED_MIN_MONTHS = 12
NORMALIZED_DEFINITION = ("Relative activity index: each month's detections divided by the mean monthly detections of the "
                         "same series over its observed months (1.0 = an average month). Dimensionless. It makes the "
                         "shape of the seasonal cycle and anomalies comparable between MODIS and VIIRS, but not their "
                         "absolute counts.")
NORMALIZED_LIMITATIONS = [
    "This is a mathematical normalization, not a harmonization: no MODIS-equivalent counts are estimated and nothing "
    "is validated.",
    "Each series is scaled by its own mean, so an index of 2.0 means twice that sensor's typical month, not twice the "
    "other sensor's value.",
    "Unobserved months are excluded from the reference mean and have no index (they are not zero).",
]


def _relative_index(rows: list[dict], month_key: str, value_key: str, usable) -> dict | None:
    """Index rows by their mean over usable (observed / complete) months. None when there are too few months."""
    usable_rows = [r for r in rows if usable(r) and r.get(value_key) is not None]
    if len(usable_rows) < NORMALIZED_MIN_MONTHS:
        return None
    mean = sum(float(r[value_key]) for r in usable_rows) / len(usable_rows)
    months = [r[month_key] for r in usable_rows]
    out = {"reference_period": {"start": min(months), "end": max(months), "months": len(usable_rows)},
           "reference_mean_monthly_detections": round(mean, 4)}
    if mean <= 0:
        return {**out, "status": "no_activity", "monthly": [],
                "explanation": "No detections in the reference months, so the index is undefined."}
    return {**out, "status": "available", "monthly": [
        {"month": r[month_key], "detections": r.get(value_key),
         "index": round(float(r[value_key]) / mean, 4) if usable(r) and r.get(value_key) is not None else None}
        for r in rows]}


def normalized_view(ml_analysis: dict, descriptive: dict, request_source: str) -> dict:
    """Relative activity index for the ML record (S-NPP VIIRS) and, with >= 12 complete months, the requested source.

    Offered everywhere, intended for locations without a validated regional harmonized series.
    """
    series = []
    monthly = ((ml_analysis or {}).get("historical_activity") or {}).get("monthly") or []
    ml = _relative_index(monthly, "month", "detections", lambda r: bool(r.get("observed")))
    if ml:
        series.append({"series": "ml_record", "product": "S-NPP VIIRS (VIIRS_SNPP_SP, then VIIRS_SNPP_NRT)",
                       "instrument": "VIIRS", **ml})
    desc_rows = (descriptive.get("historical_activity") or {}).get("monthly_counts") or []
    desc = _relative_index(desc_rows, "month", "detections", lambda r: bool(r.get("complete")))
    if desc:
        series.append({"series": "descriptive", "product": request_source,
                       "instrument": source_provenance(request_source)["instrument"], **desc})
    available = any(s["status"] == "available" for s in series)
    return {"status": "available" if available else "insufficient_data",
            "method": "relative_activity_index", "validated": False,
            "label": "Normalized — not harmonized, not validated",
            "definition": NORMALIZED_DEFINITION, "min_months": NORMALIZED_MIN_MONTHS,
            "series": series, "limitations": NORMALIZED_LIMITATIONS,
            "explanation": None if available else
            f"At least {NORMALIZED_MIN_MONTHS} observed months with detections are needed for a relative index."}
