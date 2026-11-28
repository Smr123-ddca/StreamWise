"""Service coordinator for video upload, validation, metadata extraction, and storage."""

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from fastapi import HTTPException, UploadFile, status

from ...config import Settings
from ...config.settings import resolve_storage_directory
from .hls_config import HLSConfig
from .hls_packager import HLSPackager
from .hls_schemas import HLSResponse
from .metadata import extract_video_metadata
from .schemas import VideoUploadResponse
from .transcoder import VideoTranscoder
from .transcoding_config import TranscodingConfig
from .transcoding_schemas import TranscodingResponse
from .uploader import cleanup_upload, save_upload_file
from .validator import validate_file_extension, validate_preliminary_mime_type

# In-process registries of currently running jobs keyed by (processed_dir, video_id)
_active_transcodes_lock = threading.Lock()
_active_transcodes: Dict[Tuple[str, str], float] = {}
_active_hls_jobs: Dict[Tuple[str, str], float] = {}


def validate_video_id(video_id: str) -> str:
    """Validate and canonicalize video_id as a UUID string to prevent path traversal."""
    try:
        parsed = uuid.UUID(video_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid video ID '{video_id}'. Expected a valid UUID.",
        ) from exc
    return str(parsed)


def _resolve_storage_subdir(storage_dir_str: str, subdir: str) -> Path:
    """Resolve uploads or processed storage subdirectory via shared storage resolver."""
    if not storage_dir_str or not storage_dir_str.strip():
        raise ValueError("storage_dir cannot be empty.")
    p = Path(storage_dir_str.strip())
    if p.is_absolute() and p.name == subdir:
        target_dir = p
    else:
        target_dir = resolve_storage_directory(storage_dir_str) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir


def resolve_uploads_directory(storage_dir_str: str) -> Path:
    """Resolve base uploads directory safely across working directories."""
    return _resolve_storage_subdir(storage_dir_str, "uploads")


def resolve_processed_directory(storage_dir_str: str) -> Path:
    """Resolve base processed directory safely across working directories."""
    return _resolve_storage_subdir(storage_dir_str, "processed")


def find_source_video(uploads_dir: Path, video_id: str) -> Optional[Path]:
    """Find the uploaded source video file inside storage/uploads/{video_id}/."""
    base_resolved = uploads_dir.resolve()
    video_dir = (uploads_dir / video_id).resolve()
    if not video_dir.is_relative_to(base_resolved) or video_dir == base_resolved:
        return None
    if not video_dir.exists() or not video_dir.is_dir():
        return None
    for candidate in sorted(video_dir.glob("source.*")):
        if candidate.is_file():
            return candidate
    return None


def _renditions_exist_on_disk(processed_dir: Path, video_id: str, record: TranscodingResponse) -> bool:
    """Verify every successful rendition recorded in status still exists and is non-empty."""
    for rendition in record.successful_renditions:
        expected_file = processed_dir / video_id / rendition.rendition / "video.mp4"
        if not expected_file.exists() or expected_file.stat().st_size == 0:
            return False
    return True


def _hls_outputs_exist_on_disk(processed_dir: Path, video_id: str, record: HLSResponse) -> bool:
    """Verify master playlist, rendition playlists, and all segments exist and are non-empty."""
    hls_dir = processed_dir / video_id / "hls"
    master_file = hls_dir / "master.m3u8"
    if not master_file.exists() or master_file.stat().st_size == 0:
        return False
    if not record.renditions:
        return False
    for r in record.renditions:
        r_dir = hls_dir / r.rendition
        playlist = r_dir / "playlist.m3u8"
        if not playlist.exists() or playlist.stat().st_size == 0:
            return False
        for seg in r.segments:
            seg_file = r_dir / seg.filename
            if not seg_file.exists() or seg_file.stat().st_size == 0:
                return False
    return True


def _save_json_status_atomic(status_file: Path, json_payload: str) -> None:
    """Atomically write a JSON status payload to disk via temp file replacement."""
    temp_file = status_file.with_suffix(".tmp.json")
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(json_payload, encoding="utf-8")
        os.replace(temp_file, status_file)
    except Exception:
        try:
            temp_file.unlink(missing_ok=True)
        except Exception:
            pass


