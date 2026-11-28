"""Integration adapters connecting ContentUnderstanding, HLS packaging, and Decision Engine."""

from .content_decision_adapter import (
    BITRATE_ESTIMATION_NOTE,
    DEFAULT_DURATION_TOLERANCE_SEC,
    DEFAULT_MOTION_FALLBACK,
    DEFAULT_SALIENCY_FALLBACK,
    SCENE_TYPE_MAP,
    AdaptationResult,
    AdaptedSegmentContext,
    ContentDecisionAdapter,
    FallbackFieldInfo,
    IntegrationAdapterError,
    calculate_segment_bitrate_kbps,
    map_content_type_to_scene_type,
)

__all__ = [
    "AdaptationResult",
    "AdaptedSegmentContext",
    "BITRATE_ESTIMATION_NOTE",
    "ContentDecisionAdapter",
    "DEFAULT_DURATION_TOLERANCE_SEC",
    "DEFAULT_MOTION_FALLBACK",
    "DEFAULT_SALIENCY_FALLBACK",
    "FallbackFieldInfo",
    "IntegrationAdapterError",
    "SCENE_TYPE_MAP",
    "calculate_segment_bitrate_kbps",
    "map_content_type_to_scene_type",
]
