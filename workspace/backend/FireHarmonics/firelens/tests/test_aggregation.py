from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import MODIS_CSV, VIIRS_CSV
from firelens.catalog import REGIONS, get_source
from firelens.cleaning import parse_raw_csv, standardize, validate
from firelens.coverage import daily_coverage, monthly_coverage, parse_missing_dates_html
from firelens.grid import cell_center, cell_index, grid_shape, harmonization_frame, monthly_grid
from firelens.harmonization import cellmonth_comparison, cluster_summary, coincident_clusters

CA = REGIONS["california"]


def det(csv, sid):
    src = get_source(sid)
    return validate(standardize(parse_raw_csv(csv, src), src), bbox=CA.bbox)


def test_grid_indices_and_edges():
    assert grid_shape(0.25) == (720, 1440)
    with pytest.raises(ValueError):
        grid_shape(0.7)
    row, col, cid = cell_index([-90, 0, 90, 36.01], [-180, 0, 180, -121.51], 0.25)
    assert row.tolist()[:3] == [0, 360, 719] and col.tolist()[:3] == [0, 720, 1439]
    lat, lon = cell_center(cid, 0.25)
    assert np.all(np.abs(lat - np.array([-90, 0, 90, 36.01])) <= 0.125 + 1e-9)
    assert lat[3] == pytest.approx(36.125) and lon[3] == pytest.approx(-121.625)


def test_monthly_grid_counts_and_excludes_unusable():
    d = det(MODIS_CSV, "MODIS_SP")
    d.loc[2, "qc_usable"] = False
    g = monthly_grid(d, 0.25)
    assert g["n_detections"].sum() == 2
    row = g.iloc[0]
    assert row["n_detections"] == 2 and row["n_conf_high"] == 1 and row["n_conf_nominal_high"] == 2
    assert row["frp_sum_mw"] == pytest.approx(46.2 + 14.9)
    assert row["n_active_days"] == 1 and row["n_overpass_granules"] == 1
    assert row["harmonization_status"] == "preliminary_aggregation_unharmonized"


def test_harmonization_frame_separates_zero_from_missing():
    g = pd.concat([monthly_grid(det(MODIS_CSV, "MODIS_SP")), monthly_grid(det(VIIRS_CSV, "VIIRS_SNPP_SP"))])
    cov = pd.DataFrame({
        "source": ["MODIS_SP", "MODIS_SP", "VIIRS_SNPP_SP", "VIIRS_SNPP_SP"],
        "month": pd.PeriodIndex(["2020-09", "2020-10"] * 2, freq="M"),
        "coverage_fraction": [1.0, 1.0, 1.0, 0.2],
    })
    hf = harmonization_frame(g, cov).set_index(["cell_id", "month"])
    modis_cell = cell_index([36.0234], [-121.5115])[2][0]
    sep, octo = pd.Period("2020-09", "M"), pd.Period("2020-10", "M")
    assert hf.loc[(modis_cell, sep), "modis_n_detections"] == 3
    assert hf.loc[(modis_cell, sep), "viirs_n_detections"] == 0     # observed, nothing detected
    assert hf.loc[(modis_cell, octo), "modis_n_detections"] == 0
    assert np.isnan(hf.loc[(modis_cell, octo), "viirs_n_detections"])  # not observed
    assert not hf.loc[(modis_cell, octo), "both_observed"]


def test_missing_dates_parser():
    html = ("<tr><td>2026-07-16</td><td>Suomi NPP (VIIRS)</td><td>2024-03-06</td><td>NOAA-21 (VIIRS)</td></tr>"
            "<tr><td>2002-07-30</td><td>Aqua (MODIS)</td><td></td><td></td></tr>")
    df = parse_missing_dates_html(html)
    assert set(map(tuple, df[["platform"]].to_numpy())) == {("Suomi NPP",), ("NOAA-21",), ("Aqua",)}
    assert date(2002, 7, 30) in set(df["date"])


def test_coverage_accounts_for_platform_start_and_missing_days(settings):
    from conftest import FakeSession, make_response
    from firelens.acquisition import download
    from firelens.firms_client import FirmsClient
    c = FirmsClient(settings, session=FakeSession(route=lambda u: make_response(200, MODIS_CSV)), sleep=lambda s: None)
    download(c, "MODIS_SP", CA, date(2002, 7, 1), date(2002, 7, 31))
    missing = pd.DataFrame({"date": [date(2002, 7, 30)], "platform": ["Aqua"]})
    daily = daily_coverage(settings, "MODIS_SP", CA, date(2002, 7, 1), date(2002, 7, 31), missing)
    m = monthly_coverage(daily).iloc[0]
    # Terra: 31 operational days; Aqua: from 2002-07-04 = 28 days, one flagged missing.
    assert m["platform_days_operational"] == 31 + 28
    assert m["platform_days_observed"] == 31 + 27
    assert m["n_platforms"] == 2
    assert m["coverage_fraction"] == pytest.approx(58 / 59)


