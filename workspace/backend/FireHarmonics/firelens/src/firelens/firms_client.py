"""Thin, robust client for the NASA FIRMS API.

Only documented endpoints are used:

* ``/api/area/csv/{MAP_KEY}/{SOURCE}/{AREA}/{DAY_RANGE}/{DATE}``
  (https://firms.modaps.eosdis.nasa.gov/api/area/) - DAY_RANGE is 1..5,
  DATE is the first day (YYYY-MM-DD), AREA is ``west,south,east,north`` or ``world``.
* ``/api/data_availability/csv/{MAP_KEY}/{SENSOR}``
* ``/mapserver/mapkey_status/?MAP_KEY=...`` (transaction counter; linked from
  https://firms.modaps.eosdis.nasa.gov/api/map_key/)

The API signals client errors with HTTP 400 and a short plain-text message
(e.g. ``Invalid MAP_KEY.``, ``Invalid day range. Expects [1..5].``).
"""
from __future__ import annotations

import io
import threading
import time
from datetime import date
from typing import Callable

import pandas as pd
import requests

from .catalog import BBox, get_source
from .config import Settings
from .logging_utils import get_logger, redact, register_secret

log = get_logger(__name__)

MAX_DAY_RANGE = 5
EXPECTED_HEADER_PREFIX = "latitude,longitude,"


class FirmsAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(redact(message))
        self.status = status


class FirmsAuthError(FirmsAPIError):
    """Invalid or missing MAP_KEY. Not retryable."""


class FirmsRequestError(FirmsAPIError):
    """The request was rejected as invalid (bad source, area, date...). Not retryable."""


class FirmsRateLimitError(FirmsAPIError):
    """Transaction limit exceeded and retries exhausted."""


class FirmsTransientError(FirmsAPIError):
    """Server/network failure that persisted after all retries."""


def _looks_rate_limited(status: int, body: str) -> bool:
    """HTTP 429, or an error status whose message mentions exceeding a limit.

    Successful (200) responses are never treated as rate-limited: the
    mapkey_status JSON legitimately contains the word "transaction_limit".
    """
    if status == 429:
        return True
    if status < 400:
        return False
    b = body.lower()
    return "limit" in b and ("exceed" in b or "transaction" in b)


class FirmsClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not settings.map_key:
            raise FirmsAuthError("No MAP_KEY configured")
        register_secret(settings.map_key)
        self.s = settings
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "FireLens-pipeline/0.1 (python-requests)")
        self._sleep = sleep
        self._clock = clock
        self._last_request_t: float | None = None
        self._requests_since_status = 0
        self.requests_made = 0
        # The client is shared by parallel download workers: request starts are spaced by
        # ``min_request_interval_s`` under one lock, and a transaction-budget pause blocks all workers.
        self._throttle_lock = threading.Lock()
        self._budget_lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def _url(self, path: str) -> str:
        return self.s.base_url.rstrip("/") + path

    def _throttle(self) -> None:
        """Space request *starts* by ``min_request_interval_s`` (thread-safe) and count the request."""
        with self._throttle_lock:
            if self._last_request_t is not None:
                wait = self.s.min_request_interval_s - (self._clock() - self._last_request_t)
                if wait > 0:
                    self._sleep(wait)
            self._last_request_t = self._clock()
            self.requests_made += 1

    def _backoff(self, attempt: int) -> float:
        return min(self.s.backoff_base_s * (2 ** attempt), self.s.backoff_max_s)

    def _get(self, path: str) -> requests.Response:
        """GET with retries on network errors, 5xx and rate limiting."""
        url = self._url(path)
        last_err: Exception | None = None
        for attempt in range(self.s.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.s.request_timeout_s)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                delay = self._backoff(attempt)
                log.warning("Network error (%s) on %s; retry %d/%d in %.0fs",
                            type(e).__name__, redact(path), attempt + 1, self.s.max_retries, delay)
                self._sleep(delay)
                continue

            body = resp.text if resp.content else ""
            if _looks_rate_limited(resp.status_code, body):
                last_err = FirmsRateLimitError(body.strip()[:200] or "rate limited", resp.status_code)
                delay = max(self._backoff(attempt), 60.0)
                log.warning("Rate limited (HTTP %s); waiting %.0fs", resp.status_code, delay)
                self._sleep(delay)
                continue
            if resp.status_code >= 500:
                last_err = FirmsTransientError(f"HTTP {resp.status_code}", resp.status_code)
                delay = self._backoff(attempt)
                log.warning("Server error HTTP %s on %s; retry %d/%d in %.0fs", resp.status_code,
                            redact(path), attempt + 1, self.s.max_retries, delay)
                self._sleep(delay)
                continue
            if resp.status_code in (401, 403) or "invalid map_key" in body.lower():
                raise FirmsAuthError(f"FIRMS rejected the MAP_KEY: {body.strip()[:200]}", resp.status_code)
            if resp.status_code >= 400:
                raise FirmsRequestError(
                    f"FIRMS rejected request {redact(path)} (HTTP {resp.status_code}): {body.strip()[:200]}",
                    resp.status_code)
            return resp

        if isinstance(last_err, FirmsRateLimitError):
            raise last_err
        raise FirmsTransientError(f"Giving up on {redact(path)} after {self.s.max_retries + 1} attempts: "
                                  f"{last_err}")

    # ------------------------------------------------------- transaction budget
    def key_status(self) -> dict:
        resp = self._get(f"/mapserver/mapkey_status/?MAP_KEY={self.s.map_key}")
        return resp.json()

    def wait_for_budget(self) -> None:
        """Poll the server-side transaction counter and pause near the limit.

        Large requests count as several transactions, so counting requests
        locally is not enough.
        """
        # Held for the whole check (and a pause), so parallel workers wait at the budget gate together.
        with self._budget_lock:
            self._requests_since_status += 1
            if self._requests_since_status < self.s.status_check_every:
                return
            self._requests_since_status = 0
            try:
                st = self.key_status()
            except FirmsAPIError as e:  # status endpoint is advisory only
                log.warning("Could not read MAP_KEY status: %s", e)
                return
            limit = int(st.get("transaction_limit", self.s.transaction_limit))
            used = int(st.get("current_transactions", 0))
            log.debug("MAP_KEY transactions used %d/%d", used, limit)
            if used >= self.s.transaction_safety_fraction * limit:
                log.info("Transaction budget %d/%d reached safety threshold; pausing 10 minutes", used, limit)
                self._sleep(600)

    # ---------------------------------------------------------------- endpoints
    def data_availability(self, sensor: str = "ALL") -> pd.DataFrame:
        resp = self._get(f"/api/data_availability/csv/{self.s.map_key}/{sensor}")
        df = pd.read_csv(io.StringIO(resp.text))
        if not {"data_id", "min_date", "max_date"} <= set(df.columns):
            raise FirmsAPIError(f"Unexpected data_availability response: {resp.text[:200]!r}")
        df["min_date"] = pd.to_datetime(df["min_date"]).dt.date
        df["max_date"] = pd.to_datetime(df["max_date"]).dt.date
        return df

    def area_csv(self, source_id: str, bbox: BBox, start: date, day_range: int) -> str:
        """Return the raw CSV text for one Area API request."""
        get_source(source_id)  # validates the identifier locally
        if not 1 <= day_range <= MAX_DAY_RANGE:
            raise ValueError(f"day_range must be 1..{MAX_DAY_RANGE}, got {day_range}")
        self.wait_for_budget()
        path = (f"/api/area/csv/{self.s.map_key}/{source_id}/{bbox.to_api()}/"
                f"{day_range}/{start.isoformat()}")
        resp = self._get(path)
        text = resp.text
        if not text.startswith(EXPECTED_HEADER_PREFIX):
            raise FirmsAPIError(f"Unexpected Area API response for {source_id} {start}: {text[:200]!r}",
                                resp.status_code)
        return text
