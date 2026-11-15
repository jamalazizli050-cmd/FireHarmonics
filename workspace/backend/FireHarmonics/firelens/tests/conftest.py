from __future__ import annotations

import json

import pytest
import requests

from firelens.config import Settings

# Header + rows copied from real FIRMS Area API responses (California, 2020-09-01).
MODIS_CSV = (
    "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
    "version,bright_t31,frp,daynight,type\n"
    "36.0234,-121.5115,340.6,1,1,2020-09-01,618,Terra,MODIS,100,6.03,290.9,46.2,N,0\n"
    "36.031,-121.5249,316.7,1,1,2020-09-01,618,Terra,MODIS,78,6.03,290.6,14.9,N,0\n"
    "36.0339,-121.5025,309,1,1,2020-09-01,2105,Aqua,MODIS,25,6.03,292.6,8.3,D,2\n"
)
VIIRS_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
    "version,bright_ti5,frp,daynight,type\n"
    "39.47383,-122.90264,315.05,0.43,0.46,2020-09-01,936,N,VIIRS,n,2,293.9,2.54,N,0\n"
    "39.47802,-122.90152,309.58,0.43,0.46,2020-09-01,936,N,VIIRS,h,2,293.41,2.03,N,0\n"
    "39.47848,-122.90646,330.63,0.43,0.46,2020-09-01,2112,N,VIIRS,l,2,295.66,2.03,D,0\n"
)


def make_response(status: int = 200, text: str = "", content_type: str = "text/plain") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = text.encode("utf-8")
    r.encoding = "utf-8"
    r.headers["content-type"] = content_type
    return r


class FakeSession:
    """Returns queued responses (or raises queued exceptions) and records URLs."""

    def __init__(self, responses=None, route=None):
        self.queue = list(responses or [])
        self.route = route
        self.urls: list[str] = []
        self.headers: dict = {}

    def get(self, url, timeout=None):
        self.urls.append(url)
        if self.route is not None:
            item = self.route(url)
        else:
            item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def settings(tmp_path):
    return Settings(map_key="TESTKEY0123456789", data_dir=tmp_path / "data", reports_dir=tmp_path / "reports",
                    min_request_interval_s=0.0, backoff_base_s=0.01, max_retries=3, status_check_every=10_000)


@pytest.fixture
def sleeps():
    return []


def status_json(used: int) -> str:
    return json.dumps({"transaction_limit": 5000, "current_transactions": used, "transaction_interval": "10 minutes"})
