"""Video metadata extraction using FFprobe."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import HTTPException, status

from .schemas import VideoMetadata


def get_ffprobe_executable(custom_path: Optional[str] = None) -> str:
    """Locate the ffprobe executable from settings, environment, or system PATH."""
    path = custom_path or os.getenv("FFPROBE_PATH")
    if path and os.path.isfile(path):
        return str(Path(path).resolve())

    resolved = shutil.which(path or "ffprobe")
    if resolved:
        return resolved

    # Check common Windows install paths if not on PATH
    local_app_data = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", "C:\\Program Files")
    common_candidates = [
        Path(local_app_data) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-9.0.1-essentials_build" / "bin" / "ffprobe.exe",
        Path(program_files) / "ffmpeg" / "bin" / "ffprobe.exe",
        Path("C:\\ffmpeg\\bin\\ffprobe.exe"),
    ]
    for candidate in common_candidates:
        if candidate.is_file():
            return str(candidate)

    # Search WinGet packages directory for any ffmpeg/ffprobe installation
    winget_pkg_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if winget_pkg_dir.is_dir():
        for found in winget_pkg_dir.glob("**/ffprobe.exe"):
            if found.is_file():
                return str(found)

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="FFprobe is not installed or not available on the server system PATH.",
    )


def extract_video_metadata(
    file_path: Path,
    timeout_seconds: int = 15,
    custom_ffprobe_path: Optional[str] = None,
) -> VideoMetadata:
    """Extract technical metadata from a media file using ffprobe.

    Args:
        file_path: Absolute or relative Path to the saved video file.
        timeout_seconds: Maximum time in seconds allowed for ffprobe execution.
        custom_ffprobe_path: Optional explicit path to ffprobe executable.

    Returns:
        VideoMetadata schema populated with extracted technical information.

    Raises:
        HTTPException(503): If ffprobe is not installed or found on the system.
        HTTPException(422): If ffprobe fails, times out, or cannot parse video streams.
    """
    if not file_path.exists():
        raise HTTPException(
            status_code=422,
            detail="Media file does not exist on disk.",
        )

    ffprobe_cmd = get_ffprobe_executable(custom_ffprobe_path)

    cmd = [
        ffprobe_cmd,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(file_path.resolve()),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except OSError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FFprobe executable could not be executed.",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=422,
            detail="Metadata extraction timed out while reading the media file.",
        )

    if result.returncode != 0:
        raise HTTPException(
            status_code=422,
            detail="Invalid or corrupted video file could not be parsed by FFprobe.",
        )

    try:
        data: Dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="Failed to decode FFprobe output as JSON.",
        )

    format_info = data.get("format", {})
    streams = data.get("streams", [])

    # Identify primary video stream
    video_stream: Optional[Dict[str, Any]] = None
    audio_stream: Optional[Dict[str, Any]] = None

    for s in streams:
        c_type = s.get("codec_type")
        if c_type == "video" and video_stream is None:
            video_stream = s
        elif c_type == "audio" and audio_stream is None:
            audio_stream = s

    if not video_stream:
        raise HTTPException(
            status_code=422,
            detail="The uploaded media file does not contain a valid video stream.",
        )

    try:
        width = int(video_stream.get("width"))
        height = int(video_stream.get("height"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Video stream dimensions are invalid or missing.",
        ) from exc
    if width <= 0 or height <= 0:
        raise HTTPException(
            status_code=422,
            detail="Video stream dimensions are invalid or missing.",
        )

    # Calculate frame rate string
    frame_rate = video_stream.get("r_frame_rate")
    if not frame_rate or frame_rate == "0/0":
        frame_rate = video_stream.get("avg_frame_rate", "30/1")
    if not frame_rate or frame_rate == "0/0":
        frame_rate = "30/1"

    # Duration parsing
    raw_duration = format_info.get("duration") or video_stream.get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else 0.0
    except (ValueError, TypeError):
        duration = 0.0

    # Bitrate parsing
    raw_bitrate = format_info.get("bit_rate") or video_stream.get("bit_rate")
    bitrate: Optional[int] = None
    if raw_bitrate:
        try:
            bitrate = int(raw_bitrate)
        except (ValueError, TypeError):
            bitrate = None

    # File size
    file_size = os.path.getsize(file_path)

    # Audio codec
    audio_codec = audio_stream.get("codec_name") if audio_stream else None

    # Video codec and container
    video_codec = video_stream.get("codec_name", "unknown")
    container = format_info.get("format_name", "unknown")

    return VideoMetadata(
        duration=duration,
        width=int(width),
        height=int(height),
        resolution=f"{width}x{height}",
        frame_rate=str(frame_rate),
        video_codec=str(video_codec),
        audio_codec=str(audio_codec) if audio_codec else None,
        container=str(container),
        bitrate=bitrate,
        file_size=file_size,
    )
