"""Video processing module (transcoding, validation, metadata extraction)."""

from .metadata import extract_video_metadata
from .schemas import VideoMetadata, VideoUploadResponse
from .service import VideoProcessingService
from .uploader import cleanup_upload, save_upload_file
from .validator import validate_file_extension, validate_preliminary_mime_type

__all__ = [
    "VideoMetadata",
    "VideoUploadResponse",
    "VideoProcessingService",
    "cleanup_upload",
    "save_upload_file",
    "validate_file_extension",
    "validate_preliminary_mime_type",
    "extract_video_metadata",
]
