"""Content analysis module integrating the Content Understanding Engine."""

try:
    from backend.content_understand import (
        AnalysisConfig,
        AnalyzeVideoRequest,
        ContentAnalyzer,
        ImportanceWeights,
        SegmentAnalysis,
        SegmentInput,
        SegmentMetadata,
        VideoAnalysisResult,
        get_analysis_config,
    )
    from backend.content_understand.api import build_router
except ImportError:  # pragma: no cover - when running with backend/ on sys.path
    from content_understand import (
        AnalysisConfig,
        AnalyzeVideoRequest,
        ContentAnalyzer,
        ImportanceWeights,
        SegmentAnalysis,
        SegmentInput,
        SegmentMetadata,
        VideoAnalysisResult,
        get_analysis_config,
    )
    from content_understand.api import build_router

__all__ = [
    "AnalysisConfig",
    "AnalyzeVideoRequest",
    "ContentAnalyzer",
    "ImportanceWeights",
    "SegmentAnalysis",
    "SegmentInput",
    "SegmentMetadata",
    "VideoAnalysisResult",
    "build_router",
    "get_analysis_config",
]
