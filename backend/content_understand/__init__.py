"""Content Understanding Engine for StreamWise.

Analyzes HLS video segments for frame-level visual complexity, text/OCR density,
scene transitions, and contextual importance, producing structured JSON metadata
for the Adaptive Quality Decision Engine.
"""

from .analyzer import ContentAnalyzer
from .complexity_analyzer import (
    analyze_frames_complexity,
    calculate_visual_complexity,
)
from .config import AnalysisConfig, ImportanceWeights, get_analysis_config
from .frame_extractor import MediaExtractionError, extract_frames, probe_media_duration
from .importance_scorer import calculate_importance_score, infer_content_type_hint
from .models import (
    AnalyzeVideoRequest,
    SegmentAnalysis,
    SegmentInput,
    SegmentMetadata,
    VideoAnalysisResult,
)
from .ocr_analyzer import analyze_frames_text_density, analyze_text_density
from .scene_detector import detect_scene_change, get_scene_changes

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AnalysisConfig",
    "ImportanceWeights",
    "get_analysis_config",
    "AnalyzeVideoRequest",
    "SegmentAnalysis",
    "SegmentInput",
    "SegmentMetadata",
    "VideoAnalysisResult",
    "ContentAnalyzer",
    "MediaExtractionError",
    "extract_frames",
    "probe_media_duration",
    "detect_scene_change",
    "get_scene_changes",
    "calculate_visual_complexity",
    "analyze_frames_complexity",
    "analyze_text_density",
    "analyze_frames_text_density",
    "calculate_importance_score",
    "infer_content_type_hint",
]
