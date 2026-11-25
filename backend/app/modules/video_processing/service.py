"""Service coordinator for video upload, validation, metadata extraction, and storage."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException, UploadFile, status

from ...config import Settings
from ...config.settings import resolve_storage_directory
from .metadata import extract_video_metadata
from .schemas import VideoUploadResponse
from .uploader import cleanup_upload, save_upload_file
from .validator import validate_file_extension, validate_preliminary_mime_type


def resolve_uploads_directory(storage_dir_str: str) -> Path:
    """Resolve base uploads directory safely across working directories."""
    p = Path(storage_dir_str)
    if p.is_absolute() and p.name == "uploads":
        uploads_dir = p
    else:
        uploads_dir = resolve_storage_directory(storage_dir_str) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


class VideoProcessingService:
    """Coordinates validation, file streaming, FFprobe extraction, and cleanup."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def process_video_upload(self, file: UploadFile) -> VideoUploadResponse:
        """Process incoming video upload through the validation and extraction pipeline.

        Args:
            file: Incoming multipart/form-data video upload.

        Returns:
            VideoUploadResponse populated with video ID, original filename, and technical metadata.
        """
        original_filename = file.filename or ""

        # 1. Preliminary format & extension validation
        extension = validate_file_extension(original_filename)
        validate_preliminary_mime_type(file.content_type)

        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)

        # 2. Stream upload to disk with size limiting
        video_id, target_path, total_bytes = await save_upload_file(
            file=file,
            uploads_dir=uploads_dir,
            max_size_mb=self.settings.max_upload_size_mb,
            extension=extension,
        )

        video_dir = target_path.parent

        # 3. Extract technical metadata with FFprobe & validate content
        try:
            metadata = await asyncio.to_thread(
                extract_video_metadata,
                target_path,
                custom_ffprobe_path=self.settings.ffprobe_path,
            )
        except HTTPException:
            cleanup_upload(video_dir)
            raise
        except Exception as exc:
            cleanup_upload(video_dir)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unexpected error occurred during video processing.",
            ) from exc

        # 4. Return structured response record
        return VideoUploadResponse(
            video_id=video_id,
            original_filename=original_filename,
            status="uploaded",
            upload_size=total_bytes,
            metadata=metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
