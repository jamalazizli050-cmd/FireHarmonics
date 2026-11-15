"""Train and validate the pooled disc forecaster used by firelens.inference.

    python scripts/train_global_model.py

Writes data/models/global/global_disc_forecaster.{joblib,ubj,json} and
reports/results/global/{global_validation.json, heldout_predictions_*.parquet}.
Uses only cached data (no downloads).
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np

from firelens import global_model as G
from firelens import location_features as LF
from firelens.catalog import HARMONIZATION_REGIONS
from firelens.config import PROJECT_ROOT, load_settings
from firelens.io import write_parquet
from firelens.logging_utils import setup_logging


def strip(v: dict) -> dict:
    return {k: x for k, x in v.items() if not k.startswith("_")}


def main() -> None:
    setup_logging(logging.INFO, log_file=PROJECT_ROOT / "logs" / "firelens.log")
    log = logging.getLogger("firelens.train_global")
    S = load_settings(require_key=False)
    t0 = time.time()
    arrs = []
    for r in HARMONIZATION_REGIONS:
        a = G.region_arrays(S, r)
        log.info("%s: %d centres, %d windows, observed months %d/%d (%.0fs)", r, a["n_centres"], len(a["lat"]),
                 int(a["observed"].sum()), len(a["observed"]), time.time() - t0)
        arrs.append(a)
    table = G.training_table(arrs)
    log.info("training table: %d rows, positives %.3f", len(table), table["y"].mean())

    base = {"with_lon": list(LF.FEATURES_ALL), "without_lon": [f for f in LF.FEATURES_ALL if f != "lon"]}
    variants = {f"{fn}{'+baseline_margin' if mg else ''}": (feats, mg) for fn, feats in base.items() for mg in (False, True)}
    # design selection on an inner split (test years 2020-2022 untouched)
    sel_table = table[table.year <= 2019]
    inner = {}
    for name, (feats, mg) in variants.items():
        v = G.validate(sel_table, feats, train_end=2016, calib_year=2017, test_years=(2018, 2019), margin=mg)
        inner[name] = {"temporal_brier": float(np.mean([v["temporal"]["all"][L]["xgboost"]["brier"] for L in G.LEADS])),
                       "loro_brier": float(np.mean([v["loro"]["all"][L]["xgboost"]["brier"] for L in G.LEADS])),
                       "temporal_bss_vs_best_baseline": {L: v["temporal"]["all"][L]["xgb_vs_best_baseline"]["bss"] for L in G.LEADS},
                       "loro_bss_vs_best_baseline": {L: v["loro"]["all"][L]["xgb_vs_best_baseline"]["bss"] for L in G.LEADS}}
        inner[name]["score"] = (inner[name]["temporal_brier"] + inner[name]["loro_brier"]) / 2
        log.info("inner %s: %s (%.0fs)", name, inner[name], time.time() - t0)
    chosen = min(inner, key=lambda n: inner[n]["score"])
    feats, mg = variants[chosen]
    val = G.validate(table, feats, margin=mg)          # one evaluation on the test years
    gate = G.decide(val)
    rel = G.reliability_tables(val)
    score = {n: v["score"] for n, v in inner.items()}
    vals = {chosen: val}

    final = G.fit(table[table.year <= 2021], table[table.year == 2022], feats, mg)
    meta = {
        "model": G.MODEL_NAME, "target": "P(>=1 usable S-NPP VIIRS vegetation-fire detection within the disc in the target month)",
        "features": feats, "baseline_margin": mg, "design_variant": chosen,
        "design_selection_inner_split": {"train_target_years": "2013-2016", "calibration_year": 2017, "selection_years": [2018, 2019], "results": inner},
        "leads": list(G.LEADS), "radii_km_trained": list(G.RADII_KM), "context_km": LF.CONTEXT_KM,
        "training_regions": HARMONIZATION_REGIONS, "training_period": [str(G.TRAIN_START), str(G.TRAIN_END)],
        "final_fit": {"train_target_years": "2013-2021", "calibration_year": 2022},
        "validation_fit": {"train_target_years": "2013-2018", "calibration_year": 2019, "test_years": [2020, 2021, 2022]},
        "n_rows": int(len(table)), "n_windows": int(sum(len(a["lat"]) for a in arrs)),
        "windows_per_region": {a["region"]: int(len(a["lat"])) for a in arrs},
        "validation": {"temporal": val["temporal"], "loro": val["loro"]},
        "method_gate": gate, "reliability": rel,
        "feature_importance_gain": val["feature_importance_gain"],
    }
    files = G.save(S, final, meta)
    out = S.reports_dir / "results" / "global"
    out.mkdir(parents=True, exist_ok=True)
    (out / "global_validation.json").write_text(json.dumps(
        {"chosen_variant": chosen, "design_selection_inner_split": inner,
         "variants": {n: strip(v) for n, v in vals.items()}, "method_gate": gate}, indent=1, default=str))
    write_parquet(val["_pred_temporal"], out / "heldout_predictions_temporal.parquet")
    write_parquet(val["_pred_loro"], out / "heldout_predictions_loro.parquet")
    log.info("saved %s (%.0fs)", files, time.time() - t0)
    print(json.dumps({"chosen": chosen, "score": score, "gate": gate}, indent=1, default=str))


if __name__ == "__main__":
    main()
