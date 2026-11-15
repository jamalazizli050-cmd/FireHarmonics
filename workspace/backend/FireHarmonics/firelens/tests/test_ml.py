"""Tests for forecasting features (leakage), baselines, metrics and pattern discovery."""
import numpy as np
import pandas as pd
import pytest

from firelens import forecast as F
from firelens import patterns as P


def _panel(n_months=36, seed=0):
    rng = np.random.default_rng(seed)
    cells = F.region_cells("punjab", 0.25).head(6)
    months = pd.period_range("2018-01", periods=n_months, freq="M")
    rows = []
    for c in cells.itertuples():
        for m in months:
            seasonal = 3 if m.month in (4, 5, 10, 11) else 0
            cnt = rng.poisson(seasonal * (1 + c.Index % 2))
            rows.append({"cell_id": c.cell_id, "row": c.row, "col": c.col, "lat": c.lat, "lon": c.lon,
                         "region": "punjab", "month": m, "count": cnt, "frp": cnt * 5.0})
    p = pd.DataFrame(rows)
    p["y"] = (p["count"] > 0).astype(int)
    return p


def test_features_use_only_past_months():
    p = _panel()
    f1 = F.add_features(p)
    cut = pd.Period("2019-06", "M")
    p2 = p.copy()
    p2.loc[p2["month"] >= cut, ["count", "frp"]] = 999  # change the present and future only
    p2["y"] = (p2["count"] > 0).astype(int)
    f2 = F.add_features(p2)
    cols = [c for c in F.FEATURES]
    a = f1[f1["month"] <= cut].sort_values(["cell_id", "month"])[cols].to_numpy(float)
    b = f2[f2["month"] <= cut].sort_values(["cell_id", "month"])[cols].to_numpy(float)
    np.testing.assert_allclose(np.nan_to_num(a, nan=-7), np.nan_to_num(b, nan=-7))


def test_feature_values():
    p = _panel()
    f = F.add_features(p).set_index(["cell_id", "month"])
    c = p["cell_id"].iloc[0]
    m = pd.Period("2020-05", "M")
    raw = p.set_index(["cell_id", "month"])["count"]
    assert f.loc[(c, m), "lag1"] == pytest.approx(np.log1p(raw[(c, m - 1)]))
    assert f.loc[(c, m), "lag12"] == pytest.approx(np.log1p(raw[(c, m - 12)]))
    past_same = [(raw[(c, pd.Period(f"{y}-05", "M"))] > 0) for y in (2018, 2019)]
    assert f.loc[(c, m), "clim_same_month"] == pytest.approx(np.mean(past_same))
    assert f.loc[(c, m), "clim_years"] == 2
    hist = (raw.loc[c].loc[:m - 1] > 0).mean()
    assert f.loc[(c, m), "hist_freq"] == pytest.approx(hist)


def test_baselines_and_metrics():
    p = F.add_features(_panel())
    d = F.usable_rows(p, 12)
    b = F.baseline_probs(d, (0.6, 0.1))
    for v in b.values():
        assert ((v > 0) & (v < 1)).all() and len(v) == len(d)
    m = F.prob_metrics([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
    assert m["roc_auc"] == 1.0 and m["pr_auc"] == 1.0 and m["brier"] == pytest.approx(0.025)


def test_seasonality_trend_anomaly():
    idx = pd.period_range("2012-01", "2021-12", freq="M")
    base = np.where(idx.month == 8, 100.0, 10.0)
    growth = 1.1 ** (idx.year - 2012)
    s = pd.Series(base * growth, index=idx)
    prof = P.seasonal_profile(s)
    assert prof["peak_month"] == "Aug" and prof["n_full_years"] == 10
    tr = P.trend(s)
    assert tr["theil_sen_pct_per_year"] == pytest.approx(10, abs=1) and tr["significant_at_0.05"]
    s2 = s.copy()
    s2[pd.Period("2017-03", "M")] = 5000
    an = P.monthly_anomalies(s2)
    assert an.loc[an["month"] == "2017-03", "anomaly"].item()
    assert an["anomaly"].sum() <= 3


def test_getis_ord_and_isolation_forest():
    from firelens.forecast import region_cells
    cells = region_cells("punjab", 0.25)
    v = np.zeros(len(cells))
    hot = (cells["row"] - cells["row"].min() < 3) & (cells["col"] - cells["col"].min() < 3)
    v[hot.to_numpy()] = 50
    hs = P.getis_ord_hotspots(cells.assign(mean_annual_detections=v, region="punjab"), "mean_annual_detections")
    assert hs.loc[hot.to_numpy(), "hotspot_99"].any() and not hs.loc[~hot.to_numpy(), "hotspot_99"].all()
    rng = np.random.default_rng(1)
    n = 400
    cm = pd.DataFrame({"cell_id": rng.integers(0, 20, n), "month": pd.PeriodIndex(
        pd.period_range("2015-01", periods=48, freq="M")[rng.integers(0, 48, n)]),
        "count": rng.poisson(5, n) + 1, "frp": rng.gamma(2, 10, n), "night_share": rng.random(n),
        "active_days": rng.integers(1, 10, n)})
    cm.loc[0, ["count", "frp"]] = [5000, 1e6]
    out = P.isolation_forest_anomalies(cm.drop_duplicates(["cell_id", "month"]).reset_index(drop=True))
    assert out["iso_anomaly"].sum() >= 1
    assert out.sort_values("iso_score", ascending=False).iloc[0]["count"] == 5000


def test_anomaly_requires_material_departure():
    idx = pd.period_range("2012-01", "2021-12", freq="M")
    s = pd.Series(np.where(idx.month == 7, 10_000.0, 0.0), index=idx)
    s[pd.Period("2016-02", "M")] = 5          # a few detections in a normally-zero month: not material
    s[pd.Period("2019-07", "M")] = 60_000     # 6x the usual July: real anomaly
    an = P.monthly_anomalies(s).set_index("month")
    assert an.loc["2016-02", "z"] > 3 and not an.loc["2016-02", "anomaly"]
    assert an.loc["2019-07", "anomaly"]
