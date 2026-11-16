from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    app_name: str = "StreamWise"
    app_version: str = "0.1.0"
    debug: bool = True
    storage_dir: str = "../storage"
    max_upload_size_mb: int = 500
    frontend_url: str = "http://localhost:5173"
    ffprobe_path: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
