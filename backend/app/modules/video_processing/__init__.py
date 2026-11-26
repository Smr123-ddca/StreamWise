"""Video processing module (transcoding, HLS packaging, validation, metadata extraction)."""

from .hls_config import (
    HLSConfig,
    calculate_gop_frames,
    format_avc_codec_string,
    parse_frame_rate,
)
from .hls_packager import HLSPackager, build_hls_ffmpeg_command
from .hls_schemas import (
    HLSFailedRenditionInfo,
    HLSRenditionInfo,
    HLSResponse,
    HLSSegmentInfo,
    HLSSkippedRenditionInfo,
)
from .metadata import extract_video_metadata, get_ffprobe_executable
from .schemas import VideoMetadata, VideoUploadResponse
from .service import (
    VideoProcessingService,
    find_source_video,
    resolve_processed_directory,
    resolve_uploads_directory,
    validate_video_id,
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
    "HLSConfig",
    "HLSPackager",
    "HLSResponse",
    "HLSRenditionInfo",
    "HLSSegmentInfo",
    "HLSSkippedRenditionInfo",
    "HLSFailedRenditionInfo",
    "build_hls_ffmpeg_command",
    "parse_frame_rate",
    "calculate_gop_frames",
    "format_avc_codec_string",
    "cleanup_upload",
    "save_upload_file",
    "validate_file_extension",
    "validate_preliminary_mime_type",
    "extract_video_metadata",
    "resolve_uploads_directory",
    "resolve_processed_directory",
    "find_source_video",
    "validate_video_id",
]
