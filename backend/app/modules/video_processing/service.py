"""Service coordinator for video upload, validation, metadata extraction, and storage."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from fastapi import HTTPException, UploadFile, status

from ...config import Settings
from .metadata import extract_video_metadata
from .schemas import VideoUploadResponse
from .transcoder import VideoTranscoder
from .transcoding_config import TranscodingConfig
from .transcoding_schemas import TranscodingResponse
from .uploader import cleanup_upload, save_upload_file
from .validator import validate_file_extension, validate_preliminary_mime_type

_transcode_status_cache: Dict[str, TranscodingResponse] = {}


def resolve_uploads_directory(storage_dir_str: str) -> Path:
    """Resolve base uploads directory safely across working directories."""
    p = Path(storage_dir_str)
    resolved_storage: Path

    if p.is_absolute():
        resolved_storage = p
    elif (Path.cwd() / p).resolve().exists():
        resolved_storage = (Path.cwd() / p).resolve()
    else:
        # Check relative to backend package root
        backend_dir = Path(__file__).resolve().parent.parent.parent.parent
        if (backend_dir / p).resolve().exists():
            resolved_storage = (backend_dir / p).resolve()
        else:
            # Check repository root storage
            repo_storage = (backend_dir.parent / "storage").resolve()
            if repo_storage.exists():
                resolved_storage = repo_storage
            else:
                resolved_storage = (Path.cwd() / p).resolve()

    uploads_dir = (
        resolved_storage if resolved_storage.name == "uploads"
        else resolved_storage / "uploads"
    )
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


def resolve_processed_directory(storage_dir_str: str) -> Path:
    """Resolve base processed directory safely across working directories."""
    p = Path(storage_dir_str)
    resolved_storage: Path

    if p.is_absolute():
        resolved_storage = p
    elif (Path.cwd() / p).resolve().exists():
        resolved_storage = (Path.cwd() / p).resolve()
    else:
        # Check relative to backend package root
        backend_dir = Path(__file__).resolve().parent.parent.parent.parent
        if (backend_dir / p).resolve().exists():
            resolved_storage = (backend_dir / p).resolve()
        else:
            # Check repository root storage
            repo_storage = (backend_dir.parent / "storage").resolve()
            if repo_storage.exists():
                resolved_storage = repo_storage
            else:
                resolved_storage = (Path.cwd() / p).resolve()

    processed_dir = (
        resolved_storage if resolved_storage.name == "processed"
        else resolved_storage / "processed"
    )
    processed_dir.mkdir(parents=True, exist_ok=True)
    return processed_dir


def find_source_video(uploads_dir: Path, video_id: str) -> Optional[Path]:
    """Find the uploaded source video file inside storage/uploads/{video_id}/."""
    video_dir = uploads_dir / video_id
    if not video_dir.exists() or not video_dir.is_dir():
        return None
    for candidate in sorted(video_dir.glob("source.*")):
        if candidate.is_file():
            return candidate
    return None


def save_transcode_status(
    processed_dir: Path,
    video_id: str,
    response: TranscodingResponse,
) -> None:
    """Persist transcoding status record in memory and to disk."""
    _transcode_status_cache[video_id] = response
    status_file = processed_dir / video_id / "transcode_status.json"
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(response.model_dump_json(indent=2), encoding="utf-8")
    except Exception:
        pass


def load_transcode_status(
    processed_dir: Path,
    video_id: str,
) -> Optional[TranscodingResponse]:
    """Load transcoding status record from cache or disk."""
    if video_id in _transcode_status_cache:
        return _transcode_status_cache[video_id]
    status_file = processed_dir / video_id / "transcode_status.json"
    if status_file.exists():
        try:
            record = TranscodingResponse.model_validate_json(status_file.read_text(encoding="utf-8"))
            _transcode_status_cache[video_id] = record
            return record
        except Exception:
            return None
    return None


class VideoProcessingService:
    """Coordinates validation, file streaming, FFprobe extraction, transcoding, and cleanup."""

    def __init__(
        self,
        settings: Settings,
        transcoder: Optional[VideoTranscoder] = None,
    ) -> None:
        self.settings = settings
        if transcoder is not None:
            self.transcoder = transcoder
        else:
            config = TranscodingConfig(
                ffmpeg_path=settings.ffmpeg_path,
                ffprobe_path=settings.ffprobe_path,
                ffmpeg_timeout=settings.transcoding_timeout_seconds,
            )
            self.transcoder = VideoTranscoder(config=config)

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
            metadata = extract_video_metadata(
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

    def transcode_video(self, video_id: str) -> TranscodingResponse:
        """Transcode an uploaded video into standardized quality renditions.

        Args:
            video_id: UUID string identifying the uploaded video.

        Returns:
            TranscodingResponse containing the generated rendition information.

        Raises:
            HTTPException(404): If the video ID is not found in uploads.
            HTTPException(422): If source file is corrupted or unparseable.
            HTTPException(503): If FFmpeg or FFprobe are missing.
        """
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, video_id)
        if not source_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source video for video ID '{video_id}' not found.",
            )

        response = self.transcoder.transcode_video(
            source_path=source_path,
            output_base_dir=processed_dir,
            video_id=video_id,
        )

        save_transcode_status(processed_dir, video_id, response)
        return response

    def get_transcode_status(self, video_id: str) -> TranscodingResponse:
        """Retrieve the transcoding status and renditions for a given video ID.

        Args:
            video_id: UUID string identifying the video.

        Returns:
            TranscodingResponse matching the latest transcode result.

        Raises:
            HTTPException(404): If video is not found or has not been transcoded.
        """
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, video_id)
        if not source_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source video for video ID '{video_id}' not found.",
            )

        status_record = load_transcode_status(processed_dir, video_id)
        if not status_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No transcoding status record found for video ID '{video_id}'.",
            )
        return status_record
