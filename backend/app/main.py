from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from .api import analysis_router, integration_router, video_router
from .config import get_settings
from .modules.decision_engine.api import router as decision_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

# CORS configuration for local frontend development and HLS browser playback
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "ETag",
        "Last-Modified",
        "Content-Type",
    ],
)

# Register API routers
app.include_router(video_router)
app.include_router(integration_router)
app.include_router(decision_router)
app.include_router(analysis_router)


@app.get("/player", response_class=HTMLResponse, include_in_schema=False)
def serve_playback_page() -> HTMLResponse:
    """Serve the minimal HLS playback verification page."""
    player_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "player.html"
    if not player_path.exists() or not player_path.is_file():
        raise HTTPException(status_code=404, detail="Playback verification page not found.")
    return HTMLResponse(content=player_path.read_text(encoding="utf-8"))


@app.get("/adaptive_controller.js", include_in_schema=False)
def serve_adaptive_controller() -> Response:
    """Serve the frontend adaptive playback controller script explicitly."""
    script_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "adaptive_controller.js"
    if not script_path.exists() or not script_path.is_file():
        raise HTTPException(status_code=404, detail="Adaptive controller script not found.")
    return Response(
        content=script_path.read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
    )


@app.get("/api/health")
def health_check():
    """Health check endpoint returning application metadata and operational status."""
    return {
        "application": settings.app_name,
        "status": "healthy",
        "version": settings.app_version,
    }
