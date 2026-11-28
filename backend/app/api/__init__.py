"""API route definitions and endpoints for StreamWise."""

from .analysis_routes import router as analysis_router
from .integration_routes import router as integration_router
from .video_routes import router as video_router

__all__ = ["analysis_router", "integration_router", "video_router"]
