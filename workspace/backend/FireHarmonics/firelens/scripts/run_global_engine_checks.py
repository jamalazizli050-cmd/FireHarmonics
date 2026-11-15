"""Real end-to-end checks of the universal engine on six contrasting locations.

    python scripts/run_global_engine_checks.py            # all six, as background jobs
    python scripts/run_global_engine_checks.py --cached   # re-run (timing of cached analyses)

Downloads FIRMS data for locations that are not cached (rate-limited,
deduplicated). Writes reports/results/global/locations/<name>.json and
<name>_events.jsonl, plus summary.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from firelens.config import PROJECT_ROOT, load_settings
from firelens.jobs import JobFailed, JobManager
from firelens.logging_utils import setup_logging

LOCATIONS = {
    "california_sierra_nevada": (37.7, -119.5, "known training region"),
    "zambia": (-13.0, 24.0, "different climate (training region zambia_angola)"),
    "siberia_yakutia": (62.0, 125.0, "challenging boreal region (training region yakutia)"),
    "northern_australia": (-14.5, 132.5, "outside the five regions"),
    "central_england": (52.5, -1.5, "low fire activity"),
    "south_pacific_ocean": (-30.0, -140.0, "open ocean"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true", help="tag outputs as the cached re-run")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--radius", type=float, default=25.0)
    args = ap.parse_args()
    setup_logging(logging.INFO, log_file=PROJECT_ROOT / "logs" / "firelens.log")
    S = load_settings(require_key=False)
    out = S.reports_dir / "results" / "global" / "locations"
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_cached_rerun" if args.cached else ""
    jm = JobManager(S, max_workers=2)
    jobs, t0, files = {}, {}, {}
    for name, (lat, lon, why) in LOCATIONS.items():
        if args.only and name not in args.only:
            continue
        f = (out / f"{name}{suffix}_events.jsonl").open("w", encoding="utf-8")
        files[name] = f

        def cb(ev, _f=f, _n=name):
            _f.write(json.dumps(ev) + "\n")
            _f.flush()
            if ev["type"] != "progress" or ev["stage_progress"] and ev["stage_progress"]["completed"] % 100 == 0:
                print(f"[{_n}] {ev['type']:10s} {ev['status']:9s} {ev['stage'] or '':18s} {ev['progress_percent']:5.1f}% "
                      f"eta={ev['eta_seconds']} {ev['message'][:80]}", flush=True)
        jobs[name] = jm.start_analysis(lat, lon, args.radius, callback=cb)
        t0[name] = time.time()
        print(f"submitted {name}: job {jobs[name]}", flush=True)
    summary = {}
    for name, jid in jobs.items():
        try:
            r = jm.get_job_result(jid, timeout=4 * 3600)
            (out / f"{name}{suffix}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
            st = jm.get_job_status(jid)
            p = r["prediction"]
            summary[name] = {
                "job_id": jid, "why": LOCATIONS[name][2], "status": r["status"],
                "started_at": st["started_at"], "finished_at": st["finished_at"],
                "chunks_downloaded": r["data_coverage"]["retrieval"]["chunks_downloaded_this_call"],
                "chunks_failed": r["data_coverage"]["retrieval"]["chunks_failed_this_call"],
                "chunks_reused_from_concurrent_job": r["data_coverage"]["retrieval"]["chunks_reused_from_concurrent_job"],
                "months_observed": r["data_coverage"]["months_observed"],
                "months_unobserved": r["data_coverage"]["months_unobserved"],
                "last_observed_month": r["data_coverage"]["last_observed_month"],
                "total_detections": r["historical_activity"]["total_detections_observed_months"],
                "mean_annual_detections": r["historical_activity"]["mean_annual_detections"],
                "peak_month": r["seasonal_patterns"].get("peak_month"),
                "core_season": r["seasonal_patterns"].get("core_season_months_80pct"),
                "trend": {k: r["long_term_trends"].get(k) for k in ("status", "theil_sen_pct_per_year", "p_value")},
                "n_anomalies": len(r["anomalies"]), "n_hotspot_cells": r["hotspot_analysis"].get("n_gi_star_hotspot_cells_99"),
                "prediction": {k: p.get(k) for k in ("status", "target_month", "lead_months", "probability", "method", "reason")},
                "seasonal_norm": p.get("seasonal_norm"), "relative_activity": (p.get("relative_activity") or {}).get("category"),
                "xgboost_pooled_probability_not_used": ((p.get("model_comparison") or {}).get("xgboost_pooled") or {}).get("probability"),
                "warnings": r["warnings"]}
        except JobFailed as e:
            summary[name] = {"job_id": jid, "status": "job_failed", "error": e.error}
        print(json.dumps({name: summary[name]}, default=str)[:1500], flush=True)
    for f in files.values():
        f.close()
    (out / f"summary{suffix}.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    jm.shutdown()


if __name__ == "__main__":
    main()
