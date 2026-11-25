"""File upload handler streaming chunks and enforcing storage isolation."""

import shutil
import uuid
from pathlib import Path
from fastapi import HTTPException, UploadFile, status

CHUNK_SIZE = 1024 * 1024  # 1 MB stream chunk


def cleanup_upload(video_dir: Path) -> None:
    """Safely remove the temporary/target video directory on failure."""
    try:
        if video_dir.exists():
            shutil.rmtree(video_dir, ignore_errors=True)
    except Exception:
        pass


async def save_upload_file(
    file: UploadFile,
    uploads_dir: Path,
    max_size_mb: int,
    extension: str,
) -> tuple[str, Path, int]:
    """Stream incoming UploadFile to disk in chunks with size limit enforcement.

    Args:
        file: FastAPI UploadFile instance.
        uploads_dir: Base directory for storing uploads (e.g. storage/uploads).
        max_size_mb: Maximum allowed file size in megabytes.
        extension: Validated lowercase file extension including dot (e.g. '.mp4').

    Returns:
        tuple of (video_id: str, target_path: Path, total_bytes: int)

    Raises:
        HTTPException(400): If the file is 0 bytes.
        HTTPException(413): If file exceeds max_size_mb limit.
    """
    video_id = str(uuid.uuid4())
    video_dir = uploads_dir / video_id
    video_dir.mkdir(parents=True, exist_ok=True)

    clean_ext = extension if extension.startswith(".") else f".{extension}"
    target_path = video_dir / f"source{clean_ext}"

    max_bytes = max_size_mb * 1024 * 1024
    total_bytes = 0

    try:
        with open(target_path, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    out_file.close()
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed upload size of {max_size_mb} MB.",
                    )

                out_file.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty (0 bytes).",
            )

        return video_id, target_path, total_bytes

    except HTTPException:
        cleanup_upload(video_dir)
        raise
    except Exception as exc:
        cleanup_upload(video_dir)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred while writing video upload.",
        ) from exc
