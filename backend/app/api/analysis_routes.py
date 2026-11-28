"""API routes for Content Understanding metadata generation and retrieval."""

from fastapi import Depends

from ..config import Settings, get_settings
from ..config.settings import resolve_storage_directory
from ..modules.content_analysis import AnalysisConfig, ContentAnalyzer, build_router


def get_content_analyzer(settings: Settings = Depends(get_settings)) -> ContentAnalyzer:
    """Dependency provider aligning ContentAnalyzer storage and tool settings with the main app."""
    base_config = AnalysisConfig()
    resolved_storage = resolve_storage_directory(settings.storage_dir)
    config = base_config.model_copy(
        update={
            "storage_dir": resolved_storage,
            "ffmpeg_path": settings.ffmpeg_path or base_config.ffmpeg_path,
            "ffprobe_path": settings.ffprobe_path or base_config.ffprobe_path,
            "tesseract_cmd": settings.tesseract_cmd or base_config.tesseract_cmd,
        }
    )
    return ContentAnalyzer(config=config)


router = build_router(analyzer_dependency=get_content_analyzer)
