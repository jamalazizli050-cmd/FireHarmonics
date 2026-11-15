from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest

from conftest import FakeSession, VIIRS_CSV, make_response
from firelens import country_archive as ca
from firelens.acquisition import download, iter_chunks, manifest_for
from firelens.catalog import BBox, REGIONS
from firelens.firms_client import FirmsClient
from firelens.location_data import tile_region

HEADER = ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,"
          "bright_ti5,frp,daynight,type\n")
IN_1 = "55.26233,-4.82425,296.07,0.47,0.4,{d},{t},N,VIIRS,n,2,285.35,0.32,N,0\n"
IN_2 = "56.10000,-3.50000,310.00,0.40,0.40,{d},{t},N,VIIRS,n,2,290.00,1.50,D,0\n"
OUT = "52.00000,-1.50000,305.00,0.40,0.40,{d},{t},N,VIIRS,n,2,288.00,1.00,D,0\n"   # England, outside the tile
TILE = tile_region(BBox(-5, 55, -1, 57))
TODAY = date(2026, 11, 15)
WINDOW = (date(2012, 1, 20), date(2026, 8, 6))


def archive_csv() -> str:
    """Country-file style: zero-padded acq_time, rows inside and outside the tile."""
    return (HEADER + IN_1.format(d="2023-07-01", t="0259") + OUT.format(d="2023-07-01", t="0300")
            + IN_2.format(d="2023-07-04", t="1312") + IN_1.format(d="2023-07-04", t="0118"))


def api_csv(extra: bool = False) -> str:
    """Area API style: unpadded acq_time, only rows inside the tile."""
    text = (HEADER + IN_1.format(d="2023-07-01", t="259") + IN_2.format(d="2023-07-04", t="1312")
            + IN_1.format(d="2023-07-04", t="118"))
    if extra:  # a detection the archive does not contain (e.g. filed under another country)
        text += IN_2.format(d="2023-07-02", t="1200")
    return text


class ArchiveSession:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.urls: list[str] = []

    def get(self, url, stream=False, timeout=None):
        self.urls.append(url)
        for key, text in self.files.items():
            if url.endswith(key):
                r = make_response(200, text)
                r.headers["Content-Length"] = str(len(text.encode()))
                r._content_consumed = True   # body already in memory: iter_content() yields it
                return r
        return make_response(404, "Not Found")


class FakeApiClient:
    """Answers each 5-day chunk with the rows of ``text`` that fall inside it (like the Area API)."""

    def __init__(self, text: str):
        self.rows = pd.read_csv(io.StringIO(text), dtype=str)
        self.calls: list[date] = []

    def area_csv(self, source, bbox, start, day_range):
        self.calls.append(start)
        end = date.fromordinal(start.toordinal() + day_range - 1)
        sel = self.rows[(self.rows["acq_date"] >= start.isoformat()) & (self.rows["acq_date"] <= end.isoformat())]
        buf = io.StringIO()
        sel.to_csv(buf, index=False, lineterminator="\n")
        return buf.getvalue()


def chunk_of(d: date):
    return next(iter_chunks(d, d))


def chunks_for(start: date, end: date):
    return [(c.start, c.end) for c in iter_chunks(start, end)]


def test_country_table_resolves_tiles():
    assert ca.countries_for_bbox(BBox(-5, 55, -1, 57)) == ["United_Kingdom"]
    assert ca.countries_for_bbox(BBox(23, -14, 25, -12)) == ["Angola", "Zambia"]
    with pytest.raises(ca.ArchiveUnavailable):          # open ocean
        ca.countries_for_bbox(BBox(-30, -30, -29, -29))
    with pytest.raises(ca.ArchiveUnavailable):          # Andorra: no FIRMS country file
        ca.countries_for_bbox(BBox(1, 42, 2, 43))


def test_archive_fills_tile_after_matching_spot_check(settings):
    st = settings.with_overrides(archive_spot_check_chunks=1)
    sess = ArchiveSession({"viirs-snpp_2023_United_Kingdom.csv": archive_csv()})
    client = FakeApiClient(api_csv())
    chunks = chunks_for(date(2023, 6, 25), date(2023, 7, 10))
    res = ca.fill_from_archive(st, TILE, chunks, WINDOW, client=client, session=sess, today=TODAY)

    assert res.used and res.countries == ["United_Kingdom"] and res.years == [2023]
    assert res.chunks_written == len(chunks)
    assert res.spot_check.startswith("passed")
    busiest = chunk_of(date(2023, 7, 4))
    assert client.calls == [busiest.start]               # the busiest chunk was checked
    entries = manifest_for(st, ca.SOURCE, TILE).entries
    assert all(e.status == "ok" and e.complete for e in entries.values())
    busy = entries[busiest.id]
    rows = pd.read_csv(io.StringIO((st.raw_dir / busy.path).read_text()), dtype=str)
    assert len(rows) == busy.n_rows == 2
    assert set(rows["acq_time"]) == {"1312", "118"}       # normalised like the Area API ("0118" -> "118")
    assert list(rows.columns) == ca.COLUMNS
    first = entries[chunk_of(date(2023, 7, 1)).id]
    rows = pd.read_csv(io.StringIO((st.raw_dir / first.path).read_text()), dtype=str)
    assert first.n_rows == 1 and rows["latitude"].tolist() == ["55.26233"]   # England row is outside the tile
    quiet = entries[chunk_of(date(2023, 6, 25)).id]
    assert quiet.n_rows == 0                              # observed zero inside complete archive coverage
    # the country-year file is kept locally: a second tile needs no network
    sess2 = ArchiveSession({})
    ca.tile_rows(st, ["United_Kingdom"], 2023, BBox(-5, 55, -1, 57), session=sess2)
    assert sess2.urls == []


