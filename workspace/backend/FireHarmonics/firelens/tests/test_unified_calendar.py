from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from conftest import make_response
from firelens import unified_calendar as uc

VIIRS_HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
                "bright_ti5,frp,daynight,type\n")
MODIS_HEADER = ("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
                "bright_t31,frp,daynight,type\n")
LAT, LON = 56.0, -3.5          # Scotland: covered only by the United_Kingdom file


@pytest.fixture(autouse=True)
def no_prior_table(monkeypatch):
    """Tests use the built-in default priors, not whatever table the developer has built."""
    monkeypatch.setattr(uc, "_prior_table", lambda: None)


def months(a, b):
    return pd.period_range(a, b, freq="M")


def det(rows):
    """rows: (date, daynight, lat, lon)"""
    return pd.DataFrame([{"acq_date": pd.Timestamp(d), "daynight": dn, "latitude": la, "longitude": lo,
                          "fire_type": 0, "confidence_class": "nominal", "satellite_code": "N"} for d, dn, la, lo in rows])


def units_frame(idx, d, n=0.0):
    return pd.DataFrame({"D": np.asarray(d, dtype=float), "N": np.full(len(idx), n, dtype=float)}, index=idx)


def seasonal(idx, peak=8, scale=10.0, seed=0):
    rng = np.random.default_rng(seed)
    base = np.array([scale * np.exp(-((p.month - peak) % 12 if (p.month - peak) % 12 < 6 else (p.month - peak) % 12 - 12) ** 2 / 2)
                     for p in idx])
    return pd.Series(np.round(base * rng.uniform(0.8, 1.2, len(idx))), index=idx, dtype=float)


# ------------------------------------------------------------------ unit
def test_active_units_collapse_pixels_of_one_cell_and_keep_days_and_strata_apart():
    d = det([("2015-08-10", "D", 56.0001, -3.4999), ("2015-08-10", "D", 56.0004, -3.4996),   # same 1-km cell
             ("2015-08-10", "N", 56.0001, -3.4999),                                         # night: separate
             ("2015-08-11", "D", 56.0001, -3.4999),                                         # next day: new unit
             ("2015-08-10", "D", 56.0501, -3.4999)])                                        # another cell
    u = uc.active_units(d, months("2015-08", "2015-08"))
    assert u.loc[pd.Period("2015-08", "M"), "D"] == 3 and u.loc[pd.Period("2015-08", "M"), "N"] == 1
    assert uc.unit_totals(d).tolist() == [3.0, 1.0]


def test_quality_drops_low_confidence_non_vegetation_and_terra():
    d = det([("2015-08-10", "D", 56.0, -3.5)] * 4)
    d.loc[1, "confidence_class"] = "low"
    d.loc[2, "fire_type"] = 2
    d.loc[3, "satellite_code"] = "Terra"
    assert len(uc.quality(d)) == 2 and len(uc.quality(d, aqua_only=True)) == 0


# ------------------------------------------------------------------ partial pooling
def test_fit_link_pools_toward_the_prior_where_local_data_are_sparse():
    idx = months("2012-01", "2021-12")
    prior = {"D": 0.5, "N": 0.2}
    empty = uc.fit_link(units_frame(idx, 0), units_frame(idx, 0), uc.AQUA_FIT_YEARS, prior)
    assert empty["theta"].tolist() == [0.5, 0.2]                                  # nothing local: the region
    tgt = units_frame(idx, seasonal(idx, scale=400))
    ref = units_frame(idx, (tgt["D"] * 0.8).round())
    rich = uc.fit_link(ref, tgt, uc.AQUA_FIT_YEARS, prior)
    assert abs(rich["theta"][0] - 0.8) < 0.01                                     # lots of local data: local ratio
    sparse = uc.fit_link(units_frame(idx, (seasonal(idx, scale=1) > 0) * 1.0 * 0), units_frame(idx, seasonal(idx, scale=1)),
                         uc.AQUA_FIT_YEARS, prior)
    assert 0 < sparse["theta"][0] < 0.5                                           # between zero-local and prior
    lo, hi = np.percentile(rich["boot"][:, 0], [5, 95])
    assert lo <= rich["theta"][0] <= hi


def test_forward_test_beats_raw_and_covers_held_out_years():
    idx = months("2012-01", "2021-12")
    rng = np.random.default_rng(3)
    tgt_d = seasonal(idx, scale=60, seed=1)
    tgt = units_frame(idx, tgt_d)
    ref = units_frame(idx, rng.poisson(tgt_d.to_numpy() * 0.4))
    out = uc.forward_test("aqua_snpp", ref, tgt, ref.sum(axis=1) * 3, tgt.sum(axis=1) * 9, LAT, LON)
    assert out["status"] == "ok" and out["train_years"] == [2012, 2013, 2014, 2015, 2016]
    chain, raw = out["methods"]["continuity_chain"], out["methods"]["raw_target"]
    assert chain["median_abs_pct_error_annual"] < 0.15 < raw["median_abs_pct_error_annual"]
    assert out["interval_90_coverage"] >= 0.75
    assert len(out["by_year"]) == 5


