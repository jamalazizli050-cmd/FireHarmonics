"""Export JSON payloads for backend integration (see docs/API_INTEGRATION_SPEC.md).

This module only writes files under ``exports/api_v1/``; it does not touch any
backend code. Every value comes from FireLens outputs on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .catalog import REGIONS
from .config import Settings
from .io import read_parquet

SCHEMA_VERSION = "firelens.api.v1"


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (o != o) else round(float(o), 6)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(obj), indent=1, ensure_ascii=False))


def _status_note(h: dict) -> str:
    if not h:
        return "not evaluated"
    parts = [f"{t}: {h[t]['status']} (held-out region: {h[t]['heldout_region_verdict']}; held-out years ok: "
             f"{h[t]['heldout_years_ok']})" for t in ("count", "frp") if t in h]
    return "; ".join(parts) + ". Applies to the jointly observed domain; the full-domain series is not validated."


def export_all(settings: Settings, status: dict) -> list[str]:
    """``status``: {"harmonization": {region: {...}}, "forecast_regions": [...], ...}."""
    root = settings.data_dir.parent / "exports" / "api_v1"
    res_dir = settings.reports_dir / "results"
    written = []

    # 1) regions
    regions = []
    for name in status["regions"]:
        b = REGIONS[name].bbox
        h = status["harmonization"].get(name, {})
        regions.append({"region_id": name, "description": REGIONS[name].description,
                        "bbox": {"west": b.west, "south": b.south, "east": b.east, "north": b.north},
                        "harmonization_status": h.get("status", "not_evaluated"),
                        "harmonization_notes": h.get("notes") or _status_note(h),
                        "forecast_available": name in status["forecast_regions"]})
    p = root / "regions.json"
    _write(p, {"schema": SCHEMA_VERSION, "regions": regions})
    written.append(str(p))

    # 2) monthly activity per region
    series_path = res_dir / "patterns" / "regional_monthly_series.csv"
    if series_path.exists():
        s = pd.read_csv(series_path)
        for name in status["regions"]:
            sr = s[s["region"] == name]
            if sr.empty:
                continue
            h = status["harmonization"].get(name, {})
            series = {}
            for sname, g in sr.groupby("series"):
                series[sname] = {
                    "unit_count": "fire pixels (detections), vegetation fires only",
                    "unit_frp": "MW (sum of per-detection FRP)",
                    "harmonization_status": "not_validated" if sname == "modis_equivalent" else "observed",
                    **({"note": "Full-domain MODIS-equivalent (all VIIRS clusters). Not validated: when this region "
                                "was held out, the predicted total count was "
                                f"{h.get('count', {}).get('full_domain_total_ratio_heldout_region', float('nan')):.2f}x "
                                "the observed Aqua-MODIS total (VIIRS samples more overpasses than Aqua; not corrected). "
                                "Use /harmonized for the validated jointly-observed product."}
                       if sname == "modis_equivalent" else {}),
                    "data": [{"month": r.month, "count": r.count, "frp_mw": r.frp} for r in g.itertuples()]}
            p = root / "activity" / f"{name}_monthly.json"
            _write(p, {"schema": SCHEMA_VERSION, "region_id": name, "series": series,
                       "notes": "Counts are satellite hotspot detections, not fire incidents. Do not add or "
                                "compare counts across series without harmonization."})
            written.append(str(p))

    # 2b) harmonized history: held-out (leave-one-year-out) predictions on the
    # jointly observed domain - the product that was actually validated.
    mp = res_dir / "harmonization" / "monthly_predictions.csv"
    if mp.exists():
        d = pd.read_csv(mp)
        d = d[d["experiment"] == "loyo"]
        sm_p = res_dir / "harmonization" / "summary.csv"
        sm = pd.read_csv(sm_p) if sm_p.exists() else pd.DataFrame()
        for name in status["regions"]:
            g = d[d["region"] == name].sort_values("month")
            if g.empty:
                continue
            h = status["harmonization"].get(name, {})
            rows = [{"month": r.month,
                     "viirs_raw": {"count": r.viirs_raw_count, "frp_mw": r.viirs_raw_frp},
                     "modis_equivalent": {"count": r.full_count, "count_interval_90": [r.count_conf_lo, r.count_conf_hi],
                                          "frp_mw": r.full_frp, "frp_interval_90": [r.frp_conf_lo, r.frp_conf_hi]},
                     "modis_aqua_observed": {"count": r.modis_obs_count, "frp_mw": r.modis_obs_frp}}
                    for r in g.itertuples()]
            p = root / "harmonized" / f"{name}_monthly.json"
            _write(p, {"schema": SCHEMA_VERSION, "region_id": name,
                       "domain": "jointly observed: VIIRS fire clusters that Aqua-MODIS also observed within 20 min, "
                                 "vegetation fires only",
                       "prediction_type": "held-out: each year predicted by a model trained on the other years",
                       "validation": {"count": h.get("count", {}), "frp": h.get("frp", {}), "overall": h.get("status")},
                       "interval_method": "split-conformal (nominal 90%)",
                       "interval_empirical_coverage_heldout_years": (
                           {t: float(g2["pi90_cov_conformal"].mean()) for t, g2 in
                            sm[(sm["experiment"] == "loyo") & (sm["test_region"] == name)].groupby("target")}
                           if not sm.empty and "pi90_cov_conformal" in sm else None),
                       "data": rows})
            written.append(str(p))

    # 3) patterns
    pat = res_dir / "patterns" / "patterns.json"
    if pat.exists():
        pj = json.loads(pat.read_text())
        for name, v in pj.items():
            p = root / "patterns" / f"{name}.json"
            _write(p, {"schema": SCHEMA_VERSION, "region_id": name, **v})
            written.append(str(p))

    # 4) forecast
    fdir = res_dir / "forecast"
    fm = fdir / "forecast_metrics.json"
    for f in sorted(fdir.glob("forecast_*.parquet")):
        fc = read_parquet(f)
        metrics = json.loads(fm.read_text()) if fm.exists() else {}
        sig_p = fdir / "forecast_significance.json"
        sig = json.loads(sig_p.read_text()) if sig_p.exists() else {}
        skill = {split: {grp: {k: {m: metrics.get(split, {}).get("metrics", {}).get(grp, {}).get(k, {}).get(m)
                                   for m in ("roc_auc", "pr_auc", "brier", "ece", "base_rate")}
                               for k in ("xgboost_calibrated", "seasonal_climatology", "historical_frequency")}
                         for grp in ("all_cells", "ever_burned_cells")}
                 for split in ("temporal", "spatial_blocks") if split in metrics}
        for name, g in fc.groupby("region"):
            p = root / "forecast" / f"{name}_{fc['month'].iloc[0]}.json"
            _write(p, {
                "schema": SCHEMA_VERSION, "region_id": name, "forecast_month": fc["month"].iloc[0],
                "target": "probability of >=1 S-NPP VIIRS vegetation-fire detection in the 0.25 deg cell during the month",
                "model": {"name": "xgb_next_month", "calibration": "isotonic",
                          "heldout_test_years": metrics.get("temporal", {}).get("test_years"),
                          "heldout_skill": skill,
                          "gain_vs_baselines_bootstrap": {k: v.get("bootstrap_month_blocks_300") for k, v in sig.items()}},
                "limitations": status.get("forecast_limitations", []),
                "cells": [{"cell_id": int(r.cell_id), "lat": r.lat, "lon": r.lon, "p_fire": r.p_fire,
                           "p_seasonal_baseline": r.p_seasonal} for r in g.itertuples()]})
            written.append(str(p))

    # 5) model cards
    cards = {"schema": SCHEMA_VERSION, "harmonization": status["harmonization"],
             "forecast": {"regions": status["forecast_regions"], "limitations": status.get("forecast_limitations", [])}}
    mf = settings.data_dir / "models" / "harmonization" / "manifest.json"
    if mf.exists():
        cards["harmonization_manifest"] = json.loads(mf.read_text())
    p = root / "model_cards.json"
    _write(p, cards)
    written.append(str(p))
    return written