def save_transcode_status(
    processed_dir: Path,
    video_id: str,
    response: TranscodingResponse,
) -> None:
    """Atomically persist transcoding status record to disk."""
    status_file = processed_dir / video_id / "transcode_status.json"
    _save_json_status_atomic(status_file, response.model_dump_json(indent=2))


def load_transcode_status(
    processed_dir: Path,
    video_id: str,
) -> Optional[TranscodingResponse]:
    """Load transcoding status record from disk."""
    status_file = processed_dir / video_id / "transcode_status.json"
    if not status_file.exists():
        return None
    try:
        return TranscodingResponse.model_validate_json(status_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_hls_status(
    processed_dir: Path,
    video_id: str,
    response: HLSResponse,
) -> None:
    """Atomically persist HLS status record to disk."""
    status_file = processed_dir / video_id / "hls" / "hls_status.json"
    _save_json_status_atomic(status_file, response.model_dump_json(indent=2))


def load_hls_status(
    processed_dir: Path,
    video_id: str,
) -> Optional[HLSResponse]:
    """Load HLS status record from disk."""
    status_file = processed_dir / video_id / "hls" / "hls_status.json"
    if not status_file.exists():
        return None
    try:
        return HLSResponse.model_validate_json(status_file.read_text(encoding="utf-8"))
    except Exception:
        return None


class VideoProcessingService:
    """Coordinates validation, file streaming, FFprobe extraction, transcoding, HLS packaging, and cleanup."""

    def __init__(
        self,
        settings: Settings,
        transcoder: Optional[VideoTranscoder] = None,
        hls_packager: Optional[HLSPackager] = None,
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

        if hls_packager is not None:
            self.hls_packager = hls_packager
        else:
            hls_config = HLSConfig(
                segment_duration=settings.hls_segment_duration_seconds,
                ffmpeg_timeout=settings.hls_timeout_seconds,
                ffmpeg_path=settings.ffmpeg_path,
                ffprobe_path=settings.ffprobe_path,
            )
            self.hls_packager = HLSPackager(config=hls_config)

    async def process_video_upload(self, file: UploadFile) -> VideoUploadResponse:
        """Process incoming video upload through the validation and extraction pipeline."""
        original_filename = file.filename or ""

        extension = validate_file_extension(original_filename)
        validate_preliminary_mime_type(file.content_type)

        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)

        video_id, target_path, total_bytes = await save_upload_file(
            file=file,
            uploads_dir=uploads_dir,
            max_size_mb=self.settings.max_upload_size_mb,
            extension=extension,
        )

        video_dir = target_path.parent

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

        return VideoUploadResponse(
            video_id=video_id,
            original_filename=original_filename,
            status="uploaded",
            upload_size=total_bytes,
            metadata=metadata,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def transcode_video(self, video_id: str, force: bool = False) -> TranscodingResponse:
        """Transcode an uploaded video into standardized quality renditions."""
        canonical_id = validate_video_id(video_id)
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, canonical_id)
        if not source_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source video for video ID '{canonical_id}' not found.",
            )

        job_key = (str(processed_dir.resolve()), canonical_id)

        with _active_transcodes_lock:
            if job_key in _active_transcodes:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Transcoding is already in progress for video ID '{canonical_id}'.",
                )
            if job_key in _active_hls_jobs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"HLS packaging is currently in progress for video ID '{canonical_id}'.",
                )

            if not force:
                existing = load_transcode_status(processed_dir, canonical_id)
                if (
                    existing is not None
                    and existing.status in ("completed", "skipped")
                    and _renditions_exist_on_disk(processed_dir, canonical_id, existing)
                ):
                    return existing

            _active_transcodes[job_key] = time.perf_counter()

        try:
            response = self.transcoder.transcode_video(
                source_path=source_path,
                output_base_dir=processed_dir,
                video_id=canonical_id,
            )
            save_transcode_status(processed_dir, canonical_id, response)
            return response
        finally:
            with _active_transcodes_lock:
                _active_transcodes.pop(job_key, None)

    def get_transcode_status(self, video_id: str) -> TranscodingResponse:
        """Retrieve the transcoding status and renditions for a given video ID."""
        canonical_id = validate_video_id(video_id)
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, canonical_id)
        if not source_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source video for video ID '{canonical_id}' not found.",
            )

        job_key = (str(processed_dir.resolve()), canonical_id)
        with _active_transcodes_lock:
            active_start = _active_transcodes.get(job_key)

        if active_start is not None:
            return TranscodingResponse(
                video_id=canonical_id,
                status="processing",
                overall_status="processing",
                successful_renditions=[],
                skipped_renditions=[],
                failed_renditions=[],
                processing_duration=round(time.perf_counter() - active_start, 3),
            )

        status_record = load_transcode_status(processed_dir, canonical_id)
        if not status_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No transcoding status record found for video ID '{canonical_id}'.",
            )
        return status_record

    def package_hls(self, video_id: str, force: bool = False) -> HLSResponse:
        """Package existing MP4 renditions into synchronized MPEG-TS HLS streams."""
        canonical_id = validate_video_id(video_id)
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, canonical_id)
        video_processed_dir = processed_dir / canonical_id
        if not source_path and not video_processed_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video ID '{canonical_id}' not found.",
            )
        if not video_processed_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No transcoded MP4 renditions found for video ID '{canonical_id}'. Transcode the video first.",
            )

        job_key = (str(processed_dir.resolve()), canonical_id)

        with _active_transcodes_lock:
            if job_key in _active_transcodes:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Transcoding is currently in progress for video ID '{canonical_id}'.",
                )
            if job_key in _active_hls_jobs:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"HLS packaging is already in progress for video ID '{canonical_id}'.",
                )

            if not force:
                existing = load_hls_status(processed_dir, canonical_id)
                if (
                    existing is not None
                    and existing.status == "completed"
                    and _hls_outputs_exist_on_disk(processed_dir, canonical_id, existing)
                ):
                    return existing

            _active_hls_jobs[job_key] = time.perf_counter()

        try:
            transcode_record = load_transcode_status(processed_dir, canonical_id)
            response, promoted = self.hls_packager.package_hls(
                output_base_dir=processed_dir,
                video_id=canonical_id,
                transcode_record=transcode_record,
            )
            if not promoted:
                # Preserve any previously valid HLS output and its status on disk
                existing_master = video_processed_dir / "hls" / "master.m3u8"
                if not existing_master.exists():
                    save_hls_status(processed_dir, canonical_id, response)
            return response
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error during HLS packaging: {str(exc)}",
            ) from exc
        finally:
            with _active_transcodes_lock:
                _active_hls_jobs.pop(job_key, None)

    def get_hls_status(self, video_id: str) -> HLSResponse:
        """Retrieve HLS packaging status and playlist details for a given video ID."""
        canonical_id = validate_video_id(video_id)
        uploads_dir = resolve_uploads_directory(self.settings.storage_dir)
        processed_dir = resolve_processed_directory(self.settings.storage_dir)

        source_path = find_source_video(uploads_dir, canonical_id)
        video_processed_dir = processed_dir / canonical_id
        if not source_path and not video_processed_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video ID '{canonical_id}' not found.",
            )

        job_key = (str(processed_dir.resolve()), canonical_id)
        with _active_transcodes_lock:
            active_start = _active_hls_jobs.get(job_key)

        if active_start is not None:
            return HLSResponse(
                video_id=canonical_id,
                status="processing",
                overall_status="processing",
                master_playlist_path=None,
                segment_duration=self.hls_packager.config.segment_duration,
                renditions=[],
                skipped_renditions=[],
                failed_renditions=[],
                processing_duration=round(time.perf_counter() - active_start, 3),
            )

        status_record = load_hls_status(processed_dir, canonical_id)
        if not status_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No HLS packaging status record found for video ID '{canonical_id}'.",
            )
        return status_record