def test_spot_check_mismatch_rejects_archive_and_writes_nothing(settings):
    st = settings.with_overrides(archive_spot_check_chunks=2)
    sess = ArchiveSession({"viirs-snpp_2023_United_Kingdom.csv": archive_csv()})
    res = ca.fill_from_archive(st, TILE, chunks_for(date(2023, 6, 25), date(2023, 7, 10)), WINDOW,
                               client=FakeApiClient(api_csv(extra=True)), session=sess, today=TODAY)
    assert not res.used and res.spot_check.startswith("failed")
    assert manifest_for(st, ca.SOURCE, TILE).entries == {}


def test_spot_check_tolerates_static_coastal_rows_missing_from_the_country_file(settings):
    st = settings.with_overrides(archive_spot_check_chunks=2)
    sess = ArchiveSession({"viirs-snpp_2023_United_Kingdom.csv": archive_csv()})
    static = IN_2.format(d="2023-07-04", t="0200").rsplit(",", 1)[0] + ",2\n"   # type 2: static/industrial source
    res = ca.fill_from_archive(st, TILE, chunks_for(date(2023, 6, 25), date(2023, 7, 10)), WINDOW,
                               client=FakeApiClient(api_csv() + static), session=sess, today=TODAY)
    assert res.used and res.spot_check.startswith("passed") and "1 static/coastal rows" in res.spot_check


def test_spot_diff_allows_a_tiny_share_of_vegetation_rows_only_in_big_chunks():
    rows = [{c: "x" for c in ca.COMPARE} | {"acq_time": str(i), "type": "0"} for i in range(1000)]
    api = pd.DataFrame(rows)
    arch = api.iloc[2:]                                    # 2 of 1000 vegetation rows missing
    veg, other, n_veg = ca._spot_diff(api, arch)
    assert (veg, other, n_veg) == (2, 0, 1000) and veg <= int(ca.SPOT_CHECK_VEG_TOLERANCE * n_veg)
    veg, _, n_veg = ca._spot_diff(api.iloc[:10], api.iloc[1:10])   # 1 of 10: not tolerated
    assert veg > int(ca.SPOT_CHECK_VEG_TOLERANCE * n_veg)


def test_current_year_and_missing_files_fall_back_to_the_api(settings):
    sess = ArchiveSession({})
    res = ca.fill_from_archive(settings, TILE, chunks_for(date(2026, 3, 1), date(2026, 3, 20)), WINDOW,
                               session=sess, today=TODAY)
    assert not res.used and "completed archive years" in res.reason
    res = ca.fill_from_archive(settings, TILE, chunks_for(date(2023, 3, 1), date(2023, 3, 20)), WINDOW,
                               session=sess, today=TODAY)                    # NASA returns 404
    assert not res.used and "no archive files" in res.reason
    assert manifest_for(settings, ca.SOURCE, TILE).entries == {}


def test_only_chunks_inside_the_sp_window_are_filled(settings):
    sess = ArchiveSession({"viirs-snpp_2023_United_Kingdom.csv": archive_csv()})
    window_end = date(2023, 6, 29)
    chunks = chunks_for(date(2023, 6, 25), date(2023, 7, 10))
    res = ca.fill_from_archive(settings, TILE, chunks, (date(2012, 1, 20), window_end), session=sess, today=TODAY)
    inside = [s for s, e in chunks if e <= window_end]
    assert res.chunks_written == len(inside) >= 1
    written = {date.fromisoformat(e.chunk_start) for e in manifest_for(settings, ca.SOURCE, TILE).entries.values()}
    assert written == set(inside)


def test_parallel_download_equals_sequential(settings):
    bad = list(iter_chunks(date(2020, 9, 1), date(2020, 10, 30)))[2].start.isoformat()

    def route(url):
        if f"/{bad}" in url:
            return make_response(400, "Invalid area")       # one rejected chunk is recorded, not fatal
        return make_response(200, VIIRS_CSV)

    region = REGIONS["california"]
    results = {}
    for workers in (1, 4):
        st = settings.with_overrides(download_workers=workers, data_dir=settings.data_dir / f"w{workers}")
        client = FirmsClient(st, session=FakeSession(route=route), sleep=lambda s: None)
        summary = download(client, "VIIRS_SNPP_SP", region, date(2020, 9, 1), date(2020, 10, 30))
        entries = manifest_for(st, "VIIRS_SNPP_SP", region).entries
        results[workers] = (summary.chunks_downloaded, summary.chunks_failed, summary.rows_downloaded,
                            sorted((k, e.status, e.n_rows) for k, e in entries.items()))
    assert results[1] == results[4]
    assert results[4][1] == 1 and results[4][0] > 5


def test_archive_switch(monkeypatch, tmp_path):
    from firelens.config import Settings, load_settings
    assert Settings(map_key="").use_country_archive is False          # tests / offline tools: never online
    env = tmp_path / "none.env"
    monkeypatch.delenv("FIRELENS_COUNTRY_ARCHIVE", raising=False)
    assert load_settings(env, require_key=False).use_country_archive is True
    assert load_settings(env, require_key=False).archive_only_history is True
    monkeypatch.setenv("FIRELENS_ARCHIVE_ONLY", "0")
    assert load_settings(env, require_key=False).archive_only_history is False
    monkeypatch.setenv("FIRELENS_COUNTRY_ARCHIVE", "0")
    assert load_settings(env, require_key=False).use_country_archive is False
