from datetime import date, timedelta

import pytest

from conftest import MODIS_CSV, FakeSession, make_response
from firelens.acquisition import (Chunk, ManifestEntry, _needs_download, cached_chunks, download,
                                  iter_chunks, manifest_for)
from firelens.catalog import REGIONS
from firelens.firms_client import FirmsAuthError, FirmsClient

CA = REGIONS["california"]


def test_chunks_are_epoch_aligned_and_cover_range():
    chunks = list(iter_chunks(date(2020, 9, 3), date(2020, 9, 20), 5))
    assert chunks[0].start <= date(2020, 9, 3) <= chunks[0].end
    assert chunks[-1].start <= date(2020, 9, 20) <= chunks[-1].end
    for a, b in zip(chunks, chunks[1:]):
        assert b.start == a.end + timedelta(days=1)
    # A different but overlapping request yields the same aligned chunks.
    other = list(iter_chunks(date(2020, 9, 1), date(2020, 9, 30), 5))
    assert {c.id for c in chunks} <= {c.id for c in other}


def test_chunk_validation():
    with pytest.raises(ValueError):
        list(iter_chunks(date(2020, 1, 1), date(2020, 1, 2), 6))
    with pytest.raises(ValueError):
        list(iter_chunks(date(2020, 1, 2), date(2020, 1, 1), 5))


def _client(settings, session):
    return FirmsClient(settings, session=session, sleep=lambda s: None)


def test_download_caches_and_is_incremental(settings):
    s = FakeSession(route=lambda url: make_response(200, MODIS_CSV))
    c = _client(settings, s)
    r1 = download(c, "MODIS_SP", CA, date(2020, 9, 1), date(2020, 9, 10))
    assert r1.chunks_downloaded == r1.chunks_total > 0 and r1.rows_downloaded == 3 * r1.chunks_total
    n_calls = len(s.urls)
    r2 = download(c, "MODIS_SP", CA, date(2020, 9, 1), date(2020, 9, 10))
    assert r2.chunks_downloaded == 0 and r2.chunks_cached == r1.chunks_total
    assert len(s.urls) == n_calls  # no new requests
    # Extending the range only downloads the new chunks.
    r3 = download(c, "MODIS_SP", CA, date(2020, 9, 1), date(2020, 9, 20))
    assert r3.chunks_cached == r1.chunks_total and r3.chunks_downloaded == r3.chunks_total - r1.chunks_total
    # Raw file preserved verbatim.
    _, entry = cached_chunks(settings, "MODIS_SP", CA)[0]
    assert (settings.raw_dir / entry.path).read_text() == MODIS_CSV


def test_failed_chunks_are_recorded_and_retried(settings):
    responses = iter([make_response(400, "Invalid area coordinate."), make_response(200, MODIS_CSV)])
    s = FakeSession(route=lambda url: next(responses))
    c = _client(settings, s)
    r1 = download(c, "MODIS_SP", CA, date(2020, 9, 2), date(2020, 9, 2))
    assert r1.chunks_failed == 1
    m = manifest_for(settings, "MODIS_SP", CA)
    assert list(m.entries.values())[0].status == "error"
    r2 = download(c, "MODIS_SP", CA, date(2020, 9, 2), date(2020, 9, 2))
    assert r2.chunks_downloaded == 1


def test_auth_error_aborts_download(settings):
    s = FakeSession(route=lambda url: make_response(400, "Invalid MAP_KEY."))
    with pytest.raises(FirmsAuthError):
        download(_client(settings, s), "MODIS_SP", CA, date(2020, 9, 1), date(2020, 9, 10))


def test_chunks_outside_availability_are_skipped_and_partial_chunks_refetched(settings):
    s = FakeSession(route=lambda url: make_response(200, MODIS_CSV))
    c = _client(settings, s)
    avail = (date(2020, 9, 1), date(2020, 9, 7))
    r = download(c, "MODIS_SP", CA, date(2020, 8, 1), date(2020, 9, 30), availability=avail)
    assert r.skipped_unavailable > 0
    entries = list(manifest_for(settings, "MODIS_SP", CA).entries.values())
    partial = [e for e in entries if not e.complete]
    assert len(partial) == 1  # the chunk straddling 2020-09-07
    r2 = download(c, "MODIS_SP", CA, date(2020, 8, 1), date(2020, 9, 30), availability=avail)
    assert r2.chunks_downloaded == 1


def test_nrt_chunks_are_refreshed_until_settled(tmp_path):
    raw = tmp_path
    (raw / "f.csv").write_text("x")
    ch = Chunk(date(2026, 11, 10), 1)
    e = ManifestEntry("MODIS_NRT", "ca", "", "2026-11-10", 1, "ok", 1, "f.csv", None, "2026-11-10T12:00:00+00:00")
    assert _needs_download(e, ch, standard=False, raw_dir=raw, today=date(2026, 11, 12))
    assert not _needs_download(e, ch, standard=True, raw_dir=raw, today=date(2026, 11, 12))
    settled = ManifestEntry(**{**e.__dict__, "retrieved_at": "2026-11-20T00:00:00+00:00"})
    assert not _needs_download(settled, ch, standard=False, raw_dir=raw, today=date(2026, 11, 21))
