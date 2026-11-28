"""Contextual importance scoring and optional content-type hint inference."""

import logging
from typing import Mapping, Optional
from .config import AnalysisConfig, ImportanceWeights

logger = logging.getLogger(__name__)


def calculate_importance_score(
    visual_complexity: float,
    text_density: float,
    scene_change: bool,
    has_significant_text: bool,
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    """Compute normalized contextual importance score in [0.0, 1.0].

    Default weights match the StreamWise specification:
      - visual_complexity: 0.30
      - text_density: 0.40
      - scene_change_bonus: 0.20
      - text_flag_bonus: 0.10
    """
    active_weights = dict(weights) if weights is not None else ImportanceWeights().as_dict()

    clamped_complexity = max(0.0, min(float(visual_complexity), 1.0))
    clamped_text_density = max(0.0, min(float(text_density), 1.0))

    score = (
        active_weights.get("visual_complexity", 0.30) * clamped_complexity
        + active_weights.get("text_density", 0.40) * clamped_text_density
        + active_weights.get("scene_change_bonus", 0.20) * (1.0 if scene_change else 0.0)
        + active_weights.get("text_flag_bonus", 0.10) * (1.0 if has_significant_text else 0.0)
    )
    return round(max(0.0, min(score, 1.0)), 4)


def infer_content_type_hint(
    visual_complexity: float,
    text_density: float,
    has_significant_text: bool,
    scene_change: bool,
    detected_text_sample: str = "",
    config: Optional[AnalysisConfig] = None,
) -> str:
    """Return an informational content-type hint without coupling downstream logic."""
    cfg = config or AnalysisConfig()
    lowered = detected_text_sample.lower()

    code_markers = ("def ", "class ", "import ", "return ", "function ", "=>", "};", "(){", ") {")
    if any(marker in lowered for marker in code_markers) or (
        has_significant_text
        and text_density >= cfg.slide_text_density_threshold
        and visual_complexity >= cfg.diagram_complexity_threshold
    ):
        return "code_screen"

    if has_significant_text or text_density >= cfg.slide_text_density_threshold:
        return "presentation_slide"

    if visual_complexity >= cfg.diagram_complexity_threshold:
        return "diagram_or_whiteboard"

    if scene_change:
        return "dynamic_scene"

    return "talking_head_or_simple"
