"""API route definitions and endpoints for StreamWise."""

from .analysis_routes import router as analysis_router
from .video_routes import router as video_router

__all__ = ["analysis_router", "video_router"]