def test_anomalies_use_the_fixed_baseline_and_need_a_clear_margin():
    rows = [{"month": f"{y}-08", "value": 10.0 + (y % 3), "interval_90": [8.0, 14.0]} for y in range(2003, 2022)]
    rows.append({"month": "2024-08", "value": 40.0, "interval_90": [30.0, 50.0]})
    rows.append({"month": "2025-08", "value": 12.5, "interval_90": [5.0, 20.0]})
    uc.add_anomalies(rows)
    assert rows[-2]["anomaly"] == "high" and rows[-2]["percentile"] == 100.0
    assert rows[-1]["anomaly"] is None and rows[-1]["baseline_median"] == 11.0


# ------------------------------------------------------------------ season
def test_season_timing_and_current_year():
    idx = months("2005-01", "2025-12")
    s = seasonal(idx, peak=8)
    out = uc.season_timing(s, date(2026, 11, 15))
    assert out["status"] == "ok"
    assert out["typical"]["peak"] == "Aug"
    assert out["typical"]["onset"] in {"Jun", "Jul"} and out["typical"]["end"] in {"Sep", "Oct"}
    early = s.copy()
    for p in months("2026-01", "2026-10"):
        early[p] = s[pd.Period(year=2025, month=min(p.month + 2, 12), freq="M")]
    out2 = uc.season_timing(early, date(2026, 11, 15))
    assert out2["current"]["onset_status"] == "earlier_than_usual"


# ------------------------------------------------------------------ now
class FakeClient:
    def __init__(self, n_rows):
        self.n = n_rows
        self.calls = []

    def area_csv(self, source, bbox, start, days):
        self.calls.append((source, start, days))
        rows = "".join(f"{LAT + 0.02 * i:.5f},{LON:.5f},330.1,0.4,0.4,{start},1300,N20,VIIRS,n,2.0NRT,290.0,3.1,D\n"
                       for i in range(self.n if not self.calls[1:] else 0))
        return "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n" + rows


def history(per_year: dict[int, int], today: date) -> pd.DataFrame:
    rows = []
    for y, n in per_year.items():
        d = (today.replace(year=y) - timedelta(days=3)).isoformat()
        rows += [(d, "D", LAT + 0.02 * i, LON) for i in range(n)]
    return det(rows)


def test_current_conditions_on_the_snpp_scale(settings):
    today = date(2026, 11, 15)
    hist = history({y: 2 for y in range(2012, 2026)}, today)
    years = list(range(2012, 2026))
    client = FakeClient(n_rows=9)
    now = uc.current_conditions(settings, client, LAT, LON, 25, today, hist, set(), years, np.array([1.0, 1.0]))
    assert now["status"] == "ok" and now["detections"] == 9 and now["active_cells"] == 9
    assert now["category"] == "highest_on_record" and now["history_median"] == 2
    assert [c[0] for c in client.calls] == ["VIIRS_NOAA20_NRT"] * 3          # 14 days in 5-day requests
    half = uc.current_conditions(settings, FakeClient(9), LAT, LON, 25, today, hist, set(), years, np.array([0.2, 1.0]))
    assert half["snpp_equivalent"] == pytest.approx(1.8)                    # bridge ratio applied
    quiet = uc.current_conditions(settings, FakeClient(0), LAT, LON, 25, today, det([]), set(), years, None)
    assert quiet["category"] == "quiet_as_usual"
    few = uc.current_conditions(settings, FakeClient(1), LAT, LON, 25, today, det([]), set(), years, None)
    assert few["category"] == "isolated_detections"
    assert uc.current_conditions(settings, None, LAT, LON, 25, today, hist, set(), years, None)["status"] == "unavailable"


# ------------------------------------------------------------------ end to end
class ArchiveSession:
    """Synthetic yearly country files: MODIS (Aqua + Terra) 2010-2021, VIIRS S-NPP 2012-2021, NOAA-20 2019-2021."""

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
                n = 3 if mth in (7, 8, 9) else 0      # three 1-km pixels of one cell
                text += "".join(f"{LAT:.4f},{LON + 0.001 * i:.4f},320,1,1,{y}-{mth:02d}-10,1330,Aqua,MODIS,80,6.1NRT,290,10,D,0\n"
                                for i in range(n))
                text += f"{LAT:.4f},{LON:.4f},320,1,1,{y}-{mth:02d}-11,1030,Terra,MODIS,80,6.1NRT,290,10,D,0\n"
        elif "/viirs-snpp/" in url and 2012 <= y <= 2021:
            text = VIIRS_HEADER
            for mth in range(1, 13):
                n = 12 if mth in (7, 8, 9) else 0     # twelve 375-m pixels of the same cell
                text += "".join(f"{LAT + 0.0005 * i:.5f},{LON:.5f},330,0.4,0.4,{y}-{mth:02d}-10,1330,N,VIIRS,n,2,290,3,D,0\n"
                                for i in range(n))
        elif "/viirs-jpss1/" in url and 2019 <= y <= 2021:
            text = VIIRS_HEADER
            for mth in range(1, 13):
                n = 12 if mth in (7, 8, 9) else 0     # NOAA-20 sees the same fire in two cells
                text += "".join(f"{LAT + 0.0005 * i + (0.01 if i % 2 else 0):.5f},{LON:.5f},330,0.4,0.4,{y}-{mth:02d}-10,1330,N20,VIIRS,n,2,290,3,D,0\n"
                                for i in range(n))
        else:
            return make_response(404, "Not Found")
        r = make_response(200, text)
        r.headers["Content-Length"] = str(len(text.encode()))
        r._content_consumed = True
        return r


