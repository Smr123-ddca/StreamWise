"""API route definitions and endpoints for StreamWise."""

from .video_routes import router as video_router

__all__ = ["video_router"]
