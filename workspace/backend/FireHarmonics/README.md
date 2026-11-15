# FireHarmonics

A Python backend for NASA fire detection data.

It downloads NASA FIRMS detections, imports historical exports, and returns
JSON through FastAPI. Map-point analyses run as background jobs with progress
and saved results. Each analysis also runs the FireLens ML engine (`firelens/`):
seasonal patterns, trends, anomalies, recurring hotspots and the probability of
at least one satellite hotspot detection in a target month.

The forecast is a satellite-detection probability, not a wildfire-ignition
probability. In the shipped model every forecast uses the validated seasonal
baseline, because pooled XGBoost did not beat it on held-out data.

## Start here

Read one guide at a time.

| You want to... | Open this guide |
| --- | --- |
| Run the backend | [Setup](docs/SETUP.md) (Python 3.13) |
| Use an endpoint | [API reference](docs/API.md) |
| Build the frontend against the API | [Frontend integration](FRONTEND_INTEGRATION.md) |
| Understand the ML engine and its limits | [ML integration](docs/ML_INTEGRATION.md) |
| Change the code | [Contributing](CONTRIBUTING.md) |
| Upload changes and invite a teammate | [GitHub sharing](docs/GITHUB.md) |

The guides use short steps, copyable commands, and expected results.

## Check that it runs

With the server running, open:

<http://127.0.0.1:8000/health>

Expected result: **HTTP 200**.

```json
{
  "status_code": 200,
  "status": "ok",
  "service": "FireHarmonics"
}
```

This checks the backend only. It does not check NASA or your key.

To test other endpoints, open:

<http://127.0.0.1:8000/docs>

## Analyse a map point

Open `/docs` and choose **POST /api/v1/analyses**.

Use this request body:

```json
{
  "latitude": -13,
  "longitude": 24,
  "radius_km": 25
}
```

Copy the returned `job_id`.
Check progress with **GET /api/v1/analyses/{job_id}**.
When it is complete, read **GET /api/v1/analyses/{job_id}/result**.

The [API guide](docs/API.md) explains SSE events, retries, archive imports,
and the limits of each statistic.

## Where the files are

| File | Purpose |
| --- | --- |
| `FireDetectionAPP/main.py` | API endpoints and application startup |
| `FireDetectionAPP/nasa.py` | NASA requests and CSV parsing |
| `FireDetectionAPP/jobs.py` | Background jobs, retries, cache reuse, ML stage and result assembly |
| `FireDetectionAPP/ml_engine.py` | The only module that calls FireLens: status, analysis, error mapping, JSON safety |
| `FireDetectionAPP/satellites.py` | Provider/instrument provenance and regional harmonization products |
| `FireDetectionAPP/analytics.py` | Radius filtering and descriptive statistics |
| `FireDetectionAPP/archives.py` | CSV, JSON, GeoJSON, and shapefile imports |
| `FireDetectionAPP/storage.py` | Persistent SQLite results and progress events |
| `FireDetectionAPP/schemas.py` | Request and response models |
| `FireDetectionAPP/regions.py` | Five example map locations |
| `FireDetectionAPP/.env.example` | Configuration example with a placeholder |
| `FireDetectionAPP/.env` | Your private NASA key; excluded from Git |
| `firelens/` | FireLens ML engine, trained model (`data/models/global`), harmonization exports, its own tests |
| `requirements.txt` | Python dependencies (`requirements-lock.txt`: exact verified versions) |
| `tests/` | API, worker, geospatial, statistics, archive and ML-integration tests |

## Before sharing this repository

A NASA key is present in the existing Git history.

It is in commit `ca7485f` (`FireDetectionAPP/main.py`). Treat it as public:
revoke it and request a new one, even if the history is never rewritten.
Replace that key before sharing the repository further.
The steps are in [GitHub sharing](docs/GITHUB.md#1-replace-the-exposed-key).