def test_build_end_to_end_chain_from_archives(settings):
    cal, now = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, client=None,
                        session=ArchiveSession(), kappa=1e-9)
    assert cal["status"] == "ok" and cal["countries"] == ["United_Kingdom"] and cal["method"] == "continuity_chain"
    link1, link2 = cal["chain"]
    assert link1["ratio"]["D"] == pytest.approx(1.0)              # 3 Aqua pixels vs 12 S-NPP pixels: both one cell
    assert link2["ratio"]["D"] == pytest.approx(0.5)              # NOAA-20 splits the fire into two cells
    rows = {r["month"]: r for r in cal["monthly"]}
    assert rows["2011-08"]["source"] == "modis_aqua" and rows["2011-08"]["value"] == 1     # Terra excluded
    assert rows["2015-08"]["source"] == "viirs_snpp_scaled" and rows["2015-08"]["value"] == 1.0
    assert rows["2015-08"]["viirs_snpp"] == 1 and rows["2015-08"]["modis_aqua"] == 1
    assert rows["2005-08"]["value"] is None                      # no archive file: missing, not zero
    assert rows["2015-08"]["interval_90"] is not None
    assert cal["season"]["typical"]["peak"] in {"Jul", "Aug", "Sep"}
    assert cal["validation"]["aqua_snpp"]["status"] in {"ok", "too_little_activity"}
    assert now["status"] == "unavailable"                        # no client -> no near-real-time data
    s2 = ArchiveSession()                                        # cache-only re-run: local Parquet, no requests
    cal2, _ = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=False, session=s2, kappa=1e-9)
    assert cal2["chain"][0]["ratio"] == link1["ratio"] and s2.urls == []


def test_sparse_location_falls_back_to_the_regional_prior(settings):
    cal, _ = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, session=ArchiveSession())
    w = cal["chain"][0]["local_weight"]["D"]
    assert 0 < w < 1 and cal["chain"][0]["prior_scope"]["D"] == "built-in default"
    assert cal["chain"][0]["local_weight"]["N"] == 0               # no night data at all: the prior alone


def test_open_ocean_is_reported_not_raised(settings):
    cal, now = uc.build(settings, -30.0, -30.0, 25, date(2026, 11, 15), None, session=ArchiveSession())
    assert cal["status"] == "unavailable" and now["status"] == "unavailable"


def test_missing_archives_do_not_block_the_analysis(settings, monkeypatch):
    started = []
    monkeypatch.setattr(uc, "prefetch_in_background", lambda st, jobs: started.extend(jobs))
    cal, now = uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, client=None)
    assert cal["status"] == "preparing" and "background" in cal["explanation"]
    assert ("modis", "United_Kingdom", 2002) in started and ("viirs-snpp", "United_Kingdom", 2025) in started
    assert ("viirs-jpss1", "United_Kingdom", 2018) in started
    assert now["status"] == "unavailable"


def test_404_years_are_remembered(settings):
    uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, session=ArchiveSession())
    from firelens import country_archive as ca
    assert ca.is_resolved(settings, "United_Kingdom", 2003, "modis")          # 404 recorded
    assert ca.is_resolved(settings, "United_Kingdom", 2018, "viirs-jpss1")
    s2 = ArchiveSession()
    uc.build(settings, LAT, LON, 25, date(2026, 11, 15), None, allow_download=True, session=s2)
    assert s2.urls == []                                                      # nothing re-requested


def test_prior_table_restricts_to_requested_years(monkeypatch):
    table = {"grid_deg": 5, "cells": {"-1,11": {"aqua": {"2012": [500, 100], "2020": [5000, 1000]},
                                                "viirs-snpp": {"2012": [1000, 1000], "2020": [1000, 1000]}}}}
    monkeypatch.setattr(uc, "_prior_table", lambda: table)
    early, scope = uc.prior_ratio(LAT, LON, "aqua_snpp", [2012])
    assert early["D"] == pytest.approx(0.5) and scope["D"] == "5 deg cell"
    both, _ = uc.prior_ratio(LAT, LON, "aqua_snpp", [2012, 2020])
    assert both["D"] == pytest.approx(2.75)
