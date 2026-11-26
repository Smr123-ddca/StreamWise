"""API routes for video upload and processing."""

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import Settings, get_settings
from ..modules.video_processing.schemas import VideoUploadResponse
from ..modules.video_processing.service import VideoProcessingService
from ..modules.video_processing.transcoding_schemas import TranscodingResponse

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


@router.post(
    "/{video_id}/transcode",
    response_model=TranscodingResponse,
    summary="Transcode uploaded video into multi-resolution renditions",
    description="Transcode an uploaded video into standardized quality renditions (360p, 480p, 720p) using H.264/AAC.",
)
def transcode_video(
    video_id: str,
    service: VideoProcessingService = Depends(get_video_service),
) -> TranscodingResponse:
    """Transcode a source video into standardized quality renditions without upscaling."""
    return service.transcode_video(video_id=video_id)


@router.get(
    "/{video_id}/transcode/status",
    response_model=TranscodingResponse,
    summary="Get video transcoding status and rendition details",
    description="Retrieve the current status, rendition details, and metadata for a previously requested transcoding job.",
)
def get_transcode_status(
    video_id: str,
    service: VideoProcessingService = Depends(get_video_service),
) -> TranscodingResponse:
    """Retrieve transcoding job status and generated renditions."""
    return service.get_transcode_status(video_id=video_id)
