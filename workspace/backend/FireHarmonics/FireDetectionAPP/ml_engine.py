"""Adapter between the FireHarmonics backend and the FireLens inference engine.

This is the only backend module that imports FireLens. Route handlers and the
job service talk to an *engine object* with two methods:

* ``status()``  - whether the package imports, the trained model loads and a
  NASA key is configured (never the key itself);
* ``analyze()`` - run one location analysis in the calling (worker) thread and
  return the engine's own result, made strictly JSON-safe.

FireLens is located by ``FIRELENS_ROOT`` (default: ``<repo>/firelens``) and is
imported lazily, so the descriptive backend keeps working when it is absent.
The engine's scientific logic is not modified here; this module only calls it,
maps its errors and normalises its output for the public API.
"""
from __future__ import annotations

import importlib
import math
import os
import re
import sys
import threading
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRELENS_ROOT = REPO_ROOT / "firelens"
METHODS = {"xgboost_pooled", "seasonal_baseline"}
ML_MODES = ("download", "cache_only", "off")
FORECAST_SERIES = {
    "provider": "NASA FIRMS",
    "instrument": "VIIRS",
    "platform": "Suomi NPP",
    "products": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"],
    "resolution_m": 375,
    "description": "S-NPP VIIRS 375 m vegetation-fire detections: standard processing (SP) up to its last "
                   "available day, near-real-time (NRT) afterwards. MODIS and VIIRS counts are never combined.",
}
_URL = re.compile(r"https?://\S+")


class EngineError(RuntimeError):
    """A failed engine call, already safe to persist and show to clients."""

    def __init__(self, code: str, detail: str, recoverable: bool):
        super().__init__(detail)
        self.code, self.detail, self.recoverable = code, detail, recoverable


# ------------------------------------------------------------------ JSON safety
def json_safe(value):
    """Recursively convert to strict-JSON builtins.

    NumPy scalars become Python numbers, timestamps/periods become ISO strings,
    and NaN/inf become ``None`` (a missing value, never a fabricated number).
    """
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):   # pandas NaT is a datetime subclass: missing, not a date
        return None if _is_missing(value) else value.isoformat()
    kind = type(value).__module__.split(".")[0]
    if kind == "numpy":
        if getattr(value, "ndim", 0):                      # array
            return json_safe(value.tolist())
        if hasattr(value, "item"):                           # NumPy scalar
            item = value.item()
            if isinstance(item, (int, float, bool, str)) or item is None:
                return json_safe(item)
            return str(item)
    if kind == "pandas":
        if hasattr(value, "isoformat") and not _is_missing(value):
            return value.isoformat()
        if _is_missing(value):
            return None
        return str(value)
    return str(value)


def _is_missing(value) -> bool:
    try:
        return bool(value != value)   # NaT / NA compare unequal to themselves
    except (TypeError, ValueError):
        return False


def _scrub(message: str, secret: str | None) -> str:
    text = str(message)
    if secret:
        text = text.replace(secret, "<redacted>")
    return _URL.sub("<url>", text)[:500]


# ------------------------------------------------------------------ engines
class DisabledEngine:
    """Used when FIREHARMONICS_ML_ENGINE=disabled. Never runs anything."""

    name = "disabled"
    available = False

    def status(self, load_model: bool = True) -> dict:
        return {"engine": "firelens", "available": False, "status": "disabled",
                "reason": "The ML engine is disabled by configuration (FIREHARMONICS_ML_ENGINE=disabled).",
                "package": None, "model": None, "firms_key_configured": None}

    def analyze(self, **kwargs) -> dict:
        raise EngineError("engine_disabled", "The ML engine is disabled by configuration.", False)


