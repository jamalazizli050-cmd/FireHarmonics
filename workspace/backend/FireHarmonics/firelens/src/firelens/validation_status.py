"""Per-region harmonisation validation status from held-out folds.

Rule (fixed before reading per-region results in detail):
* ``validated``   - leave-one-region-out verdict is "improved" AND held-out-year
                    verdicts are "improved" or "marginal" in >= 75% of years;
* ``partial``     - exactly one of the two conditions holds;
* ``not_validated`` - neither holds.
Statuses are given separately for detection counts and FRP. The full-domain
product (all VIIRS clusters vs all Aqua pixels) is tracked separately.
"""
from __future__ import annotations

import json

import pandas as pd


def compute_status(summary: pd.DataFrame) -> dict:
    out = {}
    for region in sorted(summary["test_region"].unique()):
        r = summary[summary["test_region"] == region]
        out[region] = {}
        for target in ("count", "frp"):
            t = r[r["target"] == target]
            loro = t[t["experiment"] == "loro"]["verdict"].tolist()
            loyo = t[t["experiment"] == "loyo"]["verdict"].tolist()
            loro_ok = bool(loro) and loro[0] == "improved"
            ok_years = sum(v == "improved" or v.startswith("marginal") for v in loyo)
            loyo_ok = bool(loyo) and ok_years / len(loyo) >= 0.75
            status = "validated" if loro_ok and loyo_ok else "partial" if (loro_ok or loyo_ok) else "not_validated"
            fd = t[t["experiment"] == "loro"]
            out[region][target] = {
                "status": status, "heldout_region_verdict": loro[0] if loro else None,
                "heldout_years_ok": f"{ok_years}/{len(loyo)}",
                "median_monthly_error_factor_model_heldout_region":
                    float(10 ** fd["medAbsLog10_full"].iloc[0]) if len(fd) else None,
                "median_monthly_error_factor_raw_viirs":
                    float(10 ** fd["medAbsLog10_raw"].iloc[0]) if len(fd) else None,
                "full_domain_total_ratio_heldout_region":
                    float(fd["fd_totalRatio_full"].iloc[0]) if len(fd) and "fd_totalRatio_full" in fd else None}
        c, f = out[region]["count"]["status"], out[region]["frp"]["status"]
        out[region]["status"] = "validated" if c == f == "validated" else \
            "validated_frp_only" if f == "validated" else \
            "validated_count_only" if c == "validated" else \
            "partial" if "partial" in (c, f) else "not_validated"
    return out


def save_status(settings, summary: pd.DataFrame) -> dict:
    st = compute_status(summary)
    p = settings.reports_dir / "results" / "harmonization" / "validation_status.json"
    p.write_text(json.dumps(st, indent=1))
    return st
