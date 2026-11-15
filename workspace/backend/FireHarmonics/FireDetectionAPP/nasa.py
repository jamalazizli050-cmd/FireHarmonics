"""NASA's documented CSV APIs; private upstream URLs never enter responses."""
import csv
import io
import os
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from fastapi import HTTPException

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
AVAILABILITY_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv"
ARCHIVE_URL = "https://firms.modaps.eosdis.nasa.gov/download/"


class FIRMSException(HTTPException):
    def __init__(self, status_code, detail, *, recoverable=False, code="upstream_error", retry_after=None):
        super().__init__(status_code=status_code, detail=detail)
        self.recoverable = recoverable
        self.code = code
        self.retry_after = retry_after


def get_map_key() -> str:
    key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    if not key or key == "your_nasa_firms_map_key_here":
        raise FIRMSException(
            500,
            "NASA_FIRMS_MAP_KEY is not configured. Set it in FireDetectionAPP/.env or your environment.",
            recoverable=True, code="configuration_missing",
        )
    return key


def parse_csv(response_text: str) -> list[dict[str, str]]:
    text = response_text.lstrip("\ufeff").strip()
    if not text or text.lower().rstrip(".") == "no data found":
        return []
    lower = text.lower()
    if any(term in lower for term in ("exceeded", "too many requests", "rate limit")) and "," not in text.splitlines()[0]:
        raise FIRMSException(502, "NASA FIRMS transaction limit reached.", recoverable=True, code="rate_limited", retry_after=60)
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        columns = reader.fieldnames
        if (
            not columns or len(columns) < 2
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in columns)
            or len(set(columns)) != len(columns)
            or lower.startswith("invalid")
        ):
            raise ValueError("Invalid CSV headers")
        rows = list(reader)
        if any(None in row or None in row.values() for row in rows):
            raise ValueError("CSV row does not match headers")
        return rows
    except (csv.Error, ValueError) as exc:
        raise FIRMSException(502, "FIRMS returned an error or malformed CSV data.", code="invalid_csv") from exc


def retry_delay(value: str | None) -> int:
    try:
        seconds = int(value or "")
    except ValueError:
        try:
            seconds = int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            seconds = 5
    # Do not retry before NASA's requested delay. Very long delays are left for a manual retry.
    return max(1, seconds)


def fetch_csv(url: str, timeout: float) -> list[dict[str, str]]:
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise FIRMSException(504, "The NASA FIRMS request timed out.", recoverable=True, code="timeout") from exc
    except httpx.RequestError as exc:
        raise FIRMSException(502, "Could not connect to NASA FIRMS.", recoverable=True, code="connection_error") from exc
    if response.status_code != 200:
        code = response.status_code
        raise FIRMSException(
            502, f"NASA FIRMS returned HTTP {code}.",
            recoverable=code == 429 or code >= 500,
            code="rate_limited" if code == 429 else "upstream_http_error",
            retry_after=retry_delay(response.headers.get("Retry-After")) if code == 429 or code == 503 else None,
        )
    return parse_csv(response.text)


def validate_area(area: str) -> str:
    if area == "world":
        return area
    try:
        parts = area.split(",")
        if len(parts) != 4:
            raise ValueError
        west, south, east, north = map(float, parts)
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "area must be 'world' or west,south,east,north with valid ordered bounds") from exc
    return ",".join(f"{value:.8f}".rstrip("0").rstrip(".") for value in (west, south, east, north))


def fetch_area(source: str, area: str, days: int, start: date | str | None = None):
    area = validate_area(area)
    url = f"{FIRMS_BASE_URL}/{get_map_key()}/{source}/{area}/{days}"
    if start is not None:
        url += f"/{start}"
    rows = fetch_csv(url, 60.0)
    if rows and not {"latitude", "longitude"}.issubset(rows[0]):
        raise FIRMSException(502, "FIRMS returned CSV without detection coordinates.", code="invalid_csv")
    return rows


def fetch_availability(source: str):
    return fetch_csv(f"{AVAILABILITY_BASE_URL}/{get_map_key()}/{source}", 30.0)


def available_window(source: str) -> tuple[date, date]:
    rows = fetch_availability(source)
    try:
        selected = [row for row in rows if row.get("data_id", source) == source]
        windows = [(date.fromisoformat(row["min_date"]), date.fromisoformat(row["max_date"])) for row in selected]
        if not windows:
            raise KeyError("no availability")
        first, last = min(start for start, _ in windows), max(end for _, end in windows)
        if first > last:
            raise ValueError("Invalid availability range")
        return first, last
    except (KeyError, ValueError, TypeError) as exc:
        raise FIRMSException(502, "FIRMS did not provide a valid availability date range.", code="availability_missing") from exc
