# Backend API contract — investigate clone, do not guess

Source of truth for integration: `workspace/backend/FireHarmonics/FRONTEND_INTEGRATION.md` **after cloning**.

According to earlier Claude handoff, the published branch has:
- `POST /api/v1/analyses` for analysis start.
- `GET /api/v1/analyses/{job_id}` for status.
- `GET /api/v1/analyses/{job_id}/events` for SSE progress.
- `GET /api/v1/analyses/{job_id}/result` for completion result.
- Export endpoint, reported as `/export`; confirm actual path and query syntax.
- `/api/v1/harmonization`, `/api/v1/harmonization/regions/{id}`; verify.
- `/api/v1/model`, `/api/v1/models`; verify.

Reported result blocks: `prediction`, `ml_analysis`, `satellite_analysis`, `overview`, `components`, `provenance`.
Reported request fields: `ml_mode`, `target_month`, `history_years`, `location_name` and coordinates / radius.
Do not hardcode undocumented names. Some results may be `partial` with genuine descriptive stats and `probability: null`.

## UI contract expectations
- Frontend only displays true values and null/unavailable states.
- Keep real source selection/date selection synchronized between globe panel and report, without implying a backend transformation that does not exist.
- Prediction selected by backend may be seasonal baseline because XGBoost failed validation. User wants XGBoost more visually prominent as **experimental**, with unmistakable limitations and the validated baseline alongside.
- Generate PDF client-side only in future implementation; JSON/CSV should use real API export where supported.
- No live NASA verification was performed as of reported integration; new API key required before testing.
