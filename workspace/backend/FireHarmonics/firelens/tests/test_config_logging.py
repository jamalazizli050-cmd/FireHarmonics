import logging

import pytest

from firelens.catalog import BBox, resolve_region
from firelens.config import ConfigError, load_settings
from firelens.logging_utils import RedactingFilter, register_secret


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("NASA_FIRMS_MAP_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("NASA_FIRMS_MAP_KEY=your_map_key_here\n")
    with pytest.raises(ConfigError):
        load_settings(env)
    assert load_settings(env, require_key=False).map_key == ""


def test_key_loaded_from_env_file_and_hidden_from_repr(tmp_path, monkeypatch):
    monkeypatch.delenv("NASA_FIRMS_MAP_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("NASA_FIRMS_MAP_KEY=abc123secret\n")
    s = load_settings(env)
    assert s.map_key == "abc123secret"
    assert "abc123secret" not in repr(s)


def test_log_redaction():
    register_secret("supersecretkey")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "GET /api/area/csv/%s/MODIS_SP", ("supersecretkey",), None)
    RedactingFilter().filter(rec)
    assert "supersecretkey" not in rec.getMessage() and "***" in rec.getMessage()


def test_bbox_validation_and_parsing():
    assert BBox.parse("world").to_api() == "world"
    assert BBox.parse("-10,20,30,40").to_api() == "-10,20,30,40"
    with pytest.raises(ValueError):
        BBox(10, 0, -10, 5)
    with pytest.raises(ValueError):
        BBox(-10, 0, 10, 95)
    tiles = BBox.parse("world").split(4, 2)
    assert len(tiles) == 8 and tiles[0].west == -180
    assert resolve_region("california").name == "california"
    assert resolve_region("-10,20,30,40").name == "bbox_-10_20_30_40"
