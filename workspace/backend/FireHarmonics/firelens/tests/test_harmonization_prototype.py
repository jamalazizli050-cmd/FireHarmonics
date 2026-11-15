"""Tests for swath geometry, coverage-aware matching and the harmonisation model."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from firelens.catalog import REGIONS
from firelens.cleaning import detections_dir
from firelens.io import write_parquet
from firelens.matching import MatchConfig, build_region, cluster_viirs, duplicate_stats
from firelens.swath import AQUA_MYD03, SNPP_VNP03IMG, GranuleGeometry, to_minutes
from firelens import harmonize as H


def poly(corners):
    """CMR-style polygon string 'lat lon ...' closed on the first point."""
    pts = list(corners) + [corners[0]]
    return " ".join(f"{la} {lo}" for la, lo in pts)


def granule_df(rows):
    return pd.DataFrame([{"granule_id": f"g{i}", "time_start": pd.Timestamp(t0, tz="UTC"),
                          "time_end": pd.Timestamp(t1, tz="UTC"), "day_night": dn, "polygon": poly(c), "n_rings": 1}
                         for i, (t0, t1, dn, c) in enumerate(rows)])


# A northbound (DAY) pass: granule A covers lat -1..0, granule B lat 0..1.
# CMR order: edges (p0,p1) and (p2,p3) are scan lines (2.4 deg long), sides 1 deg.
A = [(-1, -1.2), (-1, 1.2), (0, 1.2), (0, -1.2)]
B = [(0, -1.2), (0, 1.2), (1, 1.2), (1, -1.2)]


def test_to_minutes_is_unit_independent():
    ts_ns = pd.Series(pd.to_datetime(["2020-01-01 00:10"], utc=True))
    ts_us = ts_ns.astype("datetime64[us, UTC]")
    assert to_minutes(ts_ns)[0] == to_minutes(ts_us)[0] == pytest.approx(26297290.0)


def test_granule_geometry_with_successor():
    g = granule_df([("2020-01-01 00:00", "2020-01-01 00:05", "DAY", A),
                    ("2020-01-01 00:05", "2020-01-01 00:10", "DAY", B)])
    geo = GranuleGeometry(g)
    assert list(geo.orient_method) == ["successor", "predecessor"]
    t0 = to_minutes(g["time_start"])[0]
    loc = geo.locate([-0.5, 0.5, 5.0], [0.0, 0.9, 0.0], [t0 + 2.5, t0 + 7.5, t0 + 2.5], window_min=5)
    loc = loc.astype(float).sort_values("point")
    a = loc[loc.point == 0].iloc[0]
    assert a.granule == 0 and a.along == pytest.approx(0.5, abs=0.02) and a.xtrack == pytest.approx(0, abs=0.02)
    assert a.t_obs - t0 == pytest.approx(2.5, abs=0.1)
    b = loc[loc.point == 1].iloc[0]
    assert b.granule == 1 and abs(b.xtrack) == pytest.approx(0.75, abs=0.03)
    assert b.t_obs - t0 == pytest.approx(7.5, abs=0.1)
    assert (loc.point == 2).sum() == 0  # outside every footprint


@pytest.mark.parametrize("dn,expected_along", [("DAY", 0.1), ("NIGHT", 0.9)])
def test_isolated_granule_uses_day_night_direction(dn, expected_along):
    geo = GranuleGeometry(granule_df([("2020-01-01 00:00", "2020-01-01 00:05", dn, A)]))
    assert geo.orient_method[0] == "day_night_direction"
    t0 = geo.t0[0]
    loc = geo.locate([-0.9], [0.0], [t0 + 2.5], window_min=10).astype(float)
    assert loc.along.iloc[0] == pytest.approx(expected_along, abs=0.02)


def _px(lat, lon, t, frp=5.0, scan=0.4, track=0.4):
    return {"latitude": lat, "longitude": lon, "t_obs": t, "scan_km": scan, "track_km": track, "frp_mw": frp,
            "xtrack": 0.1}


def test_viirs_clustering_and_duplicates():
    d = 0.375 / 111.2  # one VIIRS pixel in degrees latitude
    v = pd.DataFrame([_px(10, 20, 100), _px(10 + d, 20, 100), _px(10.5, 20, 100), _px(10, 20, 220),
                      _px(10 + 0.0004, 20, 100)])  # last: ~45 m from the first -> near duplicate
    lab = cluster_viirs(v, tol_km=0.1)
    assert lab[0] == lab[1] == lab[4] and lab[2] != lab[0] and lab[3] != lab[0]
    st = duplicate_stats(v, "VIIRS")
    assert st["near_duplicate_pairs"] == 1 and st["pixels_involved"] == 2


# ----------------------------------------------------------- end-to-end matching
CA_POLY = [(30, -130), (30, -110), (45, -110), (45, -130)]  # scan edges along lat=30/45


def _det(lat, lon, t, frp, platform, scan, sat_code_conf="nominal"):
    ts = pd.Timestamp(t, tz="UTC")
    return {"latitude": lat, "longitude": lon, "acq_date": ts.tz_localize(None).normalize(), "acq_datetime_utc": ts,
            "scan_km": scan, "track_km": scan, "frp_mw": frp, "daynight": "D", "platform": platform,
            "confidence_class": pd.Categorical([sat_code_conf], categories=["low", "nominal", "high"], ordered=True)[0],
            "brightness_k": 330.0, "fire_type": 0, "qc_usable": True}


def test_build_region_coverage_and_matching(settings):
    region = REGIONS["california"]
    # Granule caches (Aqua 21:00-21:05; VIIRS 21:10-21:16 and 23:00-23:06), all DAY / northbound.
    for coll, rows in ((AQUA_MYD03, [("2020-09-01 21:00", "2020-09-01 21:05", "DAY", CA_POLY)]),
                       (SNPP_VNP03IMG, [("2020-09-01 21:10", "2020-09-01 21:16", "DAY", CA_POLY),
                                        ("2020-09-01 23:00", "2020-09-01 23:06", "DAY", CA_POLY)])):
        g = granule_df(rows)
        g["platform"] = coll.platform
        write_parquet(g, settings.cache_dir / "granules" / coll.key / region.name / "2020.parquet")
    d = 0.375 / 111.2
    viirs = [
        _det(37.0, -120.0, "2020-09-01 21:13", 20.0, "Suomi NPP", 0.4),       # cluster A (2 px)
        _det(37.0 + d, -120.0, "2020-09-01 21:13", 15.0, "Suomi NPP", 0.4),
        _det(38.0, -121.0, "2020-09-01 21:13", 1.0, "Suomi NPP", 0.4),        # cluster B, small, missed by MODIS
        _det(37.0, -120.0, "2020-09-01 23:03", 30.0, "Suomi NPP", 0.4),       # cluster C, 2 h after Aqua
    ]
    modis = [
        _det(37.001, -120.002, "2020-09-01 21:02", 40.0, "Aqua", 1.0),        # detects A
        _det(39.0, -122.0, "2020-09-01 21:03", 12.0, "Aqua", 1.0),            # MODIS-only (VIIRS observed)
        _det(37.0, -120.0, "2020-09-01 18:00", 50.0, "Terra", 1.0),           # Terra: ignored
    ]
    for sid, rows in (("VIIRS_SNPP_SP", viirs), ("MODIS_SP", modis)):
        df = pd.DataFrame(rows)
        df["confidence_class"] = df["confidence_class"].astype(pd.CategoricalDtype(["low", "nominal", "high"], ordered=True))
        write_parquet(df, detections_dir(settings, region, sid) / "2020" / "2020-09.parquet")

    res = build_region(settings, region, date(2020, 9, 1), date(2020, 9, 30), MatchConfig(), use_cache=False)
    cl = res["clusters"].sort_values(["t_viirs", "lat"]).reset_index(drop=True)
    assert len(cl) == 3
    a, b, c = cl.iloc[0], cl.iloc[1], cl.iloc[2]
    assert a.n_pix == 2 and a.frp_sum == 35.0
    assert a.comparable and b.comparable and not c.comparable
    assert a.aqua_dt_min == pytest.approx(-10.5, abs=1.0)
    assert not c.aqua_covered            # no Aqua observation within 100 min: not a non-detection
    assert a.modis_detected and a.n_modis == 1 and a.frp_modis == 40.0
    assert not b.modis_detected and b.n_modis == 0
    m = res["modis"].sort_values("latitude").reset_index(drop=True)
    assert len(m) == 2                   # Terra excluded
    assert m.comparable.all() and m.matched.tolist() == [True, False]
    assert res["diagnostics"]["modis_located_share"] == 1.0


# ------------------------------------------------------------------- model
def _synthetic(n=4000, seed=0, months=12):
    rng = np.random.default_rng(seed)
    frp = 10 ** rng.uniform(-0.5, 3, n)
    ax = rng.uniform(0, 1, n)
    logit = -3 + 2.2 * np.log10(frp) - 2.0 * ax
    det = rng.random(n) < 1 / (1 + np.exp(-logit))
    days = pd.to_datetime("2020-01-01") + pd.to_timedelta(rng.integers(0, 30 * months, n), "D")
    cl = pd.DataFrame({"frp_sum": frp, "frp_max": frp / 2, "n_pix": rng.integers(1, 10, n), "night": rng.random(n) < .5,
                       "saturated": False, "aqua_dt_min": rng.uniform(-20, 20, n), "aqua_ax": ax,
                       "viirs_ax": rng.uniform(0, 1, n), "modis_detected": det, "acq_date": days,
                       "month": days.to_period("M").astype(str), "year": days.year, "region": "x"})
    cl["n_modis"] = np.where(det, rng.poisson(1 + np.log10(frp).clip(0)), 0)
    cl.loc[det & (cl.n_modis == 0), "n_modis"] = 1
    cl["frp_modis"] = np.where(det, frp * 10 ** rng.normal(0, 0.2, n), 0.0)
    cl = H.add_features(cl)
    md = pd.DataFrame({"frp_mw": np.repeat(cl["frp_modis"] / cl["n_modis"].clip(lower=1), cl["n_modis"]).to_numpy()})
    md["acq_date"] = np.repeat(cl["acq_date"].to_numpy(), cl["n_modis"])
    md["month"] = pd.to_datetime(md["acq_date"]).dt.to_period("M").astype(str)
    md["year"] = pd.to_datetime(md["acq_date"]).dt.year
    md["region"] = "x"
    return cl, md


def test_model_recovers_detection_structure_and_beats_constant():
    cl, md = _synthetic()
    tr, te = cl[cl.acq_date < "2020-08-01"], cl[cl.acq_date >= "2020-08-01"]
    trm, tem = md[md.acq_date < "2020-08-01"], md[md.acq_date >= "2020-08-01"]
    r = H.evaluate_fold(tr, trm, te, tem, n_boot=5)
    det = r["metrics"]["detection"]
    assert det["full"]["auc"] > 0.8 and det["full"]["log_loss"] < det["constant_rate"]["log_loss"]
    fm = H.fit_model(tr, trm)
    probe = H.add_features(te.head(2).assign(frp_sum=[1.0, 500.0], frp_max=[0.5, 250.0]))
    p = fm.detector.predict_proba(probe)[:, 1]
    assert p[1] > p[0]
    assert fm.k_n == pytest.approx(1.0)  # every synthetic MODIS pixel belongs to a cluster
    assert {"count_lo", "count_hi"} <= set(r["monthly"].columns)


def test_conformal_interval_and_verdict_rules():
    pred = pd.Series([100.0, 200.0])
    lo, hi = H.conformal_interval(pred, list(np.linspace(-0.1, 0.1, 21)))
    assert lo.iloc[0] == pytest.approx(100 * 10 ** -0.09) and hi.iloc[1] == pytest.approx(200 * 10 ** 0.09)
    assert H.conformal_interval(pred, [0.0] * 3) == (None, None)

    def mm(f, r, c, tr=1.0):
        mk = lambda v, t=1.0: {"median_abs_log10_ratio": v, "total_ratio_pred_to_obs": t}
        return {"monthly_count": {"full": mk(f, tr), "raw": mk(r), "constant": mk(c), "frp_only": mk(f)},
                "monthly_frp": {"full": mk(f, tr), "raw": mk(r), "constant": mk(c), "frp_only": mk(f)}}
    assert H.verdict(mm(0.05, 0.8, 0.1))["count"] == "improved"
    assert H.verdict(mm(0.095, 0.8, 0.1))["count"].startswith("marginal")
    assert H.verdict(mm(0.2, 0.8, 0.1))["count"].startswith("not better")
    assert H.verdict(mm(0.05, 0.8, 0.1, tr=1.4))["count"].startswith("lower error but total biased")
