"""API route integrating ContentUnderstanding, HLS metadata, and the Decision Engine."""

import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..modules.content_analysis import ContentAnalyzer
from ..modules.decision_engine.models import (
    DecisionConfig,
    NetworkState,
    PlayerState,
    PreferenceMode,
    ValidationError as DecisionValidationError,
    ViewerPreferences,
    as_json,
)
from ..modules.decision_engine.service import DecisionService
from ..modules.integration import ContentDecisionAdapter, IntegrationAdapterError
from ..modules.video_processing.service import (
    load_hls_status,
    resolve_processed_directory,
    validate_video_id,
)
from .analysis_routes import get_content_analyzer

router = APIRouter(prefix="/api/videos", tags=["decisions"])


class NetworkStatePayload(BaseModel):
    """Client-supplied network throughput estimate and safety margin."""

    throughput_estimate_kbps: float = Field(..., description="Estimated available throughput in kbps")
    throughput_variance: Optional[float] = Field(default=None, description="Optional throughput variance")
    safety_margin: float = Field(default=0.8, description="Conservative safety factor in (0.01, 1.0]")


class PlayerStatePayload(BaseModel):
    """Client-supplied player buffer and playback continuity state."""

    buffer_s: float = Field(..., description="Current player buffer level in seconds")
    min_buffer_s: float = Field(default=5.0, description="Minimum target buffer threshold in seconds")
    max_buffer_s: float = Field(default=30.0, description="Maximum buffer capacity in seconds")
    last_rendition_id: Optional[str] = Field(default=None, description="Previously selected rendition ID")


class ViewerPreferencesPayload(BaseModel):
    """Optional viewer playback policy preferences."""

    mode: str = Field(default="balanced", description="Preference mode: quality_first, balanced, or data_saver")
    scene_weights: Dict[str, float] = Field(default_factory=dict, description="Optional per-scene weight overrides")
    max_height: Optional[int] = Field(default=None, description="Optional maximum vertical resolution cap")


class DecisionConfigPayload(BaseModel):
    """Optional allocator tuning parameters."""

    switching_penalty: float = Field(default=0.04, description="Penalty applied to quality switches")
    safety_buffer_factor: float = Field(default=0.5, description="Budget scaling factor for buffer headroom")
    ref_bitrate_kbps: float = Field(default=1200.0, description="Reference bitrate for utility normalization")


class IntegratedDecisionPayload(BaseModel):
    """Request body for POST /api/videos/{video_id}/decisions."""

    network: NetworkStatePayload
    player: PlayerStatePayload
    viewer: Optional[ViewerPreferencesPayload] = None
    config: Optional[DecisionConfigPayload] = None


def get_decision_service() -> DecisionService:
    """Dependency provider for DecisionService."""
    return DecisionService()


def get_content_decision_adapter() -> ContentDecisionAdapter:
    """Dependency provider for ContentDecisionAdapter."""
    return ContentDecisionAdapter()


@router.post(
    "/{video_id}/decisions",
    summary="Compute content-aware segment quality decisions",
    description=(
        "Combine stored ContentUnderstanding segment metadata with packaged HLS rendition data "
        "and caller-supplied network/player state to compute per-segment quality decisions."
    ),
)
def compute_video_decisions(
    video_id: str,
    payload: IntegratedDecisionPayload,
    settings: Settings = Depends(get_settings),
    analyzer: ContentAnalyzer = Depends(get_content_analyzer),
    decision_service: DecisionService = Depends(get_decision_service),
    adapter: ContentDecisionAdapter = Depends(get_content_decision_adapter),
) -> Dict[str, Any]:
    """Generate per-segment quality decisions and explanations for a processed video."""
    canonical_id = validate_video_id(video_id)

    try:
        network = NetworkState(
            throughput_estimate_kbps=payload.network.throughput_estimate_kbps,
            throughput_variance=payload.network.throughput_variance,
            safety_margin=payload.network.safety_margin,
        )
        player = PlayerState(
            buffer_s=payload.player.buffer_s,
            min_buffer_s=payload.player.min_buffer_s,
            max_buffer_s=payload.player.max_buffer_s,
            last_rendition_id=payload.player.last_rendition_id,
        )
        viewer = (
            ViewerPreferences(
                mode=PreferenceMode(payload.viewer.mode),
                scene_weights=payload.viewer.scene_weights,
                max_height=payload.viewer.max_height,
            )
            if payload.viewer is not None
            else ViewerPreferences()
        )
        config = (
            DecisionConfig(
                switching_penalty=payload.config.switching_penalty,
                safety_buffer_factor=payload.config.safety_buffer_factor,
                ref_bitrate_kbps=payload.config.ref_bitrate_kbps,
            )
            if payload.config is not None
            else DecisionConfig()
        )
    except (DecisionValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        analysis_result = analyzer.load_metadata(video_id=canonical_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Content analysis metadata not found for video ID '{canonical_id}'. "
                f"Run POST /analysis/analyze/{canonical_id} first."
            ),
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid or corrupted content analysis metadata for video ID '{canonical_id}': {exc}",
        ) from exc

    processed_dir = resolve_processed_directory(settings.storage_dir)
    hls_response = load_hls_status(processed_dir, canonical_id)
    if hls_response is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"HLS packaging metadata not found for video ID '{canonical_id}'. "
                f"Run POST /api/videos/{canonical_id}/hls first."
            ),
        )

    try:
        adaptation = adapter.adapt(
            analysis=analysis_result,
            hls=hls_response,
            network=network,
            player=player,
            viewer=viewer,
            config=config,
        )
    except (IntegrationAdapterError, DecisionValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    decision_response = decision_service.decide(adaptation.decision_request)
    decision_payload = as_json(decision_response)

    return {
        "video_id": canonical_id,
        **decision_payload,
        "adapted_segments": [as_json(seg) for seg in adaptation.adapted_segments],
        "integration_metadata": {
            "excluded_renditions": adaptation.excluded_renditions,
            "duration_tolerance_sec": adaptation.duration_tolerance_sec,
            "bitrate_estimation_note": adaptation.bitrate_estimation_note,
            "fallback_fields": ["motion", "saliency", "face_presence"],
        },
    }
