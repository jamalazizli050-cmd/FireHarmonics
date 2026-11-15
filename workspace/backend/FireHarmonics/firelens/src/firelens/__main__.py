"""Command-line interface: ``python -m firelens <command> ...``."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import pipeline
from .catalog import resolve_region
from .config import PROJECT_ROOT, load_settings
from .firms_client import FirmsAPIError, FirmsClient
from .logging_utils import setup_logging


def _date(s: str) -> date:
    return date.fromisoformat(s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="firelens", description="FireLens NASA FIRMS pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("availability", help="Show FIRMS data availability per source")
    sub.add_parser("status", help="Show MAP_KEY transaction usage")

    def common(sp, need_dates=True):
        sp.add_argument("--region", default="california",
                        help="Region name (california, world) or 'west,south,east,north'")
        sp.add_argument("--sources", nargs="+", default=pipeline.DEFAULT_SOURCES)
        sp.add_argument("--start", type=_date, required=need_dates)
        sp.add_argument("--end", type=_date, required=need_dates)

    d = sub.add_parser("download", help="Download (incrementally) raw FIRMS data")
    common(d)
    d.add_argument("--chunk-days", type=int, default=5, help="Days per API request (1-5)")
    d.add_argument("--force", action="store_true", help="Re-download cached chunks")

    pr = sub.add_parser("process", help="Validate raw data and write Parquet")
    common(pr, need_dates=False)

    ag = sub.add_parser("aggregate", help="Monthly grid aggregation + coverage")
    common(ag)
    ag.add_argument("--res", type=float, default=0.25)
    ag.add_argument("--no-missing-dates", action="store_true")

    an = sub.add_parser("analyze", help="MODIS vs VIIRS overlap diagnostics")
    common(an)
    an.add_argument("--res", type=float, default=0.25)

    h = sub.add_parser("harmonize", help="Aqua-MODIS-equivalent VIIRS prototype: match, model, validate")
    h.add_argument("--regions", nargs="+", default=None, help="default: all harmonisation regions with data")
    h.add_argument("--start", type=_date, default=date(2018, 1, 1))
    h.add_argument("--end", type=_date, default=date(2021, 12, 31))
    h.add_argument("--max-dt", type=float, default=20.0, help="comparable overpass window (minutes)")
    h.add_argument("--n-boot", type=int, default=30)
    h.add_argument("--rebuild", action="store_true", help="rebuild matched datasets")

    r = sub.add_parser("run", help="download + process + aggregate (+ analyze)")
    common(r)
    r.add_argument("--chunk-days", type=int, default=5)
    r.add_argument("--res", type=float, default=0.25)
    r.add_argument("--skip-analysis", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO,
                  log_file=PROJECT_ROOT / "logs" / "firelens.log")
    needs_key = args.cmd in {"availability", "status", "download", "run"}
    settings = load_settings(require_key=needs_key)
    try:
        if args.cmd == "availability":
            print(pipeline.availability_table(settings, refresh=True).to_string(index=False))
        elif args.cmd == "status":
            print(json.dumps(FirmsClient(settings).key_status(), indent=2))
        elif args.cmd == "harmonize":
            from . import experiments, harmonization_figures
            from .catalog import HARMONIZATION_REGIONS
            from .matching import MatchConfig, build_region
            cfg = MatchConfig(max_dt_min=args.max_dt)
            regions = args.regions or [r for r in HARMONIZATION_REGIONS
                                       if (settings.processed_dir / "detections" / r / "VIIRS_SNPP_SP").exists()]
            if args.rebuild:
                for r in regions:
                    build_region(settings, resolve_region(r), args.start, args.end, cfg, use_cache=False)
            data = experiments.load_datasets(settings, regions, args.start, args.end, cfg)
            folds, monthly = experiments.run_experiments(data, n_boot=args.n_boot)
            out = experiments.save(settings, data, folds, monthly)
            harmonization_figures.make_all(settings, data, folds, monthly, out["summary"])
            experiments.save_final_models(settings, data, cfg, args.start, args.end, out["summary"])
            from .validation_status import save_status
            status = save_status(settings, out["summary"])
            print("validation status:", {r: v["status"] for r, v in status.items()})
            cols =["fold", "target", "verdict", "medAbsLog10_raw", "medAbsLog10_constant", "medAbsLog10_full",
                    "totalRatio_full", "pi90_cov_conformal"]
            print(out["summary"][cols].round(3).to_string(index=False))
        else:
            region = resolve_region(args.region)
            if args.cmd in ("download", "run"):
                for s in pipeline.run_download(settings, args.sources, region, args.start, args.end,
                                               args.chunk_days, getattr(args, "force", False)):
                    print(s)
            if args.cmd in ("process", "run"):
                for s in pipeline.run_process(settings, args.sources, region, args.start, args.end):
                    print(s)
            if args.cmd in ("aggregate", "run"):
                pipeline.run_aggregate(settings, args.sources, region, args.start, args.end, args.res,
                                       use_missing_dates=not getattr(args, "no_missing_dates", False))
            if args.cmd == "analyze" or (args.cmd == "run" and not args.skip_analysis
                                         and {"MODIS_SP", "VIIRS_SNPP_SP"} <= set(args.sources)):
                res = pipeline.run_analysis(settings, region, args.start, args.end, args.res)
                print(json.dumps(res, indent=2, default=pipeline._json_default)[:4000])
    except FirmsAPIError as e:
        logging.getLogger("firelens").error("%s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
