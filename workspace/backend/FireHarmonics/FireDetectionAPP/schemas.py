"""Public request and response schemas.

New fields are optional with explicit defaults, so results cached before the ML
integration still validate. Probabilities are either genuine engine output or
``None``; there is no fallback value.
"""
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TARGET_MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


class Source(str, Enum):
    landsat = "LANDSAT_NRT"
    modis = "MODIS_NRT"
    modis_standard = "MODIS_SP"
    noaa20 = "VIIRS_NOAA20_NRT"
    noaa20_standard = "VIIRS_NOAA20_SP"
    noaa21 = "VIIRS_NOAA21_NRT"
    noaa21_standard = "VIIRS_NOAA21_SP"
    snpp = "VIIRS_SNPP_NRT"
    snpp_standard = "VIIRS_SNPP_SP"


DEFAULT_SOURCE = Source.noaa20


class HealthResponse(BaseModel):
    status_code: Literal[200] = 200
    status: Literal["ok"] = "ok"
    service: Literal["FireHarmonics"] = "FireHarmonics"


class ParsedDataResponse(BaseModel):
    status_code: Literal[200] = 200
    source: str
    count: int
    rows: list[dict[str, str]]


class FireDataResponse(ParsedDataResponse):
    area: str
    date: str | None
    day_range: int


class ErrorResponse(BaseModel):
    status_code: int
    detail: str
    recoverable: bool = False


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=25, gt=0, le=500)
    source: Source = DEFAULT_SOURCE
    lookback_days: int = Field(default=30, ge=1, le=3650)
    start_date: date | None = None
    end_date: date | None = None
    # ML engine (FireLens). Relative-date rules for target_month are checked at submission
    # time, not here, so persisted jobs stay valid after the month passes.
    ml_mode: Literal["download", "cache_only", "off"] = Field(
        default="download", description="download: fetch missing S-NPP VIIRS data; cache_only: never download; off: descriptive only")
    target_month: str | None = Field(default=None, pattern=TARGET_MONTH, description="YYYY-MM; default: next calendar month (UTC)")
    history_years: int = Field(default=10, ge=3, le=14, description="Years of history for the ML analysis")
    location_name: str | None = Field(default=None, max_length=160, description="Display name supplied by the client")

    @model_validator(mode="after")
    def validate_dates(self):
        today = datetime.now(timezone.utc).date()
        if any(value and value > today for value in (self.start_date, self.end_date)):
            raise ValueError("Dates must not be in the future")
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be on or before end_date")
            if (self.end_date - self.start_date).days >= 3650:
                raise ValueError("An analysis can cover at most 3650 days")
        return self


class JobError(BaseModel):
    code: str
    detail: str
    recoverable: bool
    status_code: int
    retry_after_seconds: int | None = None
    retry_not_before: str | None = None


class JobResponse(BaseModel):
    status_code: Literal[200] = 200
    job_id: str
    status: Literal["queued", "running", "paused", "completed", "failed"]
    progress: int = Field(ge=0, le=100)
    stage: str
    eta_seconds: int | None = None
    nasa_pause_seconds: int | None = None
    created_at: str
    updated_at: str
    request: AnalysisRequest
    cache_hit: bool = False
    attempt: int = 1
    error: JobError | None = None
    links: dict[str, str]
    message: str | None = None
    result_status: Literal["complete", "partial", "descriptive_only"] | None = None


class AcceptedJobResponse(JobResponse):
    status_code: Literal[202] = 202


class Prediction(BaseModel):
    """Probability of >= 1 satellite hotspot detection in the disc during target_month.

    Not the probability of a wildfire ignition. Extra engine fields (seasonal_norm,
    validation_evidence, uncertainty, model_comparison, ...) pass through unchanged.
    """
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    status: str = "unavailable"
    probability: float | None = Field(default=None, ge=0, le=1)
    method: Literal["xgboost_pooled", "seasonal_baseline", "unavailable"] = "unavailable"
    target_month: str | None = None
    explanation: str = "A prediction model has not been added. Observed detections are not a forecast."
    error: dict[str, Any] | None = None


def _not_run_ml():
    return {"status": "not_run", "mode": None, "error": None, "engine": None,
            "explanation": "This result was produced without the ML engine (archive import or an earlier backend version)."}


class AnalysisResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    schema_version: str | None = None
    status: Literal["complete", "partial", "descriptive_only"] | None = None
    location: dict[str, Any]
    source: str
    provenance: dict[str, Any] | None = None
    coverage: dict[str, Any]
    detection_count: int
    historical_activity: dict[str, Any]
    seasonal_patterns: dict[str, Any]
    long_term_trends: dict[str, Any]
    anomalies: dict[str, Any]
    hotspots: list[dict[str, Any]]
    prediction: Prediction = Field(default_factory=Prediction)
    ml_analysis: dict[str, Any] = Field(default_factory=_not_run_ml)
    satellite_analysis: dict[str, Any] | None = None
    overview: dict[str, Any] | None = None
    components: dict[str, Any] | None = None
    warnings: list[str]
