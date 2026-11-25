from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import analysis_router, video_router
from .config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)

# Basic CORS configuration for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(video_router)
app.include_router(analysis_router)


@app.get("/api/health")
def health_check():
    """Health check endpoint returning application metadata and operational status."""
    return {
        "application": settings.app_name,
        "status": "healthy",
        "version": settings.app_version,
    }
