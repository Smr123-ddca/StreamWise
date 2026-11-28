"""Adapter transforming ContentUnderstanding and HLS metadata into Decision Engine inputs.

Provides a FastAPI-independent transformation layer that aligns analyzed video segments
with validated MPEG-TS HLS renditions, estimates per-segment delivered bitrates,
maps educational hints conservatively, and preserves full educational diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from backend.content_understand.models import SegmentMetadata as ContentSegmentMetadata
from backend.content_understand.models import VideoAnalysisResult

from ..decision_engine.models import (
    DecisionConfig,
    DecisionRequest,
    NetworkState,
    PlayerState,
    Rendition,
    SceneType,
)
from ..decision_engine.models import (
    SegmentMetadata as DecisionSegmentMetadata,
)
from ..decision_engine.models import (
    ValidationError as DecisionValidationError,
)
from ..decision_engine.models import (
    ViewerPreferences,
)
from ..video_processing.hls_schemas import HLSRenditionInfo, HLSResponse

DEFAULT_MOTION_FALLBACK = 0.5
DEFAULT_SALIENCY_FALLBACK = 0.5
DEFAULT_DURATION_TOLERANCE_SEC = 0.25

SCENE_TYPE_MAP: Dict[str, SceneType] = {
    "code_screen": SceneType.STATIC,
    "presentation_slide": SceneType.STATIC,
    "diagram_or_whiteboard": SceneType.STATIC,
    "dynamic_scene": SceneType.OTHER,
    "talking_head_or_simple": SceneType.OTHER,
    "unknown": SceneType.OTHER,
}

BITRATE_ESTIMATION_NOTE = (
    "Per-segment bitrate_kbps is estimated from delivered MPEG-TS segment file size "
    "((file_size_bytes * 8) / (duration_seconds * 1000)) and includes container and audio overhead."
)


class IntegrationAdapterError(ValueError):
    """Raised when ContentUnderstanding and HLS metadata cannot be safely aligned."""


@dataclass(frozen=True)
class FallbackFieldInfo:
    """Documents a Decision Engine field populated with a neutral fallback."""

    value: Optional[float]
    is_fallback: bool
    reason: str


@dataclass(frozen=True)
class AdaptedSegmentContext:
    """Preserved educational signals and diagnostic metadata for one aligned segment."""

    segment_id: str
    segment_index: int
    duration_s: float
    playlist_start_time_sec: float
    hls_relative_start_time_sec: float
    visual_complexity: float
    text_density: float
    importance_score: float
    has_significant_text: bool
    detected_text_sample: str
    word_count: int
    content_type_hint: str
    mapped_scene_type: str
    scene_change_detected: bool
    ocr_engine: str
    renditions: List[Dict[str, Any]]
    fallback_fields: Dict[str, FallbackFieldInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class AdaptationResult:
    """Complete output of transforming ContentUnderstanding + HLS into a DecisionRequest."""

    video_id: str
    decision_request: DecisionRequest
    adapted_segments: List[AdaptedSegmentContext]
    excluded_renditions: Dict[str, List[Dict[str, str]]]
    duration_tolerance_sec: float
    bitrate_estimation_note: str = BITRATE_ESTIMATION_NOTE


def map_content_type_to_scene_type(content_type_hint: str) -> SceneType:
    """Map a ContentUnderstanding content_type_hint conservatively to Decision Engine SceneType."""
    normalized = (content_type_hint or "unknown").strip().lower()
    return SCENE_TYPE_MAP.get(normalized, SceneType.OTHER)


def calculate_segment_bitrate_kbps(file_size_bytes: int, duration_seconds: float) -> float:
    """Estimate delivered segment bitrate in kbps using SI 1000 bits/kbps conversion."""
    if file_size_bytes <= 0:
        raise IntegrationAdapterError(
            f"HLS segment file_size must be positive, got {file_size_bytes}."
        )
    if duration_seconds <= 0:
        raise IntegrationAdapterError(
            f"HLS segment duration must be positive, got {duration_seconds}."
        )
    return (file_size_bytes * 8.0) / (duration_seconds * 1000.0)


def _validate_and_order_analysis_segments(
    analysis: VideoAnalysisResult,
    tolerance_sec: float,
) -> List[ContentSegmentMetadata]:
    """Validate ContentUnderstanding segment sequence, durations, and contiguous timeline."""
    if not analysis.segments:
        raise IntegrationAdapterError("ContentUnderstanding analysis contains no segments.")
    if analysis.total_segments != len(analysis.segments):
        raise IntegrationAdapterError(
            f"Analysis total_segments ({analysis.total_segments}) does not match "
            f"segments length ({len(analysis.segments)})."
        )

    ordered = sorted(analysis.segments, key=lambda s: s.segment_index)
    expected_indices = list(range(len(ordered)))
    actual_indices = [s.segment_index for s in ordered]
    if actual_indices != expected_indices:
        raise IntegrationAdapterError(
            f"Analysis segment indices must be contiguous 0..{len(ordered) - 1}, got {actual_indices}."
        )

    for idx, seg in enumerate(ordered):
        if seg.duration_sec <= 0:
            raise IntegrationAdapterError(
                f"Analysis segment {seg.segment_id} has non-positive duration {seg.duration_sec}."
            )
        span = seg.end_time_sec - seg.start_time_sec
        if abs(span - seg.duration_sec) > tolerance_sec:
            raise IntegrationAdapterError(
                f"Analysis segment {seg.segment_id} end-start span ({span:.4f}s) "
                f"inconsistent with duration_sec ({seg.duration_sec:.4f}s)."
            )
        if idx > 0:
            prev = ordered[idx - 1]
            if abs(seg.start_time_sec - prev.end_time_sec) > tolerance_sec:
                raise IntegrationAdapterError(
                    f"Analysis timeline gap/overlap between segments {prev.segment_id} "
                    f"(end={prev.end_time_sec:.4f}s) and {seg.segment_id} (start={seg.start_time_sec:.4f}s)."
                )
    return ordered


def _validate_hls_renditions(
    hls: HLSResponse,
    expected_count: int,
    tolerance_sec: float,
) -> List[HLSRenditionInfo]:
    """Validate successful HLS renditions and cross-rendition segment alignment."""
    if hls.status == "failed" or not hls.renditions:
        raise IntegrationAdapterError(
            f"HLS packaging has no successful renditions for video '{hls.video_id}' (status={hls.status})."
        )

    validated: List[HLSRenditionInfo] = []
    for rendition in hls.renditions:
        if rendition.width <= 0 or rendition.height <= 0:
            raise IntegrationAdapterError(
                f"HLS rendition '{rendition.rendition}' has invalid dimensions {rendition.width}x{rendition.height}."
            )
        if not rendition.segments:
            raise IntegrationAdapterError(
                f"HLS rendition '{rendition.rendition}' contains no segments."
            )
        if rendition.segment_count != len(rendition.segments):
            raise IntegrationAdapterError(
                f"HLS rendition '{rendition.rendition}' segment_count ({rendition.segment_count}) "
                f"does not match segments length ({len(rendition.segments)})."
            )
        if len(rendition.segments) != expected_count:
            raise IntegrationAdapterError(
                f"Segment count mismatch: analysis has {expected_count} segments, "
                f"HLS rendition '{rendition.rendition}' has {len(rendition.segments)}."
            )

        origin_pts = rendition.segments[0].start_time
        cumulative_dur = 0.0
        for idx, seg in enumerate(rendition.segments):
            if seg.file_size <= 0:
                raise IntegrationAdapterError(
                    f"HLS segment '{rendition.rendition}/{seg.filename}' has invalid file_size {seg.file_size}."
                )
            if seg.duration <= 0:
                raise IntegrationAdapterError(
                    f"HLS segment '{rendition.rendition}/{seg.filename}' has non-positive duration {seg.duration}."
                )
            rel_pts = seg.start_time - origin_pts
            if abs(rel_pts - cumulative_dur) > tolerance_sec:
                raise IntegrationAdapterError(
                    f"HLS rendition '{rendition.rendition}' segment {idx} relative PTS ({rel_pts:.4f}s) "
                    f"diverges from cumulative duration ({cumulative_dur:.4f}s)."
                )
            cumulative_dur += seg.duration

        validated.append(rendition)

    ref = validated[0]
    ref_origin = ref.segments[0].start_time
    for other in validated[1:]:
        other_origin = other.segments[0].start_time
        for idx, (ref_seg, other_seg) in enumerate(zip(ref.segments, other.segments)):
            if abs(other_seg.duration - ref_seg.duration) > tolerance_sec:
                raise IntegrationAdapterError(
                    f"HLS duration mismatch at segment {idx} between '{ref.rendition}' "
                    f"({ref_seg.duration:.4f}s) and '{other.rendition}' ({other_seg.duration:.4f}s)."
                )
            ref_rel = ref_seg.start_time - ref_origin
            other_rel = other_seg.start_time - other_origin
            if abs(other_rel - ref_rel) > tolerance_sec:
                raise IntegrationAdapterError(
                    f"HLS relative timeline mismatch at segment {idx} between '{ref.rendition}' "
                    f"({ref_rel:.4f}s) and '{other.rendition}' ({other_rel:.4f}s)."
                )

    return validated


def _build_segment_renditions(
    hls_renditions: Sequence[HLSRenditionInfo],
    segment_index: int,
) -> List[Rendition]:
    """Build per-segment Decision Engine Rendition options sorted strictly ascending by bitrate."""
    built: List[Rendition] = []
    for hls_r in hls_renditions:
        seg = hls_r.segments[segment_index]
        bitrate_kbps = calculate_segment_bitrate_kbps(seg.file_size, seg.duration)
        built.append(
            Rendition(
                id=hls_r.rendition,
                bitrate_kbps=bitrate_kbps,
                height=hls_r.height,
                width=hls_r.width,
            )
        )

    built.sort(key=lambda r: (r.bitrate_kbps, r.height, r.width, r.id))

    for prev_r, next_r in zip(built, built[1:]):
        if prev_r.bitrate_kbps >= next_r.bitrate_kbps:
            raise IntegrationAdapterError(
                f"Segment index {segment_index} has non-distinct rendition bitrates: "
                f"'{prev_r.id}' ({prev_r.bitrate_kbps:.3f} kbps) >= '{next_r.id}' ({next_r.bitrate_kbps:.3f} kbps)."
            )
    return built


class ContentDecisionAdapter:
    """Transforms VideoAnalysisResult and HLSResponse into a validated DecisionRequest."""

    def __init__(self, duration_tolerance_sec: float = DEFAULT_DURATION_TOLERANCE_SEC) -> None:
        if duration_tolerance_sec <= 0:
            raise ValueError("duration_tolerance_sec must be positive.")
        self.duration_tolerance_sec = duration_tolerance_sec

    def adapt(
        self,
        analysis: VideoAnalysisResult,
        hls: HLSResponse,
        network: NetworkState,
        player: PlayerState,
        viewer: Optional[ViewerPreferences] = None,
        config: Optional[DecisionConfig] = None,
    ) -> AdaptationResult:
        """Align ContentUnderstanding segments with HLS segments and build a DecisionRequest.

        Args:
            analysis: ContentUnderstanding segment analysis output.
            hls: HLS packaging output containing validated renditions and segment sizes.
            network: Current network throughput and safety margin state.
            player: Current player buffer and last-rendition state.
            viewer: Optional viewer preferences (defaults to BALANCED).
            config: Optional decision engine allocator tuning config.

        Returns:
            AdaptationResult containing the validated DecisionRequest and preserved
            educational metadata + fallback diagnostics.

        Raises:
            IntegrationAdapterError: On video ID mismatch, segment count/duration/timeline
                mismatch, missing renditions, or invalid segment metrics.
        """
        if not analysis.video_id or not hls.video_id or analysis.video_id != hls.video_id:
            raise IntegrationAdapterError(
                f"Video ID mismatch between analysis ('{analysis.video_id}') and HLS ('{hls.video_id}')."
            )

        ordered_analysis = _validate_and_order_analysis_segments(
            analysis, tolerance_sec=self.duration_tolerance_sec
        )
        valid_hls_renditions = _validate_hls_renditions(
            hls,
            expected_count=len(ordered_analysis),
            tolerance_sec=self.duration_tolerance_sec,
        )

        ref_hls = valid_hls_renditions[0]
        analysis_origin = ordered_analysis[0].start_time_sec
        hls_origin = ref_hls.segments[0].start_time

        decision_segments: List[DecisionSegmentMetadata] = []
        adapted_contexts: List[AdaptedSegmentContext] = []

        for idx, cu_seg in enumerate(ordered_analysis):
            ref_hls_seg = ref_hls.segments[idx]

            # Validate duration agreement between ContentUnderstanding and HLS segment
            if abs(cu_seg.duration_sec - ref_hls_seg.duration) > self.duration_tolerance_sec:
                raise IntegrationAdapterError(
                    f"Duration mismatch at segment {idx} ({cu_seg.segment_id}): "
                    f"analysis={cu_seg.duration_sec:.4f}s vs HLS={ref_hls_seg.duration:.4f}s."
                )

            # Validate relative timeline consistency without comparing disparate timestamp origins
            cu_rel_start = cu_seg.start_time_sec - analysis_origin
            hls_rel_start = ref_hls_seg.start_time - hls_origin
            if abs(cu_rel_start - hls_rel_start) > self.duration_tolerance_sec:
                raise IntegrationAdapterError(
                    f"Relative timeline mismatch at segment {idx} ({cu_seg.segment_id}): "
                    f"analysis_rel={cu_rel_start:.4f}s vs hls_rel={hls_rel_start:.4f}s."
                )

            rendition_ladder = _build_segment_renditions(valid_hls_renditions, idx)
            scene_type = map_content_type_to_scene_type(cu_seg.analysis.content_type_hint)

            try:
                decision_seg = DecisionSegmentMetadata(
                    segment_id=cu_seg.segment_id,
                    index=cu_seg.segment_index,
                    duration_s=ref_hls_seg.duration,
                    complexity=cu_seg.analysis.visual_complexity,
                    motion=DEFAULT_MOTION_FALLBACK,
                    saliency=DEFAULT_SALIENCY_FALLBACK,
                    scene_type=scene_type,
                    renditions=rendition_ladder,
                    face_presence=None,
                )
            except DecisionValidationError as exc:
                raise IntegrationAdapterError(
                    f"Failed to construct valid Decision Engine segment {cu_seg.segment_id}: {exc}"
                ) from exc

            decision_segments.append(decision_seg)

            fallbacks = {
                "motion": FallbackFieldInfo(
                    value=DEFAULT_MOTION_FALLBACK,
                    is_fallback=True,
                    reason="ContentUnderstanding does not currently emit motion vectors.",
                ),
                "saliency": FallbackFieldInfo(
                    value=DEFAULT_SALIENCY_FALLBACK,
                    is_fallback=True,
                    reason="ContentUnderstanding does not currently emit visual saliency; importance_score is kept separate.",
                ),
                "face_presence": FallbackFieldInfo(
                    value=None,
                    is_fallback=True,
                    reason="ContentUnderstanding does not currently perform face detection.",
                ),
            }

            adapted_contexts.append(
                AdaptedSegmentContext(
                    segment_id=cu_seg.segment_id,
                    segment_index=cu_seg.segment_index,
                    duration_s=ref_hls_seg.duration,
                    playlist_start_time_sec=cu_seg.start_time_sec,
                    hls_relative_start_time_sec=round(hls_rel_start, 6),
                    visual_complexity=cu_seg.analysis.visual_complexity,
                    text_density=cu_seg.analysis.text_density,
                    importance_score=cu_seg.analysis.importance_score,
                    has_significant_text=cu_seg.analysis.has_significant_text,
                    detected_text_sample=cu_seg.analysis.detected_text_sample,
                    word_count=cu_seg.analysis.word_count,
                    content_type_hint=cu_seg.analysis.content_type_hint,
                    mapped_scene_type=scene_type.value,
                    scene_change_detected=cu_seg.analysis.scene_change_detected,
                    ocr_engine=cu_seg.analysis.ocr_engine,
                    renditions=[
                        {
                            "id": r.id,
                            "bitrate_kbps": round(r.bitrate_kbps, 3),
                            "width": r.width,
                            "height": r.height,
                        }
                        for r in rendition_ladder
                    ],
                    fallback_fields=fallbacks,
                )
            )

        try:
            decision_request = DecisionRequest(
                segments=decision_segments,
                network=network,
                player=player,
                viewer=viewer or ViewerPreferences(),
                config=config or DecisionConfig(),
            )
        except DecisionValidationError as exc:
            raise IntegrationAdapterError(f"Invalid DecisionRequest: {exc}") from exc

        excluded = {
            "skipped": [
                {"rendition": s.rendition, "reason": s.reason}
                for s in hls.skipped_renditions
            ],
            "failed": [
                {"rendition": f.rendition, "error": f.error}
                for f in hls.failed_renditions
            ],
        }

        return AdaptationResult(
            video_id=analysis.video_id,
            decision_request=decision_request,
            adapted_segments=adapted_contexts,
            excluded_renditions=excluded,
            duration_tolerance_sec=self.duration_tolerance_sec,
        )
