"""Small persistent store for one backend process and its worker threads."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def next_month(today=None):
    today = today or datetime.now(timezone.utc).date()
    return f"{today.year + today.month // 12:04d}-{today.month % 12 + 1:02d}"


def request_key(request):
    value = request.model_dump(mode="json")
    if value["start_date"] is not None:
        value.pop("lookback_days")
    value.pop("location_name", None)   # a display label; it does not change the analysis
    if value.get("ml_mode", "off") == "off":
        value.pop("target_month", None)
        value.pop("history_years", None)
    elif value.get("target_month") is None:
        value["target_month"] = next_month()  # the default target changes at month boundaries
    return fingerprint(value)


def location_key(request):
    return fingerprint({key: value for key, value in request.model_dump(mode="json").items()
                        if key in {"latitude", "longitude", "radius_km", "source"}})


class Store:
    def __init__(self, folder):
        Path(folder).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(folder) / "analyses.sqlite3")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, record TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, location TEXT NOT NULL, record TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS datasets_location ON datasets(location, updated_at);
                CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, record TEXT NOT NULL, updated_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_job(self, job, event=True):
        # Job state and its event are committed together for consistent polling/SSE.
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["job_id"], json.dumps(job, allow_nan=False)))
            if event:
                snapshot = {key: value for key, value in job.items() if key != "result"}
                db.execute("INSERT INTO events(job_id, record) VALUES (?, ?)", (job["job_id"], json.dumps(snapshot, allow_nan=False)))

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self):
        with self.connect() as db:
            rows = db.execute("SELECT record FROM jobs ORDER BY rowid DESC").fetchall()
        return [json.loads(row[0]) for row in rows]

    def events(self, job_id, after):
        with self.connect() as db:
            rows = db.execute("SELECT id, record FROM events WHERE job_id=? AND id>? ORDER BY id", (job_id, after)).fetchall()
        return [(seq, json.loads(record)) for seq, record in rows]

    def save_dataset(self, request, rows, result):
        record = {"request": request.model_dump(mode="json"), "detections": rows,
                  "result": result, "cached_at": now()}
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO datasets VALUES (?, ?, ?, ?)",
                       (request_key(request), location_key(request), json.dumps(record, allow_nan=False), record["cached_at"]))
        return record

    def cached(self, request):
        with self.connect() as db:
            row = db.execute("SELECT record FROM datasets WHERE id=?", (request_key(request),)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_location(self, request):
        with self.connect() as db:
            rows = db.execute("SELECT record FROM datasets WHERE location=? ORDER BY updated_at DESC", (location_key(request),)).fetchall()
        for row in rows:
            value = json.loads(row[0])
            coverage = value["result"]["coverage"]
            if request.start_date and coverage["start_date"] != str(request.start_date):
                continue
            if request.end_date and coverage["end_date"] != str(request.end_date):
                continue
            return value
        return None

    def chunk(self, key, ttl_seconds):
        with self.connect() as db:
            row = db.execute("SELECT record, updated_at FROM chunks WHERE id=?", (key,)).fetchone()
        if row and (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds() <= ttl_seconds:
            return json.loads(row[0])
        return None

    def save_chunk(self, key, rows):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO chunks VALUES (?, ?, ?)", (key, json.dumps(rows, allow_nan=False), now()))
