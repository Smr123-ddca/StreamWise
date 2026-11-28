"""Content-aware, deterministic segment rendition decisions."""

from .allocator import allocate
from .models import DecisionRequest, DecisionResponse
from .service import DecisionService

__all__ = ["DecisionRequest", "DecisionResponse", "DecisionService", "allocate"]