class FireLensEngine:
    """Lazy, thread-safe access to the FireLens package and its trained model."""

    name = "firelens"

    def __init__(self, root: str | os.PathLike | None = None, settings=None):
        self.root = Path(root or os.getenv("FIRELENS_ROOT") or DEFAULT_FIRELENS_ROOT)
        self._settings = settings          # tests pass explicit FireLens Settings
        self._lock = threading.Lock()
        self._modules: dict | None = None
        self._import_error: str | None = None

    # ------------------------------------------------------------ import
    def _load(self) -> dict | None:
        with self._lock:
            if self._modules is not None or self._import_error is not None:
                return self._modules
            source = self.root / "src"
            if source.is_dir() and str(source) not in sys.path:
                sys.path.insert(0, str(source))
            try:
                self._modules = {name: importlib.import_module(f"firelens.{name}")
                                 for name in ("inference", "global_model", "config", "jobs", "progress")}
                self._modules["package"] = importlib.import_module("firelens")
            except Exception as exc:  # ImportError, or a blocked native DLL (OSError)
                self._import_error = f"{type(exc).__name__}: {exc}"[:500]
            return self._modules

    @property
    def available(self) -> bool:
        return self._load() is not None

    def settings(self):
        modules = self._load()
        if modules is None:
            raise EngineError("engine_not_installed", f"FireLens could not be imported ({self._import_error}).", False)
        return self._settings or modules["config"].load_settings(require_key=False)

    # ------------------------------------------------------------ status
    def status(self, load_model: bool = True) -> dict:
        modules = self._load()
        if modules is None:
            return {"engine": "firelens", "available": False, "status": "unavailable",
                    "reason": "The FireLens package could not be imported.",
                    "package": {"importable": False, "root": _relative(self.root), "error": self._import_error},
                    "model": None, "firms_key_configured": bool(os.getenv("NASA_FIRMS_MAP_KEY", "").strip())}
        settings = self.settings()
        inference, model_module = modules["inference"], modules["global_model"]
        folder = model_module.model_dir(settings)
        artifact = folder / f"{model_module.MODEL_NAME}.joblib"
        metadata = folder / f"{model_module.MODEL_NAME}.json"
        model = {"name": model_module.MODEL_NAME, "artifact_present": artifact.exists() and metadata.exists(),
                 "artifact": _relative(artifact), "loadable": None, "error": None}
        if load_model and model["artifact_present"]:
            try:
                forecaster, meta = model_module.load(settings)
                booster_features = list(forecaster.booster.get_booster().feature_names or [])
                model.update({
                    "loadable": True, "type": "XGBoost classifier with isotonic calibration (pooled global model)",
                    "target": meta.get("target"), "design": meta.get("design_variant"),
                    "n_features": len(forecaster.features),
                    "features_consistent": forecaster.features == meta.get("features") == booster_features,
                    "leads_months": meta.get("leads"), "radii_km_trained": meta.get("radii_km_trained"),
                    "training_regions": meta.get("training_regions"), "training_period": meta.get("training_period"),
                    "method_gate": _gate_summary(meta.get("method_gate", {})),
                })
            except Exception as exc:
                model.update({"loadable": False, "error": f"{type(exc).__name__}: {exc}"[:300]})
        ready = model["artifact_present"] and model["loadable"] is not False
        return {"engine": "firelens", "available": ready, "status": "available" if ready else "degraded",
                "reason": None if ready else "The trained global model is missing or cannot be loaded.",
                "package": {"importable": True, "version": getattr(modules["package"], "__version__", None),
                            "schema_version": inference.SCHEMA_VERSION, "engine_version": inference.ENGINE_VERSION,
                            "root": _relative(self.root)},
                "model": model, "firms_key_configured": bool(settings.map_key),
                "forecast_horizon_months": inference.FORECAST_HORIZON_MONTHS,
                "radius_km_range": [inference.MIN_RADIUS_KM, inference.MAX_RADIUS_KM],
                "history_years_range": [inference.MIN_HISTORY_YEARS, inference.MAX_HISTORY_YEARS],
                "series": FORECAST_SERIES}

    # ------------------------------------------------------------ analysis
    def analyze(self, *, latitude, longitude, radius_km, target_month=None, history_years=10,
                allow_download=True, progress_callback=None) -> dict:
        """Run FireLens synchronously (call from a worker thread, never the event loop)."""
        settings = self.settings()
        modules = self._modules
        inference = modules["inference"]
        try:
            request = inference.validate_request(latitude, longitude, radius_km, target_month, history_years)
        except inference.InvalidRequestError as exc:
            raise EngineError("invalid_request", _scrub(exc, settings.map_key), False) from exc
        tracker = modules["progress"].ProgressTracker(callbacks=[progress_callback] if progress_callback else None)
        try:
            result = inference.run_analysis(request, tracker, settings, allow_download=allow_download)
        except Exception as exc:
            code, recoverable = modules["jobs"].classify_error(exc)
            if isinstance(exc, FileNotFoundError):
                code = "model_unavailable"
            raise EngineError(code, _scrub(exc, settings.map_key), recoverable) from exc
        return json_safe(result)


