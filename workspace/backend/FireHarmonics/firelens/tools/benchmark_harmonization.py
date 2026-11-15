"""Benchmark the continuity chain against the previous pixel-ratio method and raw VIIRS on held-out years.

Uses only locally cached FIRMS yearly archives (no downloads, no MAP_KEY). For every location it builds the calendar
and reports the forward-in-time tests:

* "MODIS switched off": S-NPP -> Aqua coefficients fitted on 2012-2016 predict Aqua in 2017-2021;
* bridge: NOAA-20 -> S-NPP fitted on 2019-2022 predicts S-NPP in 2023-2025.

Usage (from firelens/):
    python tools/benchmark_harmonization.py                      # default locations, radius 25 km
    python tools/benchmark_harmonization.py --radius 50 --kappa 10,40,160
    python tools/benchmark_harmonization.py --json out.json 37.7,-119.5 -13,24
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firelens import unified_calendar as uc  # noqa: E402
from firelens.config import load_settings  # noqa: E402

LOCATIONS = {
    "Yosemite, US": (37.7, -119.5), "SW Oregon, US": (42.5, -123.0), "Bitterroot, Montana, US": (46.5, -114.0),
    "Flint Hills, Kansas, US": (38.5, -96.5), "Mississippi delta, Arkansas, US": (34.6, -91.0),
    "Okefenokee, Georgia, US": (30.8, -82.3), "Central Angola": (-12.5, 17.5), "Lunda, Angola": (-9.5, 20.0),
    "Zambia/Angola border": (-13.0, 24.0), "Kafue, Zambia": (-15.5, 26.0), "Northern Zambia": (-11.0, 30.5),
    "Viseu, Portugal": (40.7, -7.9), "Alentejo, Portugal": (38.3, -8.0), "Extremadura, Spain": (39.5, -6.0),
    "Sardinia, Italy": (40.0, 9.0), "Calabria, Italy": (39.0, 16.4), "Central Poland": (52.0, 19.5),
    "Peak District, UK": (53.4, -1.8),
}
METHODS = ("continuity_chain", "pixel_ratio", "raw_target")


def med(xs):
    xs = [x for x in xs if x is not None]
    return None if not xs else float(np.median(xs))


def fmt(x, pct=False):
    if x is None:
        return "   -  "
    return f"{x * 100:5.0f}%" if pct else f"{x:6.3f}"


def run(points: dict, radius: float, kappa: float, settings) -> list[dict]:
    out = []
    for name, (lat, lon) in points.items():
        t0 = time.time()
        cal, _ = uc.build(settings, lat, lon, radius, date.today(), None, allow_download=False, kappa=kappa)
        rec = {"location": name, "lat": lat, "lon": lon, "status": cal.get("status"), "seconds": round(time.time() - t0, 1)}
        if cal.get("status") == "ok":
            rec["chain"] = cal["chain"]
            rec["validation"] = cal["validation"]
        else:
            rec["explanation"] = cal.get("explanation")
        out.append(rec)
    return out


def summary(results: list[dict], link: str) -> dict:
    tests = [r["validation"][link] for r in results if r.get("validation", {}).get(link, {}).get("status") == "ok"]
    s = {"locations": len(tests)}
    for m in METHODS:
        s[m] = {k: med([(t["methods"][m] or {}).get(k) for t in tests])
                for k in ("median_abs_pct_error_annual", "median_abs_log_error_monthly", "anomaly_rank_correlation")}
    s["interval_90_coverage"] = med([t["interval_90_coverage"] for t in tests])
    s["chain_beats_pixel_ratio"] = sum(bool(t.get("beats_pixel_ratio")) for t in tests)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("points", nargs="*", help="lat,lon pairs (default: built-in list)")
    ap.add_argument("--radius", type=float, default=25.0)
    ap.add_argument("--kappa", default=str(uc.KAPPA), help="comma-separated prior strengths to compare")
    ap.add_argument("--json", help="write full results here")
    a = ap.parse_args()
    settings = load_settings(require_key=False)
    points = {p: tuple(float(v) for v in p.split(",")) for p in a.points} if a.points else LOCATIONS
    report = {"radius_km": a.radius, "runs": []}
    for kappa in [float(k) for k in a.kappa.split(",")]:
        res = run(points, a.radius, kappa, settings)
        print(f"\n=== kappa {kappa:g}, radius {a.radius:g} km ===")
        print(f"{'location':34s} {'status':10s} {'Aqua<-SNPP annual err: chain / pixel / raw':44s} cover  w_loc(D)")
        for r in res:
            v = r.get("validation", {}).get("aqua_snpp", {})
            if v.get("status") == "ok":
                e = [(v["methods"][m] or {}).get("median_abs_pct_error_annual") for m in METHODS]
                w = r["chain"][0]["local_weight"]["D"]
                print(f"{r['location'][:34]:34s} {'ok':10s} {fmt(e[0], 1)} / {fmt(e[1], 1)} / {fmt(e[2], 1)}"
                      f"{'':22s} {fmt(v['interval_90_coverage'], 1)}  {w:.2f}")
            else:
                print(f"{r['location'][:34]:34s} {str(r['status'])[:10]:10s} {v.get('status') or r.get('explanation', '')}"[:120])
        sums = {link: summary(res, link) for link in ("aqua_snpp", "snpp_n20")}
        for link, s in sums.items():
            print(f"-- {link}: {s['locations']} locations; median annual error "
                  + ", ".join(f"{m} {fmt(s[m]['median_abs_pct_error_annual'], 1)}" for m in METHODS)
                  + f"; monthly log error chain {fmt(s['continuity_chain']['median_abs_log_error_monthly'])}"
                  + f" vs pixel {fmt(s['pixel_ratio']['median_abs_log_error_monthly'])}"
                  + f"; anomaly rank corr chain {fmt(s['continuity_chain']['anomaly_rank_correlation'])}"
                  + f"; 90% interval coverage {fmt(s['interval_90_coverage'], 1)}"
                  + f"; chain beats pixel ratio at {s['chain_beats_pixel_ratio']}/{s['locations']}")
        report["runs"].append({"kappa": kappa, "summary": sums, "locations": res})
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        print(f"\nfull results: {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
