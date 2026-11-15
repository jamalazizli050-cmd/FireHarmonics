"""End-to-end pipeline steps used by the CLI and the notebook."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import acquisition, cleaning, coverage, grid, harmonization
from .catalog import Region, get_source
from .config import Settings
from .firms_client import FirmsClient
from .io import read_parquet, write_parquet
from .logging_utils import get_logger

log = get_logger(__name__)

DEFAULT_SOURCES = ["MODIS_SP", "VIIRS_SNPP_SP"]


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (date, pd.Timestamp, pd.Period)):
        return str(o)
    raise TypeError(type(o))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_json_default))


def availability_table(settings: Settings, client: FirmsClient | None = None, refresh: bool = False) -> pd.DataFrame:
    cache = settings.cache_dir / "data_availability.csv"
    if cache.exists() and not refresh:
        df = pd.read_csv(cache)
        df["min_date"] = pd.to_datetime(df["min_date"]).dt.date
        df["max_date"] = pd.to_datetime(df["max_date"]).dt.date
        return df
    client = client or FirmsClient(settings)
    df = client.data_availability("ALL")
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def availability_for(avail: pd.DataFrame, source_id: str) -> tuple[date, date] | None:
    row = avail[avail["data_id"] == source_id]
    if row.empty:
        return None
    return row["min_date"].iloc[0], row["max_date"].iloc[0]


def run_download(settings: Settings, sources: list[str], region: Region, start: date, end: date,
                 chunk_days: int = 5, force: bool = False) -> list[acquisition.DownloadSummary]:
    client = FirmsClient(settings)
    avail = availability_table(settings, client, refresh=True)
    out = []
    for sid in sources:
        get_source(sid)
        window = availability_for(avail, sid)
        if window:
            s, e = max(start, window[0]), min(end, window[1])
            if s > e:
                log.warning("%s has no data between %s and %s (available %s..%s)", sid, start, end, *window)
                continue
            if (s, e) != (start, end):
                log.info("%s: clipping request to availability %s..%s", sid, s, e)
        else:
            s, e = start, end
        out.append(acquisition.download(client, sid, region, s, e, chunk_days, availability=window, force=force))
    log.info("Requests made this run: %d", client.requests_made)
    return out


def run_process(settings: Settings, sources: list[str], region: Region, start: date | None = None,
                end: date | None = None) -> list[cleaning.ProcessSummary]:
    return [cleaning.process(settings, sid, region, start, end) for sid in sources]


def grid_dir(settings: Settings, region: Region) -> Path:
    return settings.processed_dir / "grid" / region.name


def run_aggregate(settings: Settings, sources: list[str], region: Region, start: date, end: date,
                  res: float = grid.DEFAULT_RES_DEG, use_missing_dates: bool = True) -> dict[str, pd.DataFrame]:
    avail = availability_table(settings)
    missing = coverage.fetch_missing_dates(settings) if use_missing_dates else None
    cov_frames, plat_frames, grid_frames = [], [], []
    for sid in sources:
        window = availability_for(avail, sid)
        daily = coverage.daily_coverage(settings, sid, region, start, end, missing,
                                        availability_end=window[1] if window else None)
        cov_frames.append(coverage.monthly_coverage(daily))
        plat_frames.append(coverage.monthly_coverage(daily, by_platform=True))
        det = cleaning.load_detections(settings, region, sid, start, end)
        if not det.empty:
            grid_frames.append(grid.monthly_grid(det, res))
    cov = pd.concat(cov_frames, ignore_index=True)
    cov_plat = pd.concat(plat_frames, ignore_index=True)
    g = pd.concat(grid_frames, ignore_index=True) if grid_frames else pd.DataFrame()
    out_dir = grid_dir(settings, region)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{res:g}deg"
    _to_parquet(cov, out_dir / f"coverage_monthly_{start}_{end}.parquet")
    _to_parquet(cov_plat, out_dir / f"coverage_monthly_by_platform_{start}_{end}.parquet")
    _to_parquet(g, out_dir / f"grid_monthly_{tag}.parquet")
    result = {"coverage": cov, "grid": g}
    if {"MODIS_SP", "VIIRS_SNPP_SP"} <= set(sources) and not g.empty:
        hf = grid.harmonization_frame(g, cov, res=res)
        _to_parquet(hf, out_dir / f"harmonization_prep_{tag}.parquet")
        result["harmonization_prep"] = hf
        # Aqua-only MODIS vs S-NPP VIIRS: matched (~13:30 / ~01:30) overpass times.
        aqua_cov = pd.concat([
            cov_plat[(cov_plat.source == "MODIS_SP") & (cov_plat.platform == "Aqua")],
            cov[cov.source == "VIIRS_SNPP_SP"]], ignore_index=True)
        hfa = grid.harmonization_frame(g, aqua_cov, res=res, modis_platforms=("Aqua",))
        _to_parquet(hfa, out_dir / f"harmonization_prep_aqua_{tag}.parquet")
        result["harmonization_prep_aqua"] = hfa
    log.info("Aggregated %s: %d grid rows, coverage rows %d", region.name, len(g), len(cov))
    return result


def _to_parquet(df: pd.DataFrame, path: Path) -> None:
    write_parquet(df, path)


def read_grid_table(path: Path) -> pd.DataFrame:
    df = read_parquet(path)
    if "month" in df:
        df["month"] = pd.PeriodIndex(df["month"], freq="M")
    return df


def run_analysis(settings: Settings, region: Region, start: date, end: date,
                 res: float = grid.DEFAULT_RES_DEG, max_dt_values=(10.0, 20.0, 30.0)) -> dict:
    """Overlap-period diagnostics. Writes JSON + cluster tables under reports/results."""
    out_dir = settings.reports_dir / "results"
    tag = f"{start}_{end}_{res:g}deg"
    gd = grid_dir(settings, region)
    results: dict = {"region": region.name, "start": start, "end": end, "grid_res_deg": res,
                     "status": "diagnostics only - no harmonised product"}
    for label, fname in (("terra_aqua", f"harmonization_prep_{tag}.parquet"),
                         ("aqua_only", f"harmonization_prep_aqua_{tag}.parquet")):
        hf = read_grid_table(gd / fname)
        for metric in ("n_detections", "n_conf_nominal_high", "frp_sum_mw"):
            results[f"cellmonth_{label}_{metric}"] = harmonization.cellmonth_comparison(hf, metric)

    modis = cleaning.load_detections(settings, region, "MODIS_SP", start, end)
    viirs = cleaning.load_detections(settings, region, "VIIRS_SNPP_SP", start, end)
    results["coincident"] = {}
    for dt in max_dt_values:
        cl = harmonization.coincident_clusters(modis, viirs, max_dt_min=dt)
        results["coincident"][f"max_dt_{dt:g}min"] = harmonization.cluster_summary(cl)
        _to_parquet(cl, settings.processed_dir / "clusters" / region.name / f"coincident_clusters_{start}_{end}_dt{dt:g}.parquet")
    write_json(out_dir / f"harmonization_diagnostics_{region.name}_{tag}.json", results)
    return results
