"""Content-aware priority scoring."""

from dataclasses import dataclass

from .models import PreferenceMode, SceneType, SegmentMetadata, ViewerPreferences


@dataclass(frozen=True)
class PriorityConfig:
    """Weights for content-priority contributions."""
    motion_weight: float = 0.25
    saliency_weight: float = 0.25
    complexity_weight: float = 0.2
    scene_weight: float = 0.2
    face_weight: float = 0.1
    scene_weights: dict[str, float] | None = None


def priority_factors(segment: SegmentMetadata, viewer: ViewerPreferences, config: PriorityConfig | None = None) -> dict[str, float]:
    """Return named weighted contributions used by the priority score."""
    cfg = config or PriorityConfig()
    defaults = {SceneType.ACTION.value: 1.0, SceneType.DIALOGUE.value: .75, SceneType.STATIC.value: .35, SceneType.TRANSITION.value: .55, SceneType.OTHER.value: .6}
    scene = (cfg.scene_weights or defaults).get(segment.scene_type.value, .6)
    scene *= viewer.scene_weights.get(segment.scene_type.value, 1.0)
    return {"motion": cfg.motion_weight * segment.motion, "saliency": cfg.saliency_weight * segment.saliency, "complexity": cfg.complexity_weight * segment.complexity, "scene_type": cfg.scene_weight * scene, "face_presence": cfg.face_weight * (segment.face_presence or 0)}


def compute_priority(segment: SegmentMetadata, viewer: ViewerPreferences, config: PriorityConfig | None = None) -> float:
    """Compute a non-zero normalized priority in approximately [0.5, 2.0]."""
    raw = sum(priority_factors(segment, viewer, config).values())
    modifier = {PreferenceMode.QUALITY_FIRST: 1.12, PreferenceMode.BALANCED: 1.0, PreferenceMode.DATA_SAVER: .82}[viewer.mode]
    return max(.5, min(2.0, .5 + 1.5 * raw * modifier))
