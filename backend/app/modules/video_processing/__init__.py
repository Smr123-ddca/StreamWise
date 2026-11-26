"""Video processing module (transcoding, validation, metadata extraction)."""

from .metadata import extract_video_metadata, get_ffprobe_executable
from .schemas import VideoMetadata, VideoUploadResponse
from .service import (
    VideoProcessingService,
    find_source_video,
    resolve_processed_directory,
    resolve_uploads_directory,
)
from .transcoder import (
    VideoTranscoder,
    build_ffmpeg_command,
    get_ffmpeg_executable,
)
from .transcoding_config import (
    DEFAULT_RENDITION_PROFILES,
    RenditionProfile,
    TranscodingConfig,
    calculate_rendition_dimensions,
    to_even,
)
from .transcoding_schemas import (
    FailedRenditionInfo,
    RenditionInfo,
    SkippedRenditionInfo,
    TranscodingResponse,
)
from .uploader import cleanup_upload, save_upload_file
from .validator import validate_file_extension, validate_preliminary_mime_type

__all__ = [
    "VideoMetadata",
    "VideoUploadResponse",
    "VideoProcessingService",
    "VideoTranscoder",
    "TranscodingConfig",
    "RenditionProfile",
    "DEFAULT_RENDITION_PROFILES",
    "calculate_rendition_dimensions",
    "to_even",
    "build_ffmpeg_command",
    "get_ffmpeg_executable",
    "get_ffprobe_executable",
    "RenditionInfo",
    "SkippedRenditionInfo",
    "FailedRenditionInfo",
    "TranscodingResponse",
    "cleanup_upload",
    "save_upload_file",
    "validate_file_extension",
    "validate_preliminary_mime_type",
    "extract_video_metadata",
    "resolve_uploads_directory",
    "resolve_processed_directory",
    "find_source_video",
]
