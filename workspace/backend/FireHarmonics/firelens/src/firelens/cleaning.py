"""Parsing, standardisation and validation of FIRMS hotspot records.

Principles
----------
* The raw CSV returned by NASA is kept untouched in ``data/raw``; every
  processed row carries ``raw_file`` + ``raw_row`` so it can be traced back.
* Nothing is silently dropped. Each check produces a boolean ``qc_*`` flag;
  ``qc_usable`` summarises the flags that make a record unsuitable for
  aggregation. Flag counts are written to a QC report.
* MODIS and VIIRS differ in schema and semantics (confidence encoding,
  brightness channels, pixel size). They are mapped to a common schema while
  keeping the sensor-specific meaning explicit (``confidence_pct`` exists only
  for MODIS, ``nominal_resolution_m`` and ``pixel_area_km2`` are retained).

Each record is a satellite *hotspot detection* (a pixel flagged as containing
one or more thermal anomalies), not a wildfire incident.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .acquisition import cached_chunks
from .catalog import OPTIONAL_RAW_COLUMNS, RAW_COLUMNS, BBox, Region, Source, get_source
from .config import Settings
from .io import read_parquet, write_parquet
from .logging_utils import get_logger

log = get_logger(__name__)

CONFIDENCE_CLASSES = pd.CategoricalDtype(["low", "nominal", "high"], ordered=True)
VIIRS_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}
# MODIS Collection 6/6.1 active-fire user guide: low 0-29, nominal 30-79, high 80-100.
MODIS_CONFIDENCE_BINS = [-0.5, 29.5, 79.5, 100.5]
# FIRMS 'type' (standard products only).
FIRE_TYPES = {0: "presumed vegetation fire", 1: "active volcano", 2: "other static land source",
              3: "offshore detection"}
# Generous plausibility bounds for along-scan / along-track pixel size (km).
# Values outside are flagged as suspect, not excluded.
PIXEL_SIZE_BOUNDS_KM = {"MODIS": (0.9, 5.0), "VIIRS": (0.3, 1.0)}

# qc_outside_window excludes because a row dated outside its request window
# either duplicates a row from the chunk covering that date or belongs to a
# day the coverage table does not count as retrieved.
QC_EXCLUDING = ["qc_parse_error", "qc_coord_invalid", "qc_time_invalid", "qc_platform_unknown",
                "qc_duplicate", "qc_outside_bbox", "qc_outside_window"]
QC_INFORMATIONAL = ["qc_frp_missing", "qc_frp_negative",
                    "qc_confidence_invalid", "qc_pixel_size_suspect", "qc_non_vegetation_type"]


class SchemaError(ValueError):
    pass


def parse_raw_csv(text_or_path: str | Path, source: Source) -> pd.DataFrame:
    """Read a raw FIRMS CSV as strings (no type coercion, nothing lost)."""
    if isinstance(text_or_path, Path):
        buf = text_or_path
    else:
        buf = io.StringIO(text_or_path)
    df = pd.read_csv(buf, dtype=str, keep_default_na=False)
    expected = RAW_COLUMNS[source.sensor]
    missing = [c for c in expected if c not in df.columns and c not in OPTIONAL_RAW_COLUMNS]
    if missing:
        raise SchemaError(f"{source.id}: raw CSV is missing columns {missing}; got {list(df.columns)}")
    return df


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def standardize(raw: pd.DataFrame, source: Source, raw_file: str = "") -> pd.DataFrame:
    """Map a raw FIRMS frame (strings) onto the common FireLens schema."""
    n = len(raw)
    out = pd.DataFrame(index=raw.index)
    out["source"] = source.id
    out["sensor"] = source.sensor
    out["processing"] = source.processing
    out["satellite_code"] = raw["satellite"]
    code_map = {c: p.name for p in source.platforms for c in p.codes}
    out["platform"] = raw["satellite"].map(code_map)

    out["latitude"] = _num(raw["latitude"])
    out["longitude"] = _num(raw["longitude"])

    acq_date = pd.to_datetime(raw["acq_date"], format="%Y-%m-%d", errors="coerce")
    hhmm = _num(raw["acq_time"])
    hh, mm = hhmm // 100, hhmm % 100
    time_ok = hhmm.notna() & (hhmm == hhmm.round()) & hh.between(0, 23) & mm.between(0, 59)
    out["acq_date"] = acq_date
    out["acq_time_hhmm"] = hhmm.astype("Int16")
    dt = acq_date + pd.to_timedelta(hh * 60 + mm, unit="min")
    out["acq_datetime_utc"] = dt.where(time_ok & acq_date.notna()).dt.tz_localize("UTC")

    out["brightness_k"] = _num(raw[source.brightness_col])
    out["brightness_aux_k"] = _num(raw[source.brightness_aux_col])
    out["scan_km"] = _num(raw["scan"])
    out["track_km"] = _num(raw["track"])
    out["pixel_area_km2"] = out["scan_km"] * out["track_km"]
    out["frp_mw"] = _num(raw["frp"])
    out["daynight"] = raw["daynight"].where(raw["daynight"].isin(["D", "N"]))

    conf = raw["confidence"].str.strip()
    out["confidence_raw"] = conf
    if source.sensor == "MODIS":
        pct = _num(conf)
        out["confidence_pct"] = pct
        cls = pd.cut(pct, MODIS_CONFIDENCE_BINS, labels=CONFIDENCE_CLASSES.categories)
        out["confidence_class"] = cls.astype(CONFIDENCE_CLASSES)
        conf_invalid = pct.isna() | ~pct.between(0, 100)
    else:
        out["confidence_pct"] = np.nan
        out["confidence_class"] = conf.str.lower().map(VIIRS_CONFIDENCE).astype(CONFIDENCE_CLASSES)
        conf_invalid = out["confidence_class"].isna()

    out["fire_type"] = (_num(raw["type"]) if "type" in raw.columns else pd.Series(np.nan, index=raw.index)
                        ).astype("Int8")
    out["version"] = raw["version"]
    out["instrument"] = raw["instrument"]
    out["nominal_resolution_m"] = np.int16(source.nominal_resolution_m)
    out["raw_file"] = raw_file
    out["raw_row"] = np.arange(n, dtype=np.int32)

    # Parse errors: a non-empty raw value that could not be converted.
    parse_err = np.zeros(n, dtype=bool)
    for raw_col, std_col in [("latitude", "latitude"), ("longitude", "longitude"),
                             ("frp", "frp_mw"), ("scan", "scan_km"), ("track", "track_km")]:
        parse_err |= (raw[raw_col].str.strip() != "").to_numpy() & out[std_col].isna().to_numpy()
    out["qc_parse_error"] = parse_err
    out["qc_time_invalid"] = (acq_date.isna() | ~time_ok).to_numpy()
    out["qc_confidence_invalid"] = conf_invalid.to_numpy()
    return out


def validate(df: pd.DataFrame, bbox: BBox | None = None,
             window: tuple[date, date] | None = None) -> pd.DataFrame:
    """Add QC flags in place-safe manner and return the frame."""
    df = df.copy()
    lat, lon = df["latitude"], df["longitude"]
    df["qc_coord_invalid"] = (lat.isna() | lon.isna() | ~lat.between(-90, 90) | ~lon.between(-180, 180)).to_numpy()
    if bbox is not None:
        tol = 1e-6
        inside = lat.between(bbox.south - tol, bbox.north + tol) & lon.between(bbox.west - tol, bbox.east + tol)
        df["qc_outside_bbox"] = (~df["qc_coord_invalid"] & ~inside).to_numpy()
    else:
        df["qc_outside_bbox"] = False
    if window is not None:
        d = df["acq_date"].dt.date
        df["qc_outside_window"] = (df["acq_date"].notna() & ((d < window[0]) | (d > window[1]))).to_numpy()
    elif "qc_outside_window" not in df:
        df["qc_outside_window"] = False
    for flag in ("qc_parse_error", "qc_time_invalid", "qc_confidence_invalid"):
        if flag not in df:
            df[flag] = False
    df["qc_platform_unknown"] = df["platform"].isna().to_numpy()
    df["qc_frp_missing"] = df["frp_mw"].isna().to_numpy()
    df["qc_frp_negative"] = (df["frp_mw"] < 0).fillna(False).to_numpy()
    lo_hi = df["sensor"].map(PIXEL_SIZE_BOUNDS_KM)
    lo = lo_hi.map(lambda t: t[0]).astype(float)
    hi = lo_hi.map(lambda t: t[1]).astype(float)
    df["qc_pixel_size_suspect"] = (~df["scan_km"].between(lo, hi) | ~df["track_km"].between(lo, hi)).to_numpy()
    df["qc_non_vegetation_type"] = (df["fire_type"].notna() & (df["fire_type"] != 0)).fillna(False).to_numpy(dtype=bool)

    # Exact duplicates of the observation content (same sensor/platform,
    # location, time and measured values). The first occurrence is kept usable.
    key = ["source", "satellite_code", "latitude", "longitude", "acq_date", "acq_time_hhmm",
           "brightness_k", "brightness_aux_k", "scan_km", "track_km", "frp_mw", "confidence_raw",
           "daynight", "fire_type", "version"]
    df["qc_duplicate"] = df.duplicated(subset=key, keep="first").to_numpy()

    df["qc_usable"] = ~df[QC_EXCLUDING].any(axis=1)
    return df


def qc_summary(df: pd.DataFrame) -> dict:
    flags = QC_EXCLUDING + QC_INFORMATIONAL
    return {
        "n_records": int(len(df)),
        "n_usable": int(df["qc_usable"].sum()) if len(df) else 0,
        "flags": {f: int(df[f].sum()) for f in flags if f in df},
        "excluding_flags": QC_EXCLUDING,
    }


# --------------------------------------------------------------------- storage
def detections_dir(settings: Settings, region: Region, source_id: str) -> Path:
    return settings.processed_dir / "detections" / region.name / source_id


@dataclass
class ProcessSummary:
    source: str
    region: str
    months_written: int
    qc: dict


def process(settings: Settings, source_id: str, region: Region, start: date | None = None,
            end: date | None = None) -> ProcessSummary:
    """Standardise + validate cached raw chunks and write monthly Parquet files.

    Output: ``data/processed/detections/<region>/<source>/<YYYY>/<YYYY-MM>.parquet``.
    Rows are routed to the month of their acquisition date; rows with an
    invalid date or a date outside their chunk go to the chunk's start month
    (flagged), so every raw row lands in exactly one file.
    """
    src = get_source(source_id)
    # Work in whole months so a monthly file is never overwritten with a partial month.
    if start:
        start = pd.Period(start, "M").start_time.date()
    if end:
        end = pd.Period(end, "M").end_time.date()
    chunks = cached_chunks(settings, source_id, region, start, end)
    if not chunks:
        log.warning("No cached chunks for %s / %s", source_id, region.name)
        return ProcessSummary(source_id, region.name, 0, qc_summary(pd.DataFrame()))

    # Process one calendar month at a time so memory stays bounded for global
    # multi-year runs. A chunk spanning two months is parsed for each of them.
    first = pd.Period(start or chunks[0][0].start, "M")
    last = pd.Period(end or chunks[-1][0].end, "M")
    out_dir = detections_dir(settings, region, source_id)
    qc_path = settings.processed_dir / "qc" / f"{region.name}_{source_id}.json"
    monthly_qc: dict = json.loads(qc_path.read_text()).get("by_month", {}) if qc_path.exists() else {}
    written = 0
    parsed: dict[str, pd.DataFrame] = {}
    for m in pd.period_range(first, last, freq="M"):
        m_start, m_end = m.start_time.date(), m.end_time.date()
        frames = []
        for ch, entry in chunks:
            if ch.end < m_start or ch.start > m_end:
                continue
            if ch.id not in parsed:
                raw = parse_raw_csv(settings.raw_dir / entry.path, src)
                std = standardize(raw, src, raw_file=entry.path)
                std = validate(std, bbox=region.bbox, window=(ch.start, ch.end))
                # Route each row to its acquisition month when the chunk covers that
                # month; otherwise (invalid date, or a date outside the chunk) to the
                # chunk's start month, where it is kept and flagged.
                acq_m = std["acq_date"].dt.to_period("M")
                covered = acq_m.isin([pd.Period(ch.start, "M"), pd.Period(ch.end, "M")])
                std["_route_month"] = acq_m.where(covered, pd.Period(ch.start, "M"))
                parsed[ch.id] = std
            std = parsed[ch.id]
            frames.append(std[std["_route_month"] == m])
        # Drop parsed chunks that cannot overlap later months.
        parsed = {k: v for k, v in parsed.items() if date.fromisoformat(k[:10]) + pd.Timedelta(days=5) > m_end}
        frames = [f for f in frames if len(f)]
        if not frames:
            continue
        df = pd.concat(frames, ignore_index=True).drop(columns="_route_month")
        # Re-validate the month as a whole so duplicates across chunks are found.
        df = validate(df, bbox=region.bbox)
        write_parquet(df, out_dir / f"{m.year:04d}" / f"{m}.parquet")
        monthly_qc[str(m)] = qc_summary(df)
        written += 1

    qc = _combine_qc(monthly_qc)
    qc_path.parent.mkdir(parents=True, exist_ok=True)
    qc_path.write_text(json.dumps({**qc, "by_month": monthly_qc}, indent=2))
    log.info("Processed %s / %s: %d records (%d usable) in %d monthly files (all processed months)",
             source_id, region.name, qc["n_records"], qc["n_usable"], len(monthly_qc))
    return ProcessSummary(source_id, region.name, written, qc)


def _combine_qc(monthly: dict) -> dict:
    total = {"n_records": 0, "n_usable": 0, "flags": {}, "excluding_flags": QC_EXCLUDING}
    for q in monthly.values():
        total["n_records"] += q["n_records"]
        total["n_usable"] += q["n_usable"]
        for f, n in q.get("flags", {}).items():
            total["flags"][f] = total["flags"].get(f, 0) + n
    return total


def load_detections(settings: Settings, region: Region, source_ids: list[str] | str,
                    start: date | None = None, end: date | None = None,
                    columns: list[str] | None = None, usable_only: bool = False) -> pd.DataFrame:
    """Load processed detections, reading only the monthly files needed."""
    if isinstance(source_ids, str):
        source_ids = [source_ids]
    frames = []
    for sid in source_ids:
        base = detections_dir(settings, region, sid)
        for path in sorted(base.glob("*/*.parquet")):
            p = pd.Period(path.stem, "M")
            if (start and p < pd.Period(start, "M")) or (end and p > pd.Period(end, "M")):
                continue
            frames.append(read_parquet(path, columns=columns))
    if not frames:
        return pd.DataFrame(columns=columns) if columns else pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if start is not None and "acq_date" in df:
        df = df[~(df["acq_date"] < pd.Timestamp(start))]
    if end is not None and "acq_date" in df:
        df = df[~(df["acq_date"] > pd.Timestamp(end))]
    if usable_only:
        df = df[df["qc_usable"]]
    return df.reset_index(drop=True)
