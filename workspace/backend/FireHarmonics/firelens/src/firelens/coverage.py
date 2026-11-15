"""Temporal coverage bookkeeping.

Hotspot products only report detections, so "no detections" and "no data"
look identical in the raw files. Coverage indicators separate them using:

1. the acquisition manifest (which days were successfully retrieved), and
2. platform operating periods (e.g. MODIS_SP is Terra-only before Aqua's
   first data on 2002-07-04), and
3. optionally, FIRMS's published list of days with missing data per
   satellite (https://firms.modaps.eosdis.nasa.gov/api/missing_data/). That
   page loads its table from ``?action=fetch_data``; this is the page's own
   data feed rather than a formally versioned API, so it is treated as
   optional and cached locally.
"""
from __future__ import annotations

import re
from datetime import date

import pandas as pd
import requests

from .acquisition import cached_chunks
from .catalog import Region, get_source
from .config import Settings
from .io import read_parquet, write_parquet
from .logging_utils import get_logger

log = get_logger(__name__)

MISSING_DATA_URL = "https://firms.modaps.eosdis.nasa.gov/api/missing_data/?action=fetch_data"
SATELLITE_LABELS = {"Terra (MODIS)": "Terra", "Aqua (MODIS)": "Aqua", "Suomi NPP (VIIRS)": "Suomi NPP",
                    "NOAA-20 (VIIRS)": "NOAA-20", "NOAA-21 (VIIRS)": "NOAA-21"}


def parse_missing_dates_html(html: str) -> pd.DataFrame:
    """Parse the HTML table rows (date, satellite) pairs from the FIRMS feed."""
    cells = re.findall(r"<td>(.*?)</td>", html, flags=re.S)
    rows = []
    for i in range(0, len(cells) - 1, 2):
        d, sat = cells[i].strip(), cells[i + 1].strip()
        if not d or not sat:
            continue
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        rows.append((dd, SATELLITE_LABELS.get(sat, sat)))
    return pd.DataFrame(rows, columns=["date", "platform"]).drop_duplicates().sort_values(
        ["platform", "date"]).reset_index(drop=True)


def fetch_missing_dates(settings: Settings, refresh: bool = False,
                        session: requests.Session | None = None) -> pd.DataFrame | None:
    cache = settings.cache_dir / "firms_missing_dates.parquet"
    if cache.exists() and not refresh:
        return read_parquet(cache)
    try:
        resp = (session or requests).get(MISSING_DATA_URL, timeout=60)
        resp.raise_for_status()
        df = parse_missing_dates_html(resp.json()["html"])
    except Exception as e:  # optional input: degrade gracefully
        log.warning("Could not fetch FIRMS missing-dates table (%s); coverage will rely on the manifest only", e)
        return None
    write_parquet(df, cache)
    return df


def daily_coverage(settings: Settings, source_id: str, region: Region, start: date, end: date,
                   missing_dates: pd.DataFrame | None = None,
                   availability_end: date | None = None) -> pd.DataFrame:
    """One row per (date, platform) with retrieval/operation/missing indicators.

    ``availability_end`` (from the data_availability endpoint) marks days after
    the product's last available date as not operational.
    """
    src = get_source(source_id)
    days = pd.date_range(start, end, freq="D")
    retrieved = pd.Series(False, index=days)
    for ch, _ in cached_chunks(settings, source_id, region, start, end):
        retrieved.loc[pd.Timestamp(ch.start):pd.Timestamp(ch.end)] = True

    missing = set()
    if missing_dates is not None and len(missing_dates):
        missing = {(pd.Timestamp(d), p) for d, p in zip(missing_dates["date"], missing_dates["platform"])}

    rows = []
    for p in src.platforms:
        operational = days >= pd.Timestamp(p.start)
        if availability_end is not None:
            operational &= days <= pd.Timestamp(availability_end)
        flagged = [(d, p.name) in missing for d in days]
        rows.append(pd.DataFrame({"date": days, "source": source_id, "platform": p.name,
                                  "retrieved": retrieved.to_numpy(), "operational": operational,
                                  "firms_missing": flagged}))
    out = pd.concat(rows, ignore_index=True)
    out["observed"] = out["retrieved"] & out["operational"] & ~out["firms_missing"]
    return out


def monthly_coverage(daily: pd.DataFrame, by_platform: bool = False) -> pd.DataFrame:
    """Aggregate daily platform coverage to months.

    ``coverage_fraction`` = observed platform-days / operational platform-days.
    ``n_platforms`` records how many platforms contributed (a change in this
    number changes the sampling and must not be mistaken for a fire trend).
    """
    d = daily.copy()
    d["month"] = d["date"].dt.to_period("M")
    keys = ["source", "month"] + (["platform"] if by_platform else [])
    g = d.groupby(keys)
    out = pd.DataFrame({
        "days_in_month": g["date"].nunique(),
        "days_retrieved": d[d["retrieved"]].groupby(keys)["date"].nunique(),
        "platform_days_operational": g["operational"].sum(),
        "platform_days_observed": g["observed"].sum(),
        "platform_days_firms_missing": g["firms_missing"].sum(),
        "n_platforms": d[d["operational"]].groupby(keys)["platform"].nunique(),
    }).fillna(0)
    for c in out.columns:
        out[c] = out[c].astype(int)
    out["coverage_fraction"] = (out["platform_days_observed"] /
                                out["platform_days_operational"].where(out["platform_days_operational"] > 0))
    return out.reset_index()
