from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    site_password: str = "change-me"
    secret_key: str = "dev-secret-replace-me-please-its-long-enough-32-bytes"
    data_dir: str = "./data"
    devin_api_base: str = "https://api.devin.ai"

    github_token: str = ""
    github_default_repo: str = ""

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_path / 'app.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
