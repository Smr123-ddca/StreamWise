"""FFmpeg/FFprobe-based frame extraction for HLS video segments."""

import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)


class MediaExtractionError(RuntimeError):
    """Raised when FFmpeg cannot decode or extract frames from a media segment."""


def _find_binary(
    binary_name: str,
    custom_path: Optional[str] = None,
    env_var: Optional[str] = None,
) -> str:
    """Locate an executable binary from custom path, environment variable, PATH, or WinGet."""
    candidate_override = custom_path or (os.getenv(env_var) if env_var else None)
    if candidate_override:
        override_path = Path(candidate_override)
        if override_path.is_file():
            return str(override_path)
        resolved_override = shutil.which(candidate_override)
        if resolved_override:
            return resolved_override

    resolved = shutil.which(binary_name)
    if resolved:
        return resolved

    exe_name = f"{binary_name}.exe" if os.name == "nt" and not binary_name.endswith(".exe") else binary_name
    local_app_data = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")

    common_paths = [
        Path(program_files) / "ffmpeg" / "bin" / exe_name,
        Path(r"C:\ffmpeg\bin") / exe_name,
    ]
    for candidate in common_paths:
        if candidate.is_file():
            return str(candidate)

    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.is_dir():
            for found in winget_dir.glob(f"**/{exe_name}"):
                if found.is_file():
                    return str(found)

    raise RuntimeError(
        f"'{binary_name}' executable not found. Install FFmpeg/FFprobe or set {env_var or binary_name.upper()}."
    )


def get_ffmpeg_executable(custom_path: Optional[str] = None) -> str:
    """Return path to the ffmpeg executable."""
    return _find_binary("ffmpeg", custom_path=custom_path, env_var="FFMPEG_PATH")


def get_ffprobe_executable(custom_path: Optional[str] = None) -> str:
    """Return path to the ffprobe executable."""
    return _find_binary("ffprobe", custom_path=custom_path, env_var="FFPROBE_PATH")


def probe_media_duration(
    media_path: Path,
    timeout_sec: int = 15,
    custom_ffprobe_path: Optional[str] = None,
) -> Optional[float]:
    """Return media duration in seconds using ffprobe, or None if unavailable."""
    if timeout_sec < 1:
        raise ValueError("timeout_sec must be at least 1.")
    if not media_path.is_file():
        raise FileNotFoundError(f"Media file not found: {media_path}")

    ffprobe_cmd = get_ffprobe_executable(custom_ffprobe_path)
    cmd = [
        ffprobe_cmd,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path.resolve()),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            shell=False,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("ffprobe failed for %s: %s", media_path.name, exc)
        return None
    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    if not raw or raw.upper() == "N/A":
        return None
    try:
        duration = float(raw)
        return duration if duration > 0.0 else None
    except ValueError:
        return None


def extract_frames(
    segment_path: Path,
    output_dir: Path,
    num_frames: int = 3,
    image_format: str = "jpg",
    timeout_sec: int = 30,
    default_duration_sec: float = 6.0,
    custom_ffmpeg_path: Optional[str] = None,
    custom_ffprobe_path: Optional[str] = None,
) -> List[Path]:
    """Extract evenly-spaced representative frames from a video segment.

    Computes the target extraction FPS from the probed segment duration so short
    HLS segments (e.g., 2s-6s) reliably yield up to ``num_frames`` images.

    Note:
        This function clears any existing ``frame_*.{image_format}`` files in
        ``output_dir`` before extracting; callers must pass an isolated
        ``output_dir`` per concurrent invocation.
    """
    if num_frames < 1:
        raise ValueError("num_frames must be at least 1.")
    if timeout_sec < 1:
        raise ValueError("timeout_sec must be at least 1.")
    if not segment_path.is_file():
        raise FileNotFoundError(f"Segment file not found: {segment_path}")
    if image_format not in {"jpg", "png"}:
        raise ValueError("image_format must be 'jpg' or 'png'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob(f"frame_*.{image_format}"):
        stale.unlink(missing_ok=True)

    duration = None
    try:
        duration = probe_media_duration(
            segment_path,
            timeout_sec=min(timeout_sec, 15),
            custom_ffprobe_path=custom_ffprobe_path,
        )
    except RuntimeError:
        duration = None

    effective_duration = duration if duration and duration > 0.0 else default_duration_sec
    # Compute fps so N frames span the entire segment evenly regardless of segment length
    target_fps = max(num_frames / max(effective_duration, 0.25), 0.001)

    ffmpeg_cmd = get_ffmpeg_executable(custom_ffmpeg_path)
    output_pattern = output_dir / f"frame_%03d.{image_format}"

    cmd = [
        ffmpeg_cmd,
        "-y",
        "-v",
        "error",
        "-i",
        str(segment_path.resolve()),
        "-vf",
        f"fps={target_fps:.6f}",
        "-frames:v",
        str(num_frames),
        str(output_pattern),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("FFmpeg timed out after %ss for %s.", timeout_sec, segment_path.name)
        raise TimeoutError(
            f"Frame extraction timed out after {timeout_sec}s for {segment_path.name}."
        ) from exc
    except OSError as exc:
        logger.error("Failed to execute FFmpeg for %s: %s", segment_path.name, exc)
        raise RuntimeError(f"FFmpeg execution failed for {segment_path.name}: {exc}") from exc

    if result.returncode != 0:
        stderr_msg = result.stderr.strip() or "FFmpeg failed with unknown error."
        logger.error("Frame extraction failed for %s: %s", segment_path, stderr_msg)
        raise MediaExtractionError(f"Frame extraction failed for {segment_path.name}: {stderr_msg}")

    frames = sorted(output_dir.glob(f"frame_*.{image_format}"))
    if not frames:
        logger.warning("fps filter yielded 0 frames for %s; falling back to first-frame extraction.", segment_path)
        # Fallback for ultra-short clips where fps filter skips all frames: extract first frame
        fallback_frame = output_dir / f"frame_001.{image_format}"
        fallback_cmd = [
            ffmpeg_cmd,
            "-y",
            "-v",
            "error",
            "-i",
            str(segment_path.resolve()),
            "-frames:v",
            "1",
            str(fallback_frame),
        ]
        try:
            fallback_res = subprocess.run(
                fallback_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("FFmpeg fallback timed out after %ss for %s.", timeout_sec, segment_path.name)
            raise TimeoutError(
                f"Fallback frame extraction timed out after {timeout_sec}s for {segment_path.name}."
            ) from exc
        except OSError as exc:
            logger.error("Failed to execute FFmpeg fallback for %s: %s", segment_path.name, exc)
            raise RuntimeError(f"FFmpeg execution failed for {segment_path.name}: {exc}") from exc

        if fallback_res.returncode == 0 and fallback_frame.is_file():
            return [fallback_frame]
        logger.error("Fallback frame extraction also failed for %s.", segment_path)
        raise MediaExtractionError(f"No frames could be extracted from segment: {segment_path}")

    logger.debug("Extracted %d frame(s) from %s (duration=%.2fs, fps=%.3f).", len(frames), segment_path.name, effective_duration, target_fps)
    return frames
