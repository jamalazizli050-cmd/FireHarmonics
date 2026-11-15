from datetime import date

import pytest
import requests

from conftest import MODIS_CSV, FakeSession, make_response, status_json
from firelens.catalog import WORLD, BBox
from firelens.firms_client import (FirmsAPIError, FirmsAuthError, FirmsClient, FirmsRateLimitError,
                                   FirmsRequestError, FirmsTransientError)

CA = BBox(-124.5, 32.5, -114, 42.1)


def client(settings, session, sleeps):
    return FirmsClient(settings, session=session, sleep=sleeps.append)


def test_area_url_format_and_success(settings, sleeps):
    s = FakeSession([make_response(200, MODIS_CSV)])
    text = client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 5)
    assert text == MODIS_CSV
    assert s.urls == [f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{settings.map_key}/MODIS_SP/"
                      "-124.5,32.5,-114,42.1/5/2020-09-01"]


def test_world_area_uses_world_keyword(settings, sleeps):
    s = FakeSession([make_response(200, MODIS_CSV)])
    client(settings, s, sleeps).area_csv("VIIRS_SNPP_SP", WORLD, date(2020, 9, 1), 1)
    assert "/VIIRS_SNPP_SP/world/1/2020-09-01" in s.urls[0]


def test_empty_result_is_valid(settings, sleeps):
    header = MODIS_CSV.splitlines()[0]
    s = FakeSession([make_response(200, header)])
    assert client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1) == header


def test_invalid_key_is_not_retried_and_key_is_redacted(settings, sleeps):
    s = FakeSession([make_response(400, "Invalid MAP_KEY.")])
    with pytest.raises(FirmsAuthError) as exc:
        client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)
    assert len(s.urls) == 1
    assert settings.map_key not in str(exc.value)


def test_bad_request_is_not_retried(settings, sleeps):
    s = FakeSession([make_response(400, "Invalid day range. Expects [1..5].")])
    with pytest.raises(FirmsRequestError) as exc:
        client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)
    assert "Invalid day range" in str(exc.value)
    assert settings.map_key not in str(exc.value)
    assert len(s.urls) == 1


def test_local_validation_of_source_and_day_range(settings, sleeps):
    c = client(settings, FakeSession([]), sleeps)
    with pytest.raises(ValueError):
        c.area_csv("BOGUS", CA, date(2020, 9, 1), 1)
    with pytest.raises(ValueError):
        c.area_csv("MODIS_SP", CA, date(2020, 9, 1), 6)


def test_server_error_then_success_retries_with_backoff(settings, sleeps):
    s = FakeSession([make_response(503, "unavailable"), make_response(502, ""), make_response(200, MODIS_CSV)])
    assert client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1) == MODIS_CSV
    assert len(s.urls) == 3
    assert sleeps[1] > sleeps[0]  # exponential backoff


def test_network_errors_are_retried_then_give_up(settings, sleeps):
    s = FakeSession([requests.ConnectionError("boom")] * 4)
    with pytest.raises(FirmsTransientError):
        client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)
    assert len(s.urls) == settings.max_retries + 1


def test_rate_limit_waits_and_retries(settings, sleeps):
    s = FakeSession([make_response(429, "Too many requests"), make_response(200, MODIS_CSV)])
    assert client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1) == MODIS_CSV
    assert sleeps and sleeps[0] >= 60


def test_rate_limit_exhausted(settings, sleeps):
    s = FakeSession([make_response(429, "limit exceeded")] * 4)
    with pytest.raises(FirmsRateLimitError):
        client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)


def test_unexpected_body_raises(settings, sleeps):
    s = FakeSession([make_response(200, "<html>Maintenance</html>", "text/html")])
    with pytest.raises(FirmsAPIError):
        client(settings, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)


def test_key_status_is_not_mistaken_for_rate_limit(settings, sleeps):
    s = FakeSession([make_response(200, status_json(12), "application/json")])
    st = client(settings, s, sleeps).key_status()
    assert st["current_transactions"] == 12
    assert sleeps == []


def test_budget_pause_when_near_limit(settings, sleeps):
    st = settings.with_overrides(status_check_every=1)
    s = FakeSession([make_response(200, status_json(4500), "application/json"), make_response(200, MODIS_CSV)])
    client(st, s, sleeps).area_csv("MODIS_SP", CA, date(2020, 9, 1), 1)
    assert 600 in sleeps
    assert "mapkey_status" in s.urls[0]


def test_data_availability_parsing(settings, sleeps):
    body = "data_id,min_date,max_date\nMODIS_SP,2000-11-01,2026-08-31\nVIIRS_SNPP_SP,2012-01-20,2026-07-31\n"
    df = client(settings, FakeSession([make_response(200, body)]), sleeps).data_availability()
    assert df.set_index("data_id").loc["MODIS_SP", "min_date"] == date(2000, 11, 1)


def test_missing_key_rejected(settings):
    with pytest.raises(FirmsAuthError):
        FirmsClient(settings.with_overrides(map_key=""))