def _gate_summary(gate: dict) -> dict:
    """Which method the pre-registered validation rule selects, per region and lead."""
    out = {"in_domain": {}, "out_of_domain": {}}
    for region, leads in gate.get("in_domain", {}).items():
        out["in_domain"][region] = {lead: item.get("method") for lead, item in leads.items()}
    out["out_of_domain"] = {lead: item.get("method") for lead, item in gate.get("out_of_domain", {}).items()}
    return out


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).name


def create_engine():
    choice = os.getenv("FIREHARMONICS_ML_ENGINE", "firelens").strip().lower()
    if choice in {"disabled", "off", "none"}:
        return DisabledEngine()
    if choice != "firelens":
        raise RuntimeError("FIREHARMONICS_ML_ENGINE must be 'firelens' or 'disabled'")
    return FireLensEngine()


# ------------------------------------------------------------------ normalisation
def not_run_prediction(status: str, explanation: str, target_month: str | None = None, error: dict | None = None) -> dict:
    return {"status": status, "probability": None, "method": "unavailable", "target_month": target_month,
            "explanation": explanation, "error": error}


def normalize_prediction(raw: dict) -> dict:
    """Engine prediction block -> public schema. Never invents a probability."""
    probability = raw.get("probability")
    ok = raw.get("status") == "ok" and isinstance(probability, (int, float)) and 0 <= probability <= 1
    method = raw.get("method") if ok and raw.get("method") in METHODS else "unavailable"
    if ok:
        explanation = raw.get("method_reason") or "Probability from the FireLens engine."
    else:
        explanation = raw.get("explanation") or raw.get("reason") or "No probability was issued for this location."
    return {**raw, "status": raw.get("status") or "unavailable", "probability": probability if ok else None,
            "method": method, "explanation": explanation, "error": None}


def ml_block(raw: dict, *, mode: str) -> dict:
    """Engine result (minus prediction) as the public ``ml_analysis`` block."""
    keep = ("historical_activity", "seasonal_patterns", "long_term_trends", "anomalies", "anomaly_detection",
            "hotspots", "hotspot_analysis", "data_coverage", "harmonization", "unified_calendar",
            "current_conditions", "limitations", "warnings")
    return {"status": raw.get("status"), "mode": mode, "error": None,
            "engine": {"name": "firelens", "schema_version": raw.get("schema_version"),
                       "engine_version": raw.get("engine_version"), "generated_at": raw.get("generated_at")},
            "request": raw.get("request"), "series": FORECAST_SERIES,
            **{key: raw.get(key) for key in keep}}


def empty_ml_block(status: str, explanation: str, *, mode: str | None = None, error: dict | None = None) -> dict:
    return {"status": status, "mode": mode, "error": error, "explanation": explanation, "engine": None,
            "series": FORECAST_SERIES}
