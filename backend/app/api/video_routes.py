"""API routes for video upload, processing, HLS packaging, and secure media delivery."""

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import Response

from ..config import Settings, get_settings
from ..modules.video_processing.hls_schemas import HLSResponse
from ..modules.video_processing.schemas import VideoUploadResponse
from ..modules.video_processing.service import VideoProcessingService
from ..modules.video_processing.transcoding_schemas import TranscodingResponse
from ..storage import (
    PLAYLIST_CACHE_CONTROL,
    PLAYLIST_MIME_TYPE,
    SEGMENT_CACHE_CONTROL,
    SEGMENT_MIME_TYPE,
    MediaDeliveryStorage,
)

router = APIRouter(prefix="/api/videos", tags=["videos"])


def get_video_service(settings: Settings = Depends(get_settings)) -> VideoProcessingService:
    """Dependency provider for VideoProcessingService."""
    return VideoProcessingService(settings=settings)


def get_media_storage(settings: Settings = Depends(get_settings)) -> MediaDeliveryStorage:
    """Dependency provider for scoped HLS MediaDeliveryStorage."""
    return MediaDeliveryStorage(storage_dir=settings.storage_dir)


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
    force: bool = Query(False, description="Force re-transcoding even if completed renditions already exist"),
    service: VideoProcessingService = Depends(get_video_service),
) -> TranscodingResponse:
    """Transcode a source video into standardized quality renditions without upscaling."""
    return service.transcode_video(video_id=video_id, force=force)


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


@router.post(
    "/{video_id}/hls",
    response_model=HLSResponse,
    summary="Package transcoded MP4 renditions into synchronized HLS streams",
    description="Convert available MP4 renditions into keyframe-aligned MPEG-TS HLS segments, media playlists, and master.m3u8.",
)
def package_hls(
    video_id: str,
    force: bool = Query(False, description="Force re-packaging even if completed HLS outputs already exist"),
    service: VideoProcessingService = Depends(get_video_service),
) -> HLSResponse:
    """Synchronously package MP4 renditions into synchronized HLS playlists and segments."""
    return service.package_hls(video_id=video_id, force=force)


@router.get(
    "/{video_id}/hls/status",
    response_model=HLSResponse,
    summary="Get HLS packaging status and playlist details",
    description="Retrieve the HLS packaging status, master playlist URL, and validated segment details for a video.",
)
def get_hls_status(
    video_id: str,
    service: VideoProcessingService = Depends(get_video_service),
) -> HLSResponse:
    """Retrieve HLS packaging job status and generated playlists."""
    return service.get_hls_status(video_id=video_id)


@router.get(
    "/{video_id}/hls/master.m3u8",
    summary="Serve HLS master playlist",
    description="Validate and serve the master.m3u8 playlist for the specified video ID.",
)
@router.head(
    "/{video_id}/hls/master.m3u8",
    include_in_schema=False,
)
def serve_master_playlist(
    video_id: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    storage: MediaDeliveryStorage = Depends(get_media_storage),
) -> Response:
    """Deliver master.m3u8 with playlist validation and cache revalidation headers."""
    master_path, raw_bytes = storage.resolve_master_playlist(video_id)
    return storage.build_media_response(
        file_path=master_path,
        media_type=PLAYLIST_MIME_TYPE,
        cache_control=PLAYLIST_CACHE_CONTROL,
        method=request.method,
        range_header=range_header,
        inline_bytes=raw_bytes,
    )


@router.get(
    "/{video_id}/hls/{rendition}/playlist.m3u8",
    summary="Serve HLS rendition media playlist",
    description="Validate and serve the rendition playlist.m3u8 for the specified video ID and quality tier.",
)
@router.head(
    "/{video_id}/hls/{rendition}/playlist.m3u8",
    include_in_schema=False,
)
def serve_rendition_playlist(
    video_id: str,
    rendition: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    storage: MediaDeliveryStorage = Depends(get_media_storage),
) -> Response:
    """Deliver {rendition}/playlist.m3u8 with URI validation and cache revalidation headers."""
    playlist_path, raw_bytes = storage.resolve_rendition_playlist(video_id, rendition)
    return storage.build_media_response(
        file_path=playlist_path,
        media_type=PLAYLIST_MIME_TYPE,
        cache_control=PLAYLIST_CACHE_CONTROL,
        method=request.method,
        range_header=range_header,
        inline_bytes=raw_bytes,
    )


@router.get(
    "/{video_id}/hls/{rendition}/{segment_name}",
    summary="Serve HLS MPEG-TS media segment",
    description="Serve a validated MPEG-TS (.ts) segment with HTTP byte-range (206/416) and caching support.",
)
@router.head(
    "/{video_id}/hls/{rendition}/{segment_name}",
    include_in_schema=False,
)
def serve_hls_segment(
    video_id: str,
    rendition: str,
    segment_name: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    storage: MediaDeliveryStorage = Depends(get_media_storage),
) -> Response:
    """Stream an MPEG-TS segment safely with byte-range and caching headers."""
    segment_path = storage.resolve_segment(video_id, rendition, segment_name)
    return storage.build_media_response(
        file_path=segment_path,
        media_type=SEGMENT_MIME_TYPE,
        cache_control=SEGMENT_CACHE_CONTROL,
        method=request.method,
        range_header=range_header,
    )
