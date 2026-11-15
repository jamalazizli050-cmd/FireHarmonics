from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from conftest import make_response
from firelens import unified_calendar as uc

VIIRS_HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
                "bright_ti5,frp,daynight,type\n")
MODIS_HEADER = ("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
                "bright_t31,frp,daynight,type\n")
LAT, LON = 56.0, -3.5          # Scotland: covered only by the United_Kingdom file


def months(a, b):
    return pd.period_range(a, b, freq="M")


def seasonal(idx, peak=8, scale=10.0, seed=0):
    """Synthetic monthly counts peaking in `peak`."""
    rng = np.random.default_rng(seed)
    base = np.array([scale * np.exp(-((p.month - peak) % 12 if (p.month - peak) % 12 < 6 else (p.month - peak) % 12 - 12) ** 2 / 2)
                     for p in idx])
    return pd.Series(np.round(base * rng.uniform(0.8, 1.2, len(idx))), index=idx, dtype=float)


def test_calibration_recovers_ratio_and_beats_raw_viirs():
    idx = months("2012-01", "2021-12")
    v = seasonal(idx, scale=40)
    m = (v * 0.25).round()
    cal = uc.calibrate(m, v)
    assert cal["status"] == "ok"
    assert abs(cal["ratio"] - 0.25) < 0.02
    lo, hi = cal["ratio_ci95"]
    assert lo <= cal["ratio"] <= hi
    val = cal["validation"]
    assert val["improves_on_raw_viirs"] is True
    assert val["median_abs_pct_error_annual"] < 0.1 < val["median_abs_pct_error_annual_raw_viirs"]
    assert len(val["by_year"]) == 10


def test_calibration_refuses_with_too_little_activity():
    idx = months("2012-01", "2021-12")
    cal = uc.calibrate(pd.Series(0.0, index=idx), pd.Series(1.0, index=idx))
    assert cal["status"] == "insufficient_overlap" and "ratio" not in cal


def test_season_timing_and_current_year():
    idx = months("2005-01", "2025-12")
    s = seasonal(idx, peak=8)
    out = uc.season_timing(s, date(2026, 11, 15))
    assert out["status"] == "ok"
    assert out["typical"]["peak"] == "Aug"
    assert out["typical"]["onset"] in {"Jun", "Jul"} and out["typical"]["end"] in {"Sep", "Oct"}
    # an early year: activity two months ahead of normal
    early = s.copy()
    for p in months("2026-01", "2026-10"):
        early[p] = s[pd.Period(year=2025, month=min(p.month + 2, 12), freq="M")]
    out2 = uc.season_timing(early, date(2026, 11, 15))
    assert out2["current"]["onset_status"] == "earlier_than_usual"


class FakeClient:
    def __init__(self, n_rows):
        self.n = n_rows
        self.calls = []

    def area_csv(self, source, bbox, start, days):
        self.calls.append((source, start, days))
        rows = "".join(f"{LAT + 0.001 * i:.5f},{LON:.5f},330.1,0.4,0.4,{start},1300,N20,VIIRS,n,2.0NRT,290.0,3.1,D\n"
                       for i in range(self.n if not self.calls[1:] else 0))
        return "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n" + rows


def history(per_year: dict[int, int], today: date) -> pd.DataFrame:
    rows = []
    for y, n in per_year.items():
        d = pd.Timestamp(today.replace(year=y) - timedelta(days=3))
        rows += [{"acq_date": d, "latitude": LAT, "longitude": LON}] * n
    return pd.DataFrame(rows)


