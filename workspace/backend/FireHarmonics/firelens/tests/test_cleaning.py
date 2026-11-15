from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import MODIS_CSV, VIIRS_CSV
from firelens.catalog import REGIONS, get_source
from firelens.cleaning import SchemaError, parse_raw_csv, qc_summary, standardize, validate

MODIS = get_source("MODIS_SP")
VIIRS = get_source("VIIRS_SNPP_SP")
CA = REGIONS["california"].bbox


def _std(csv, src):
    return validate(standardize(parse_raw_csv(csv, src), src, "f.csv"), bbox=CA)


def test_modis_standardisation():
    df = _std(MODIS_CSV, MODIS)
    assert list(df["platform"]) == ["Terra", "Terra", "Aqua"]
    assert list(df["confidence_class"].astype(str)) == ["high", "nominal", "low"]
    assert df["confidence_pct"].tolist() == [100, 78, 25]
    assert df["acq_datetime_utc"].iloc[0] == pd.Timestamp("2020-09-01 06:18", tz="UTC")
    assert df["acq_datetime_utc"].iloc[2] == pd.Timestamp("2020-09-01 21:05", tz="UTC")
    assert df["nominal_resolution_m"].iloc[0] == 1000
    assert df["brightness_k"].iloc[0] == pytest.approx(340.6)
    assert df["qc_usable"].all()
    assert df["qc_non_vegetation_type"].tolist() == [False, False, True]
    assert df["raw_row"].tolist() == [0, 1, 2]


def test_viirs_standardisation():
    df = _std(VIIRS_CSV, VIIRS)
    assert set(df["platform"]) == {"Suomi NPP"}
    assert list(df["confidence_class"].astype(str)) == ["nominal", "high", "low"]
    assert df["confidence_pct"].isna().all()
    assert df["brightness_k"].iloc[0] == pytest.approx(315.05)
    assert df["nominal_resolution_m"].iloc[0] == 375
    assert df["qc_usable"].all()


def test_schema_error_on_missing_columns():
    with pytest.raises(SchemaError):
        parse_raw_csv("latitude,longitude\n1,2\n", MODIS)


def test_type_column_is_optional_for_nrt():
    csv = "\n".join(",".join(r.split(",")[:-1]) for r in MODIS_CSV.strip().splitlines())
    src = get_source("MODIS_NRT")
    df = _std(csv, src)
    assert df["fire_type"].isna().all() and df["qc_usable"].all()


def _bad_rows():
    rows = MODIS_CSV.strip().splitlines()
    header, good = rows[0], rows[1]
    f = good.split(",")

    def mod(**kw):
        g = f.copy()
        cols = header.split(",")
        for k, v in kw.items():
            g[cols.index(k)] = v
        return ",".join(g)

    return "\n".join([
        header,
        good,
        good,                                   # exact duplicate
        mod(latitude="95"),                     # invalid latitude
        mod(longitude="abc"),                   # unparseable
        mod(acq_time="2475"),                   # invalid time
        mod(acq_date="2020-13-01"),             # invalid date
        mod(frp=""),                            # missing FRP (kept usable)
        mod(frp="-3"),                          # negative FRP (kept usable)
        mod(confidence="150"),                  # invalid confidence (kept usable)
        mod(satellite="X"),                     # unknown platform
        mod(latitude="10.0"),                   # valid but outside California bbox
        mod(scan="9.5"),                        # implausible pixel size (kept usable)
    ]) + "\n"


def test_validation_flags_without_dropping():
    raw = parse_raw_csv(_bad_rows(), MODIS)
    df = validate(standardize(raw, MODIS), bbox=CA, window=(date(2020, 8, 30), date(2020, 9, 3)))
    assert len(df) == len(raw) == 12  # nothing dropped
    flags = df.set_index(df.index)
    assert flags.loc[1, "qc_duplicate"] and not flags.loc[0, "qc_duplicate"]
    assert flags.loc[2, "qc_coord_invalid"]
    assert flags.loc[3, "qc_parse_error"] and flags.loc[3, "qc_coord_invalid"]
    assert flags.loc[4, "qc_time_invalid"]
    assert flags.loc[5, "qc_time_invalid"]
    assert flags.loc[6, "qc_frp_missing"] and flags.loc[6, "qc_usable"]
    assert flags.loc[7, "qc_frp_negative"] and flags.loc[7, "qc_usable"]
    assert flags.loc[8, "qc_confidence_invalid"] and flags.loc[8, "qc_usable"]
    assert flags.loc[9, "qc_platform_unknown"] and not flags.loc[9, "qc_usable"]
    assert flags.loc[10, "qc_outside_bbox"] and not flags.loc[10, "qc_usable"]
    assert flags.loc[11, "qc_pixel_size_suspect"] and flags.loc[11, "qc_usable"]
    usable = df["qc_usable"].to_numpy()
    assert usable.tolist() == [True, False, False, False, False, False, True, True, True, False, False, True]
    s = qc_summary(df)
    assert s["n_records"] == 12 and s["n_usable"] == int(usable.sum())


def test_outside_window_flag():
    df = validate(standardize(parse_raw_csv(MODIS_CSV, MODIS), MODIS), bbox=CA,
                  window=(date(2020, 9, 2), date(2020, 9, 6)))
    assert df["qc_outside_window"].all()
    assert not df["qc_usable"].any()  # kept, but excluded from aggregation


def test_process_end_to_end_with_cross_chunk_duplicates(settings):
    from conftest import FakeSession, make_response
    from firelens.acquisition import download
    from firelens.cleaning import load_detections, process
    from firelens.firms_client import FirmsClient
    from firelens.io import read_parquet  # noqa: F401  (engine available)

    region = REGIONS["california"]
    c = FirmsClient(settings, session=FakeSession(route=lambda u: make_response(200, MODIS_CSV)),
                    sleep=lambda s: None)
    r = download(c, "MODIS_SP", region, date(2020, 8, 25), date(2020, 9, 10))
    summary = process(settings, "MODIS_SP", region, date(2020, 8, 25), date(2020, 9, 10))
    d = load_detections(settings, region, "MODIS_SP")
    # Every chunk returned the same 3 rows dated 2020-09-01: nothing is dropped,
    # but only the first copy of each is usable; copies outside their chunk window are flagged.
    assert len(d) == 3 * r.chunks_total == summary.qc["n_records"]
    assert d["qc_usable"].sum() == 3
    # Duplicate detection runs within each monthly file; the copies routed to the
    # August file are excluded by the outside-window flag instead.
    assert d["qc_outside_window"].sum() == 3 * (r.chunks_total - 1)
    assert d["qc_duplicate"].sum() == 6
    assert not (d["qc_usable"] & (d["qc_duplicate"] | d["qc_outside_window"])).any()
    assert set(d["acq_date"].dt.strftime("%Y-%m")) == {"2020-09"}
    # The chunk lying entirely in August returned September-dated rows: they are
    # kept (flagged) in the August file instead of being lost.
    assert summary.months_written == 2
    assert len(load_detections(settings, region, "MODIS_SP", usable_only=True)) == 3