def _pt(sensor, lat, lon, t, frp, scan, platform):
    return dict(latitude=lat, longitude=lon, acq_datetime_utc=pd.Timestamp(t, tz="UTC"), scan_km=scan,
                track_km=scan, frp_mw=frp, daynight="D", platform=platform, qc_usable=True, sensor=sensor)


def test_coincident_clusters():
    modis = pd.DataFrame([
        _pt("MODIS", 37.0, -120.0, "2020-09-01 21:00", 50, 1.0, "Aqua"),      # co-detected
        _pt("MODIS", 37.5, -120.0, "2020-09-01 21:00", 30, 1.0, "Aqua"),      # MODIS only
        _pt("MODIS", 37.0, -120.0, "2020-09-01 18:00", 30, 1.0, "Terra"),     # Terra ignored
    ])
    viirs = pd.DataFrame([
        _pt("VIIRS", 37.002, -120.002, "2020-09-01 21:10", 20, 0.4, "Suomi NPP"),
        _pt("VIIRS", 37.005, -120.000, "2020-09-01 21:10", 25, 0.4, "Suomi NPP"),
        _pt("VIIRS", 36.8, -120.0, "2020-09-01 21:10", 3, 0.4, "Suomi NPP"),  # VIIRS only (small)
        _pt("VIIRS", 37.0, -120.0, "2020-09-02 03:00", 5, 0.4, "Suomi NPP"),  # far in time: ineligible
    ])
    cl = coincident_clusters(modis, viirs, max_dt_min=20)
    cats = cl.set_index(["category", "eligible"]).index.value_counts().to_dict()
    assert cats[("both", True)] == 1 and cats[("modis_only", True)] == 1
    assert cats[("viirs_only", True)] == 1 and cats[("viirs_only", False)] == 1
    both = cl[cl.category == "both"].iloc[0]
    assert both.n_modis == 1 and both.n_viirs == 2 and both.frp_viirs_mw == 45
    assert both.dt_viirs_minus_modis_min == pytest.approx(10)
    s = cluster_summary(cl)
    assert s["p_viirs_given_modis"] == pytest.approx(0.5)
    assert s["p_modis_given_viirs"] == pytest.approx(0.5)
    # With a 5-minute window nothing is coincident.
    cl5 = coincident_clusters(modis, viirs, max_dt_min=5)
    assert (cl5.category == "both").sum() == 0


def test_cellmonth_comparison_basic():
    months = pd.period_range("2020-01", periods=6, freq="M")
    hf = pd.DataFrame({
        "month": np.repeat(months, 2), "both_observed": True,
        "modis_n_detections": [1, 2, 3, 4, 5, 6, 0, 1, 2, 3, 4, 5],
        "viirs_n_detections": [4, 8, 12, 16, 20, 24, 3, 4, 8, 12, 16, 20],
    })
    r = cellmonth_comparison(hf, "n_detections", n_boot=50)
    assert r["loglog_slope"] == pytest.approx(1.0)
    assert r["median_cell_ratio_viirs_to_modis"] == pytest.approx(4.0)
    assert r["share_viirs_only"] == pytest.approx(1 / 12)
    assert len(r["ratio_of_totals_ci95"]) == 2


def test_aqua_only_harmonization_frame():
    g = pd.concat([monthly_grid(det(MODIS_CSV, "MODIS_SP")), monthly_grid(det(VIIRS_CSV, "VIIRS_SNPP_SP"))])
    assert set(g[g.source == "MODIS_SP"].platform) == {"Terra", "Aqua"}
    cov = pd.DataFrame({"source": ["MODIS_SP", "VIIRS_SNPP_SP"],
                        "month": pd.PeriodIndex(["2020-09"] * 2, freq="M"), "coverage_fraction": [1.0, 1.0]})
    all_ = harmonization_frame(g, cov)
    aqua = harmonization_frame(g, cov, modis_platforms=("Aqua",))
    assert all_.modis_n_detections.sum() == 3 and aqua.modis_n_detections.sum() == 1
    assert set(aqua.modis_platforms) == {"Aqua"} and set(all_.modis_platforms) == {"all"}
    assert aqua.viirs_n_detections.sum() == all_.viirs_n_detections.sum() == 3