def test_current_conditions_categories(settings):
    today = date(2026, 11, 15)
    hist = history({y: 2 for y in range(2012, 2026)}, today)
    client = FakeClient(n_rows=9)
    now = uc.current_conditions(settings, client, LAT, LON, 25, today, hist, set())
    assert now["status"] == "ok" and now["detections"] == 9
    assert now["category"] == "highest_on_record"
    assert [c[0] for c in client.calls] == ["VIIRS_NOAA20_NRT"] * 3          # 14 days in 5-day requests
    quiet = uc.current_conditions(settings, FakeClient(0), LAT, LON, 25, today, pd.DataFrame(), set(),
                                  history_years=list(range(2012, 2026)))
    assert quiet["category"] == "quiet_as_usual"
    few = uc.current_conditions(settings, FakeClient(1), LAT, LON, 25, today, pd.DataFrame(), set(),
                                history_years=list(range(2012, 2026)))
    assert few["category"] == "isolated_detections" and few["explanation"].startswith("1 detection in")
    assert uc.current_conditions(settings, None, LAT, LON, 25, today, hist, set())["status"] == "unavailable"


class ArchiveSession:
    """Serves synthetic yearly country files: MODIS (Aqua + Terra) 2010-2021, VIIRS S-NPP 2012-2021."""

    def __init__(self):
        self.urls = []

    def get(self, url, stream=False, timeout=None):
        self.urls.append(url)
        import re
        y = int(re.search(r"_(\d{4})_", url).group(1)) if "_United_Kingdom" in url else None
        if y is None:
            return make_response(404, "Not Found")
        if "/modis/" in url and 2010 <= y <= 2021:
            text = MODIS_HEADER
            for mth in range(1, 13):
                n = 3 if mth in (7, 8, 9) else 0
                text += "".join(f"{LAT:.4f},{LON + 0.001 * i:.4f},320,1,1,{y}-{mth:02d}-10,1330,Aqua,MODIS,80,6.1NRT,290,10,D,0\n"
                                for i in range(n))
                text += f"{LAT:.4f},{LON:.4f},320,1,1,{y}-{mth:02d}-11,1030,Terra,MODIS,80,6.1NRT,290,10,D,0\n"
        elif "/viirs-snpp/" in url and 2012 <= y <= 2021:
            text = VIIRS_HEADER
            for mth in range(1, 13):
                n = 12 if mth in (7, 8, 9) else 0
                text += "".join(f"{LAT + 0.0005 * i:.5f},{LON:.5f},330,0.4,0.4,{y}-{mth:02d}-10,1330,N,VIIRS,n,2,290,3,D,0\n"
                                for i in range(n))
        else:
            return make_response(404, "Not Found")
        r = make_response(200, text)
        r.headers["Content-Length"] = str(len(text.encode()))
        r._content_consumed = True
        return r


def test_build_end_to_end_from_archives(settings):
    cal, now = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, client=None,
                        session=ArchiveSession())
    assert cal["status"] == "ok" and cal["countries"] == ["United_Kingdom"]
    assert abs(cal["ratio"] - 0.25) < 1e-9                      # 3 Aqua vs 12 VIIRS per active month; Terra excluded
    rows = {r["month"]: r for r in cal["monthly"]}
    assert rows["2011-08"]["source"] == "modis_aqua" and rows["2011-08"]["value"] == 3
    assert rows["2015-08"]["source"] == "viirs_snpp_scaled" and rows["2015-08"]["value"] == 3.0
    assert rows["2015-08"]["viirs_snpp"] == 12 and rows["2015-08"]["modis_aqua"] == 3
    assert rows["2005-08"]["value"] is None                      # no archive file: missing, not zero
    assert cal["season"]["typical"]["peak"] in {"Jul", "Aug", "Sep"}
    assert now["status"] == "unavailable"                        # no client -> no near-real-time data
    # cache-only re-run uses the local Parquet files and makes no requests
    s2 = ArchiveSession()
    cal2, _ = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=False, session=s2)
    assert cal2["ratio"] == cal["ratio"] and s2.urls == []


def test_open_ocean_is_reported_not_raised(settings):
    cal, now = uc.build(settings, -30.0, -30.0, 25, date(2026, 11, 15), None, session=ArchiveSession())
    assert cal["status"] == "unavailable" and now["status"] == "unavailable"
