from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    app_name: str = "StreamWise"
    app_version: str = "0.1.0"
    debug: bool = True
    storage_dir: str = "../storage"
    max_upload_size_mb: int = 500
    frontend_url: str = "http://localhost:5173"
    ffprobe_path: Optional[str] = None
    ffmpeg_path: Optional[str] = None
    transcoding_timeout_seconds: int = 120
    hls_segment_duration_seconds: int = 4
    hls_timeout_seconds: int = 180
    tesseract_cmd: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=(str(_BACKEND_ENV_FILE), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()


def resolve_storage_directory(storage_dir_str: str) -> Path:
    """Resolve the shared base storage directory safely across working directories."""
    if not storage_dir_str or not storage_dir_str.strip():
        raise ValueError("storage_dir cannot be empty.")
    p = Path(storage_dir_str.strip())
    if p.is_absolute():
        resolved_storage = p
    elif (Path.cwd() / p).resolve().exists():
        resolved_storage = (Path.cwd() / p).resolve()
    else:
        backend_dir = Path(__file__).resolve().parent.parent.parent
        if (backend_dir / p).resolve().exists():
            resolved_storage = (backend_dir / p).resolve()
        else:
            repo_storage = (backend_dir.parent / "storage").resolve()
            if p.parts in {("..", "storage"), ("storage",)} and repo_storage.exists():
                resolved_storage = repo_storage
            elif str(p).startswith(".."):
                resolved_storage = (backend_dir / p).resolve()
            else:
                resolved_storage = (Path.cwd() / p).resolve()

    if resolved_storage.name in {"uploads", "processed"}:
        return resolved_storage.parent
    return resolved_storage
