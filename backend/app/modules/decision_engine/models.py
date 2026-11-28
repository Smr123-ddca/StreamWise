"""Validated dataclass models for the decision-engine metadata contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class ValidationError(ValueError):
    """Raised when a decision request violates the metadata contract."""


class SceneType(str, Enum):
    ACTION = "action"
    DIALOGUE = "dialogue"
    STATIC = "static"
    TRANSITION = "transition"
    OTHER = "other"


class PreferenceMode(str, Enum):
    QUALITY_FIRST = "quality_first"
    BALANCED = "balanced"
    DATA_SAVER = "data_saver"


def _range(name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ValidationError(f"{name} must be between {low} and {high}")


@dataclass(frozen=True)
class Rendition:
    id: str
    bitrate_kbps: float
    height: int
    width: int
    vmaf: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.id or self.bitrate_kbps <= 0 or self.height <= 0 or self.width <= 0:
            raise ValidationError("rendition id, bitrate, width, and height must be positive")
        if self.vmaf is not None:
            _range("vmaf", self.vmaf, 0, 100)


@dataclass(frozen=True)
class SegmentMetadata:
    segment_id: str
    index: int
    duration_s: float
    complexity: float
    motion: float
    saliency: float
    scene_type: SceneType
    renditions: list[Rendition]
    face_presence: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.segment_id or self.index < 0 or self.duration_s <= 0 or not self.renditions:
            raise ValidationError("segment id, non-negative index, positive duration, and renditions are required")
        for name in ("complexity", "motion", "saliency"):
            _range(name, getattr(self, name), 0, 1)
        if self.face_presence is not None:
            _range("face_presence", self.face_presence, 0, 1)
        if any(a.bitrate_kbps >= b.bitrate_kbps for a, b in zip(self.renditions, self.renditions[1:])):
            raise ValidationError("renditions must be sorted by strictly increasing bitrate")


@dataclass(frozen=True)
class ViewerPreferences:
    mode: PreferenceMode = PreferenceMode.BALANCED
    scene_weights: dict[str, float] = field(default_factory=dict)
    max_height: Optional[int] = None

    def __post_init__(self) -> None:
        if self.max_height is not None and self.max_height <= 0:
            raise ValidationError("max_height must be positive")
        for scene, weight in self.scene_weights.items():
            try:
                SceneType(scene)
            except ValueError as exc:
                raise ValidationError(f"unknown scene weight: {scene}") from exc
            if weight < 0:
                raise ValidationError("scene weights cannot be negative")


@dataclass(frozen=True)
class NetworkState:
    throughput_estimate_kbps: float
    throughput_variance: Optional[float] = None
    safety_margin: float = 0.8

    def __post_init__(self) -> None:
        if self.throughput_estimate_kbps <= 0 or (self.throughput_variance is not None and self.throughput_variance < 0):
            raise ValidationError("throughput must be positive and variance cannot be negative")
        _range("safety_margin", self.safety_margin, 0.01, 1)


@dataclass(frozen=True)
class PlayerState:
    buffer_s: float
    min_buffer_s: float = 5
    max_buffer_s: float = 30
    last_rendition_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.buffer_s < 0 or self.min_buffer_s < 0 or self.max_buffer_s < self.min_buffer_s:
            raise ValidationError("buffer values must satisfy 0 <= min_buffer <= max_buffer")


@dataclass(frozen=True)
class DecisionConfig:
    switching_penalty: float = 0.04
    safety_buffer_factor: float = 0.5
    ref_bitrate_kbps: float = 1200

    def __post_init__(self) -> None:
        if self.switching_penalty < 0 or self.safety_buffer_factor < 0 or self.ref_bitrate_kbps <= 0:
            raise ValidationError("decision config values are invalid")


@dataclass(frozen=True)
class DecisionRequest:
    segments: list[SegmentMetadata]
    network: NetworkState
    player: PlayerState
    viewer: ViewerPreferences = field(default_factory=ViewerPreferences)
    config: DecisionConfig = field(default_factory=DecisionConfig)

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValidationError("segments must not be empty")
        if [s.index for s in self.segments] != sorted(s.index for s in self.segments):
            raise ValidationError("segments must be ordered by index")


@dataclass(frozen=True)
class Explanation:
    top_factors: list[dict[str, Any]]
    utility_gain_vs_lower: float
    utility_gain_vs_higher: Optional[float]
    stopping_constraint: str
    switch_suppressed: bool
    human: str


@dataclass(frozen=True)
class SegmentDecision:
    segment_id: str
    rendition_id: str
    bitrate_kbps: float
    priority: float
    utility: float
    explanation: Explanation


@dataclass(frozen=True)
class DecisionResponse:
    decisions: list[SegmentDecision]
    window_budget_kbit: float
    used_kbit: float
    predicted_min_buffer_s: float
    constraints_binding: list[str]


def request_from_dict(data: dict[str, Any]) -> DecisionRequest:
    """Build and validate a request from its JSON-compatible representation."""
    try:
        segments = []
        for raw in data["segments"]:
            rs = [Rendition(**r) for r in raw["renditions"]]
            segments.append(SegmentMetadata(**{**raw, "scene_type": SceneType(raw["scene_type"]), "renditions": rs}))
        viewer_data = data.get("viewer", {})
        return DecisionRequest(
            segments=segments,
            network=NetworkState(**data["network"]),
            player=PlayerState(**data["player"]),
            viewer=ViewerPreferences(**{**viewer_data, "mode": PreferenceMode(viewer_data.get("mode", "balanced"))}),
            config=DecisionConfig(**data.get("config", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(str(exc)) from exc


def to_json_schema() -> dict[str, Any]:
    """Return a compact JSON Schema for the request contract."""
    return {"title": "StreamWise Decision Contract", "type": "object", "required": ["segments", "network", "player"], "properties": {
        "segments": {"type": "array", "items": {"$ref": "#/definitions/SegmentMetadata"}},
        "network": {"$ref": "#/definitions/NetworkState"}, "player": {"$ref": "#/definitions/PlayerState"},
        "viewer": {"$ref": "#/definitions/ViewerPreferences"}, "config": {"$ref": "#/definitions/DecisionConfig"}},
        "definitions": {"Rendition": {"type": "object", "required": ["id", "bitrate_kbps", "height", "width"]},
        "SegmentMetadata": {"type": "object", "required": ["segment_id", "index", "duration_s", "complexity", "motion", "saliency", "scene_type", "renditions"]},
        "NetworkState": {"type": "object", "required": ["throughput_estimate_kbps"]}, "PlayerState": {"type": "object", "required": ["buffer_s"]},
        "ViewerPreferences": {"type": "object"}, "DecisionConfig": {"type": "object"}}}


def as_json(value: Any) -> Any:
    """Convert an engine dataclass tree to JSON-compatible values."""
    if isinstance(value, Enum): return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {k: as_json(v) for k, v in asdict(value).items()}
    if isinstance(value, list): return [as_json(v) for v in value]
    if isinstance(value, dict): return {k: as_json(v) for k, v in value.items()}
    return value
