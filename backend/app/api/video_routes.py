"""API routes for video upload and processing."""

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import Settings, get_settings
from ..modules.video_processing.schemas import VideoUploadResponse
from ..modules.video_processing.service import VideoProcessingService

router = APIRouter(prefix="/api/videos", tags=["videos"])


def get_video_service(settings: Settings = Depends(get_settings)) -> VideoProcessingService:
    """Dependency provider for VideoProcessingService."""
    return VideoProcessingService(settings=settings)


@router.post(
    "/upload",
    response_model=VideoUploadResponse,
    summary="Upload and validate a video file",
    description="Upload a video file (MP4, MOV, MKV, WebM), validate with FFprobe, and extract technical metadata.",
)
async def upload_video(
    file: UploadFile = File(..., description="Multipart video file"),
    service: VideoProcessingService = Depends(get_video_service),
) -> VideoUploadResponse:
    """Stream, validate, and extract technical metadata for an uploaded video."""
    return await service.process_video_upload(file)
