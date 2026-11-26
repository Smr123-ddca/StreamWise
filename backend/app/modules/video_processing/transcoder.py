"""Multi-resolution video transcoding engine powered by FFmpeg.

Converts source video files into standardized quality renditions (360p, 480p, 720p)
while preserving aspect ratio, preventing upscaling, ensuring H.264 mod-2 dimensions,
and validating generated outputs.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional
from fastapi import HTTPException, status

from .metadata import extract_video_metadata
from .schemas import VideoMetadata
from .transcoding_config import (
    DEFAULT_RENDITION_PROFILES,
    RenditionProfile,
    TranscodingConfig,
    calculate_rendition_dimensions,
)
from .transcoding_schemas import (
    FailedRenditionInfo,
    RenditionInfo,
    SkippedRenditionInfo,
    TranscodingResponse,
)


def get_ffmpeg_executable(custom_path: Optional[str] = None) -> str:
    """Locate the ffmpeg executable from settings, environment, or system PATH.

    Raises:
        HTTPException(503): If ffmpeg is not found on the system.
    """
    explicit_path = custom_path or os.getenv("FFMPEG_PATH")
    if explicit_path:
        if os.path.isabs(explicit_path) and os.path.exists(explicit_path):
            return explicit_path
        resolved_explicit = shutil.which(explicit_path)
        if resolved_explicit:
            return resolved_explicit
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Configured FFmpeg executable '{explicit_path}' was not found.",
        )

    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved

    # Check common Windows install paths if not on PATH
    local_app_data = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", "C:\\Program Files")
    common_candidates = [
        Path(local_app_data)
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-9.0.1-essentials_build"
        / "bin"
        / "ffmpeg.exe",
        Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path("C:\\ffmpeg\\bin\\ffmpeg.exe"),
    ]
    for candidate in common_candidates:
        if candidate.exists():
            return str(candidate)

    # Search WinGet packages directory for any ffmpeg installation
    winget_pkg_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if winget_pkg_dir.exists():
        for found in winget_pkg_dir.glob("**/ffmpeg.exe"):
            return str(found)

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="FFmpeg is not installed or not available on the server system PATH.",
    )


def build_ffmpeg_command(
    ffmpeg_bin: str,
    source_path: Path,
    output_path: Path,
    target_width: int,
    target_height: int,
    profile: RenditionProfile,
    config: TranscodingConfig,
    source_has_audio: bool,
) -> List[str]:
    """Build the argument list for FFmpeg transcoding without shell interpolation.

    Args:
        ffmpeg_bin: Path to the FFmpeg executable.
        source_path: Path to the uploaded source video.
        output_path: Target path for the transcoded output MP4.
        target_width: Target scaled width (must be even).
        target_height: Target scaled height (must be even).
        profile: The rendition profile settings (CRF, bitrates).
        config: Transcoding engine configuration (codecs, preset, pix_fmt).
        source_has_audio: True if the source file contains an audio stream.

    Returns:
        List of command-line arguments suitable for subprocess.run.
    """
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-v", "error",
        "-i", str(source_path.resolve()),
        "-map", "0:v:0",
        "-vf", f"scale={target_width}:{target_height},setsar=1",
        "-c:v", config.video_codec,
        "-preset", config.preset,
        "-crf", str(profile.crf),
        "-pix_fmt", config.pixel_format,
    ]

    if config.threads is not None and config.threads > 0:
        cmd.extend(["-threads", str(config.threads)])

    if profile.video_bitrate_max:
        cmd.extend(["-maxrate", profile.video_bitrate_max])
    if profile.bufsize:
        cmd.extend(["-bufsize", profile.bufsize])

    if source_has_audio:
        cmd.extend(["-map", "0:a:0", "-c:a", config.audio_codec, "-b:a", profile.audio_bitrate])
    else:
        cmd.extend(["-an"])

    # Drop subtitle/data streams and place moov atom at front for streaming playback
    cmd.extend([
        "-sn",
        "-dn",
        "-movflags", "+faststart",
        str(output_path.resolve()),
        "-y",
    ])
    return cmd


class VideoTranscoder:
    """Manages multi-resolution video transcoding jobs using FFmpeg."""

    def __init__(self, config: Optional[TranscodingConfig] = None) -> None:
        self.config = config or TranscodingConfig()

    def cleanup_failed_rendition(
        self,
        rendition_dir: Path,
        output_file: Path,
        temp_file: Optional[Path] = None,
    ) -> None:
        """Safely remove staging/failed output files and empty rendition directories."""
        try:
            if temp_file is not None and temp_file.exists():
                temp_file.unlink(missing_ok=True)
            if output_file.exists():
                output_file.unlink(missing_ok=True)
            if rendition_dir.exists() and not any(rendition_dir.iterdir()):
                rendition_dir.rmdir()
        except Exception:
            pass

    def validate_transcoded_output(
        self,
        output_path: Path,
        expected_width: int,
        expected_height: int,
        source_metadata: VideoMetadata,
    ) -> VideoMetadata:
        """Perform comprehensive output validation on a transcoded rendition.

        Validates:
        1. Output file exists on disk.
        2. Output file is non-empty (> 0 bytes).
        3. FFprobe parses the media file without errors.
        4. Video dimensions match expected target width and height.
        5. Video codec matches H.264.
        6. Duration is non-zero (if source > 0) and approximately consistent with source.
        7. Audio stream presence matches source audio presence.

        Args:
            output_path: Path to the generated video file.
            expected_width: Expected pixel width.
            expected_height: Expected pixel height.
            source_metadata: Metadata extracted from the original source video.

        Returns:
            VideoMetadata extracted from the transcoded file.

        Raises:
            ValueError: If any validation check fails.
        """
        if not output_path.exists():
            raise ValueError(f"Transcoded output file '{output_path.name}' does not exist on disk.")

        if output_path.stat().st_size == 0:
            raise ValueError(f"Transcoded output file '{output_path.name}' is empty (0 bytes).")

        out_meta = extract_video_metadata(
            output_path,
            custom_ffprobe_path=self.config.ffprobe_path,
        )

        # Dimension verification
        if out_meta.width != expected_width or out_meta.height != expected_height:
            raise ValueError(
                f"Output dimensions {out_meta.width}x{out_meta.height} do not match expected {expected_width}x{expected_height}."
            )

        # Codec verification (libx264 yields 'h264' / 'avc')
        codec_lower = out_meta.video_codec.lower()
        if "h264" not in codec_lower and "avc" not in codec_lower:
            raise ValueError(f"Output video codec '{out_meta.video_codec}' does not match expected H.264.")

        # Duration verification (reject zero-duration outputs and excessive drift)
        if source_metadata.duration > 0:
            if out_meta.duration <= 0:
                raise ValueError("Transcoded output has invalid zero duration.")
            allowed_drift = max(0.5, source_metadata.duration * 0.15)
            duration_diff = abs(out_meta.duration - source_metadata.duration)
            if duration_diff > allowed_drift:
                raise ValueError(
                    f"Output duration ({out_meta.duration:.2f}s) differs significantly from source ({source_metadata.duration:.2f}s)."
                )

        # Audio stream verification
        if source_metadata.audio_codec is not None:
            if out_meta.audio_codec is None:
                raise ValueError("Source video contains audio, but transcoded output has no audio stream.")
            if "aac" not in out_meta.audio_codec.lower():
                raise ValueError(f"Output audio codec '{out_meta.audio_codec}' does not match expected AAC.")
        else:
            if out_meta.audio_codec is not None:
                raise ValueError("Source video contains no audio, but transcoded output contains an unexpected audio stream.")

        return out_meta

    def transcode_video(
        self,
        source_path: Path,
        output_base_dir: Path,
        video_id: str,
    ) -> TranscodingResponse:
        """Transcode an uploaded source video into multiple standardized renditions.

        Args:
            source_path: Path to the original uploaded video file.
            output_base_dir: Base directory for processed media (storage/processed/).
            video_id: UUID string identifying the video.

        Returns:
            TranscodingResponse containing detailed results for all renditions.

        Raises:
            HTTPException(400): If video_id resolves outside output_base_dir.
            HTTPException(422): If source file is missing, empty, or unparseable.
            HTTPException(503): If FFmpeg or FFprobe are not available.
        """
        start_time = time.perf_counter()

        base_resolved = output_base_dir.resolve()
        video_output_dir = (output_base_dir / video_id).resolve()
        if not video_output_dir.is_relative_to(base_resolved) or video_output_dir == base_resolved:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid video ID path.",
            )

        if not source_path.exists() or source_path.stat().st_size == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Source video file does not exist or is empty for video ID '{video_id}'.",
            )

        # 1. Discover FFmpeg binary early to fail fast if missing
        ffmpeg_bin = get_ffmpeg_executable(self.config.ffmpeg_path)

        # 2. Extract source metadata with FFprobe
        source_metadata = extract_video_metadata(
            source_path,
            custom_ffprobe_path=self.config.ffprobe_path,
        )

        successful_renditions: List[RenditionInfo] = []
        skipped_renditions: List[SkippedRenditionInfo] = []
        failed_renditions: List[FailedRenditionInfo] = []

        source_has_audio = source_metadata.audio_codec is not None

        # 3. Process each configured rendition profile
        for profile in self.config.rendition_profiles:
            dims, skip_reason = calculate_rendition_dimensions(
                src_width=source_metadata.width,
                src_height=source_metadata.height,
                profile=profile,
            )

            if dims is None:
                skipped_renditions.append(
                    SkippedRenditionInfo(
                        rendition=profile.name,
                        reason=skip_reason or "Upscaling prohibited.",
                    )
                )
                continue

            target_w, target_h = dims
            rendition_dir = video_output_dir / profile.name
            rendition_dir.mkdir(parents=True, exist_ok=True)
            temp_file = rendition_dir / "video.tmp.mp4"
            output_file = rendition_dir / "video.mp4"

            cmd = build_ffmpeg_command(
                ffmpeg_bin=ffmpeg_bin,
                source_path=source_path,
                output_path=temp_file,
                target_width=target_w,
                target_height=target_h,
                profile=profile,
                config=self.config,
                source_has_audio=source_has_audio,
            )

            # 4. Execute FFmpeg process into staging file
            try:
                proc = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.config.ffmpeg_timeout,
                    shell=False,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self.cleanup_failed_rendition(rendition_dir, output_file, temp_file)
                stderr_tail = ""
                if exc.stderr:
                    decoded = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                    stderr_tail = f" Details: {decoded.strip()[-300:]}" if decoded.strip() else ""
                failed_renditions.append(
                    FailedRenditionInfo(
                        rendition=profile.name,
                        error=f"FFmpeg transcoding timed out after {self.config.ffmpeg_timeout} seconds.{stderr_tail}",
                    )
                )
                continue
            except Exception as exc:
                self.cleanup_failed_rendition(rendition_dir, output_file, temp_file)
                failed_renditions.append(
                    FailedRenditionInfo(
                        rendition=profile.name,
                        error=f"Failed to execute FFmpeg process: {str(exc)}",
                    )
                )
                continue

            if proc.returncode != 0:
                self.cleanup_failed_rendition(rendition_dir, output_file, temp_file)
                err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
                failed_renditions.append(
                    FailedRenditionInfo(
                        rendition=profile.name,
                        error=f"FFmpeg error: {err_msg}",
                    )
                )
                continue

            # 5. Validate staged output before atomic promotion
            try:
                out_meta = self.validate_transcoded_output(
                    output_path=temp_file,
                    expected_width=target_w,
                    expected_height=target_h,
                    source_metadata=source_metadata,
                )
                os.replace(temp_file, output_file)
            except Exception as val_exc:
                self.cleanup_failed_rendition(rendition_dir, output_file, temp_file)
                failed_renditions.append(
                    FailedRenditionInfo(
                        rendition=profile.name,
                        error=f"Output validation failed: {str(val_exc)}",
                    )
                )
                continue

            successful_renditions.append(
                RenditionInfo(
                    rendition=profile.name,
                    width=out_meta.width,
                    height=out_meta.height,
                    resolution=out_meta.resolution,
                    file_path=f"{video_id}/{profile.name}/video.mp4",
                    file_size=out_meta.file_size,
                    duration=out_meta.duration,
                    video_codec=out_meta.video_codec,
                    audio_codec=out_meta.audio_codec,
                    bitrate=out_meta.bitrate,
                )
            )

        # 6. Determine overall job status
        if len(failed_renditions) > 0 and len(successful_renditions) > 0:
            overall_status = "partial"
        elif len(failed_renditions) > 0 and len(successful_renditions) == 0:
            overall_status = "failed"
        elif len(successful_renditions) == 0 and len(skipped_renditions) > 0:
            overall_status = "skipped"
        else:
            overall_status = "completed"

        duration = round(time.perf_counter() - start_time, 3)

        return TranscodingResponse(
            video_id=video_id,
            status=overall_status,
            overall_status=overall_status,
            successful_renditions=successful_renditions,
            skipped_renditions=skipped_renditions,
            failed_renditions=failed_renditions,
            processing_duration=duration,
        )
