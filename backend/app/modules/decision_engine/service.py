"""Application service for direct playback-controller integration."""

from .allocator import allocate
from .models import DecisionRequest, DecisionResponse


class DecisionService:
    """Plain callable facade around the pure allocator."""

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Return one deterministic decision per segment in the rolling window."""
        return allocate(request)
