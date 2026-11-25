"""Per-segment perceived-quality utility estimation."""

from dataclasses import dataclass
from math import log1p

from .models import Rendition, SegmentMetadata


@dataclass(frozen=True)
class UtilityConfig:
    """Coefficients for the saturating utility model."""
    ref_bitrate_kbps: float = 1200
    complexity_penalty: float = 0.35
    resolution_weight: float = 0.2


def estimate_utility(rendition: Rendition, segment: SegmentMetadata, config: UtilityConfig | None = None) -> float:
    """Estimate normalized perceived quality for a rendition and segment."""
    cfg = config or UtilityConfig()
    if rendition.vmaf is not None:
        return max(0.0, min(1.0, rendition.vmaf / 100))
    ladder_top = max(r.bitrate_kbps for r in segment.renditions)
    bitrate_quality = (log1p(rendition.bitrate_kbps / cfg.ref_bitrate_kbps) / log1p(ladder_top / cfg.ref_bitrate_kbps)) ** 0.7
    complexity_factor = 1 - cfg.complexity_penalty * segment.complexity * (1 - bitrate_quality)
    resolution_fit = min(1.0, rendition.height / max(1, segment.renditions[-1].height))
    return max(0.0, min(1.0, bitrate_quality * complexity_factor * ((1 - cfg.resolution_weight) + cfg.resolution_weight * resolution_fit)))


def marginal_utility(segment: SegmentMetadata, from_rendition: Rendition, to_rendition: Rendition, config: UtilityConfig | None = None) -> float:
    """Return the quality gain of moving between two renditions."""
    return max(0.0, estimate_utility(to_rendition, segment, config) - estimate_utility(from_rendition, segment, config))
