"""FastAPI router and transport-agnostic handlers for the Content Understanding API."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import ValidationError

from .analyzer import ContentAnalyzer, validate_video_id
from .config import AnalysisConfig, get_analysis_config
from .frame_extractor import MediaExtractionError
from .models import AnalyzeVideoRequest, SegmentMetadata, VideoAnalysisResult

logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when fastapi is absent
    FASTAPI_AVAILABLE = False
    APIRouter = Any  # type: ignore[misc, assignment]
    FastAPI = Any  # type: ignore[misc, assignment]


class AnalysisAPIError(Exception):
    """Transport-agnostic API error carrying an HTTP status code and detail message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def get_analyzer(config: Optional[AnalysisConfig] = None) -> ContentAnalyzer:
    """Construct a ContentAnalyzer using supplied or default configuration."""
    return ContentAnalyzer(config=config or get_analysis_config())


def _resolve_confined_path(raw_path: str, base_dir: Path, anchor_dir: Path, field_name: str) -> Path:
    """Resolve an API-supplied path and ensure it remains within anchor_dir."""
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve()
    anchor_resolved = anchor_dir.resolve()
    if not resolved.is_relative_to(anchor_resolved):
        raise ValueError(f"{field_name} must remain within '{anchor_resolved}'.")
    return resolved


