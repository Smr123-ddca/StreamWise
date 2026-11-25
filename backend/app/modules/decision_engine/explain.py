"""Structured and human-readable decision explanations."""

from .models import Explanation, Rendition, SegmentMetadata, ViewerPreferences
from .priority import compute_priority, priority_factors
from .utility import marginal_utility


def build_explanation(segment: SegmentMetadata, selected: Rendition, viewer: ViewerPreferences, stopping_constraint: str, switch_suppressed: bool, lower: Rendition | None, higher: Rendition | None) -> Explanation:
    """Build the explanation attached to one selected rendition."""
    priority = compute_priority(segment, viewer)
    factors = priority_factors(segment, viewer)
    top = [{"factor": key, "contribution": round(value, 4)} for key, value in sorted(factors.items(), key=lambda item: item[1], reverse=True)[:3]]
    lower_gain = marginal_utility(segment, lower, selected) if lower else 0.0
    higher_gain = marginal_utility(segment, selected, higher) if higher else None
    switch = "; switching was suppressed" if switch_suppressed else ""
    human = f"Selected {selected.id} at priority {priority:.2f}; next upgrade gain is {higher_gain or 0:.3f}, stopped by {stopping_constraint}{switch}."
    return Explanation(top, lower_gain, higher_gain, stopping_constraint, switch_suppressed, human)
