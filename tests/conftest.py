from __future__ import annotations


import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SITE_PASSWORD", "test-password")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-must-be-long-enough-32-bytes-12345")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from app import config as cfg, db as dbmod

    cfg.get_settings.cache_clear()
    # reset engine each test so the new DATA_DIR takes effect
    dbmod._engine = None  # type: ignore[attr-defined]
    dbmod._SessionLocal = None  # type: ignore[attr-defined]
    yield
    cfg.get_settings.cache_clear()
    dbmod._engine = None  # type: ignore[attr-defined]
    dbmod._SessionLocal = None  # type: ignore[attr-defined]