def analyze_video_handler(
    video_id: str,
    request: Optional[AnalyzeVideoRequest] = None,
    analyzer: Optional[ContentAnalyzer] = None,
) -> VideoAnalysisResult:
    """Synchronous service handler for POST /analysis/analyze/{video_id}."""
    active_analyzer = analyzer or get_analyzer()
    frames_override = request.frames_per_segment if request else None

    try:
        validate_video_id(video_id)
        processed_root = active_analyzer.config.resolve_processed_root().resolve()
        storage_root = (
            processed_root.parent
            if processed_root.name == active_analyzer.config.processed_subdir
            else processed_root
        ).resolve()
        video_dir = active_analyzer.get_video_processed_dir(video_id)

        segments_dir = (
            _resolve_confined_path(
                request.segments_dir,
                base_dir=video_dir,
                anchor_dir=storage_root,
                field_name="segments_dir",
            )
            if request and request.segments_dir
            else None
        )
        output_path = (
            _resolve_confined_path(
                request.output_path,
                base_dir=video_dir,
                anchor_dir=video_dir,
                field_name="output_path",
            )
            if request and request.output_path
            else None
        )

        return active_analyzer.analyze_video(
            video_id=video_id,
            segments_dir=segments_dir,
            output_path=output_path,
            frames_per_segment=frames_override,
        )
    except FileNotFoundError as exc:
        logger.warning("Analysis failed (404) for video_id='%s': %s", video_id, exc)
        raise AnalysisAPIError(status_code=404, detail=str(exc)) from exc
    except (ValueError, MediaExtractionError) as exc:
        logger.warning("Analysis failed (422) for video_id='%s': %s", video_id, exc)
        raise AnalysisAPIError(status_code=422, detail=str(exc)) from exc
    except TimeoutError as exc:
        logger.error("Analysis timed out (504) for video_id='%s': %s", video_id, exc)
        raise AnalysisAPIError(status_code=504, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.error("Analysis failed (503) for video_id='%s': %s", video_id, exc)
        raise AnalysisAPIError(status_code=503, detail=str(exc)) from exc


def get_metadata_handler(
    video_id: str,
    analyzer: Optional[ContentAnalyzer] = None,
) -> VideoAnalysisResult:
    """Synchronous service handler for GET /analysis/metadata/{video_id}."""
    active_analyzer = analyzer or get_analyzer()
    try:
        return active_analyzer.load_metadata(video_id=video_id)
    except FileNotFoundError as exc:
        raise AnalysisAPIError(status_code=404, detail=str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisAPIError(status_code=500, detail=f"Corrupted metadata: {exc}") from exc
    except ValueError as exc:
        raise AnalysisAPIError(status_code=422, detail=str(exc)) from exc


def get_segment_metadata_handler(
    video_id: str,
    segment_id: str,
    analyzer: Optional[ContentAnalyzer] = None,
) -> SegmentMetadata:
    """Synchronous service handler for GET /analysis/metadata/{video_id}/{segment_id}."""
    active_analyzer = analyzer or get_analyzer()
    try:
        return active_analyzer.load_segment_metadata(video_id=video_id, segment_id=segment_id)
    except (FileNotFoundError, KeyError) as exc:
        raise AnalysisAPIError(status_code=404, detail=str(exc)) from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisAPIError(status_code=500, detail=f"Corrupted metadata: {exc}") from exc
    except ValueError as exc:
        raise AnalysisAPIError(status_code=422, detail=str(exc)) from exc


def health_handler(analyzer: Optional[ContentAnalyzer] = None) -> Dict[str, Any]:
    """Return runtime status and tool capabilities for Content Understanding."""
    active_analyzer = analyzer or get_analyzer()
    return {
        "module": "content_understand",
        "status": "healthy",
        "capabilities": active_analyzer.check_capabilities(),
    }


def build_router(analyzer_dependency: Any = None) -> Any:
    """Build the FastAPI APIRouter for /analysis endpoints."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Install backend/content_understand/requirements.txt."
        )

    def _default_analyzer_dep() -> ContentAnalyzer:
        return get_analyzer()

    api_router = APIRouter(prefix="/analysis", tags=["content-understanding"])
    dep = analyzer_dependency or _default_analyzer_dep

    @api_router.get("/health")
    async def analysis_health(
        analyzer: ContentAnalyzer = Depends(dep),
    ) -> Dict[str, Any]:
        """Check module health and binary/package capabilities."""
        return health_handler(analyzer=analyzer)

    @api_router.post("/analyze/{video_id}", response_model=VideoAnalysisResult)
    async def analyze_video(
        video_id: str,
        request: Optional[AnalyzeVideoRequest] = Body(default=None),
        analyzer: ContentAnalyzer = Depends(dep),
    ) -> VideoAnalysisResult:
        """Trigger content analysis for an already-segmented video."""
        try:
            return await asyncio.to_thread(
                analyze_video_handler,
                video_id,
                request,
                analyzer,
            )
        except AnalysisAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @api_router.get("/metadata/{video_id}", response_model=VideoAnalysisResult)
    async def get_video_metadata(
        video_id: str,
        analyzer: ContentAnalyzer = Depends(dep),
    ) -> VideoAnalysisResult:
        """Return pre-computed segment metadata for a video."""
        try:
            return await asyncio.to_thread(get_metadata_handler, video_id, analyzer)
        except AnalysisAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @api_router.get(
        "/metadata/{video_id}/{segment_id}",
        response_model=SegmentMetadata,
    )
    async def get_single_segment_metadata(
        video_id: str,
        segment_id: str,
        analyzer: ContentAnalyzer = Depends(dep),
    ) -> SegmentMetadata:
        """Return pre-computed metadata for a single video segment."""
        try:
            return await asyncio.to_thread(
                get_segment_metadata_handler,
                video_id,
                segment_id,
                analyzer,
            )
        except AnalysisAPIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return api_router


def create_app(config: Optional[AnalysisConfig] = None) -> Any:
    """Create a standalone FastAPI application exposing the Content Understanding router."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "FastAPI is not installed. Install backend/content_understand/requirements.txt."
        )

    app_instance = FastAPI(
        title="StreamWise Content Understanding Engine",
        version=(config.analyzer_version if config else "0.1.0"),
    )

    def _provide_analyzer() -> ContentAnalyzer:
        return get_analyzer(config=config)

    app_instance.include_router(build_router(_provide_analyzer))
    return app_instance


_LAZY_EXPORTS: Dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """Lazily construct module-level router or app only when accessed directly."""
    if name == "router":
        if "router" not in _LAZY_EXPORTS:
            _LAZY_EXPORTS["router"] = build_router() if FASTAPI_AVAILABLE else None
        return _LAZY_EXPORTS["router"]
    if name == "app":
        if "app" not in _LAZY_EXPORTS:
            _LAZY_EXPORTS["app"] = create_app() if FASTAPI_AVAILABLE else None
        return _LAZY_EXPORTS["app"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
