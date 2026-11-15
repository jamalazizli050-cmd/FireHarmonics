"""Logging setup with secret redaction.

Every handler installed here carries a filter that replaces registered secrets
(e.g. the FIRMS MAP_KEY, which appears in request URLs) with ``***``.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_SECRETS: set[str] = set()
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def register_secret(secret: str | None) -> None:
    if secret:
        _SECRETS.add(secret)


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "***")
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _SECRETS:
            msg = record.getMessage()
            record.msg = redact(msg)
            record.args = None
        return True


def setup_logging(level: int | str = logging.INFO, log_file: str | Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if getattr(h, "_firelens", False):
            root.removeHandler(h)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter(_FORMAT))
        h.addFilter(RedactingFilter())
        h._firelens = True  # type: ignore[attr-defined]
        root.addHandler(h)
    # urllib3 logs full URLs (containing the key) at DEBUG level.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
