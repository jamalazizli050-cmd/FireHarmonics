"""Project configuration.

The FIRMS MAP_KEY is read from the environment (optionally populated from a
``.env`` file via python-dotenv). It is never hard-coded and never included in
``repr()`` output or log messages.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_KEYS = {"", "your_map_key_here", "changeme"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    map_key: str = field(repr=False)
    base_url: str = "https://firms.modaps.eosdis.nasa.gov"
    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path = PROJECT_ROOT / "reports"
    # FIRMS allows 5000 transactions per 10-minute window per MAP_KEY. Large
    # requests (big areas / many days) count as several transactions, so the
    # client also polls the server-side counter (see FirmsClient).
    transaction_limit: int = 5000
    transaction_safety_fraction: float = 0.8
    min_request_interval_s: float = 0.5
    status_check_every: int = 20
    request_timeout_s: float = 300.0
    max_retries: int = 5
    backoff_base_s: float = 2.0
    backoff_max_s: float = 120.0
    # Parallel Area API requests (request starts stay spaced by min_request_interval_s and the
    # transaction-budget guard still applies). 1 restores strictly sequential downloads.
    download_workers: int = 4
    # Bulk source for completed years of VIIRS_SNPP_SP: FIRMS yearly per-country archives
    # (https://firms.modaps.eosdis.nasa.gov/data/country/), see firelens.country_archive. Off for Settings built
    # directly (tests, offline tools); load_settings() turns it on unless FIRELENS_COUNTRY_ARCHIVE=0.
    use_country_archive: bool = False
    archive_spot_check_chunks: int = 4

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def figures_dir(self) -> Path:
        return self.reports_dir / "figures"

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)


def load_settings(env_file: str | os.PathLike | None = None, require_key: bool = True,
                  **overrides) -> Settings:
    """Load settings from the environment / ``.env``.

    ``require_key=False`` lets offline steps (processing, aggregation, tests)
    run without a key.
    """
    load_dotenv(Path(env_file) if env_file else PROJECT_ROOT / ".env", override=False)
    key = os.getenv("NASA_FIRMS_MAP_KEY", "").strip()
    if key in PLACEHOLDER_KEYS:
        if require_key:
            raise ConfigError(
                "NASA_FIRMS_MAP_KEY is not set. Copy .env.example to .env and add your key "
                "(https://firms.modaps.eosdis.nasa.gov/api/map_key/)."
            )
        key = ""
    kwargs: dict = {"map_key": key}
    if os.getenv("FIRELENS_DATA_DIR"):
        kwargs["data_dir"] = Path(os.environ["FIRELENS_DATA_DIR"])
    if os.getenv("FIRELENS_REPORTS_DIR"):
        kwargs["reports_dir"] = Path(os.environ["FIRELENS_REPORTS_DIR"])
    if os.getenv("FIRELENS_DOWNLOAD_WORKERS", "").strip():
        workers = int(os.environ["FIRELENS_DOWNLOAD_WORKERS"])
        if not 1 <= workers <= 8:
            raise ConfigError("FIRELENS_DOWNLOAD_WORKERS must be between 1 and 8")
        kwargs["download_workers"] = workers
    kwargs["use_country_archive"] = os.getenv("FIRELENS_COUNTRY_ARCHIVE", "1").strip().lower() not in {"0", "false", "no", "off"}
    kwargs.update(overrides)
    return Settings(**kwargs)
