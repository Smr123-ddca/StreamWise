"""HLS packaging engine converting MP4 renditions into synchronized MPEG-TS HLS streams.

Operates on existing MP4 renditions in storage/processed/{video_id}/{rendition}/video.mp4
without modifying original uploads or MP4 outputs. Enforces fixed closed-GOP keyframes,
validates every segment and keyframe boundary with FFprobe, and promotes validated
outputs atomically from temporary staging directories.
"""

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from fastapi import HTTPException, status

from .hls_config import (
    HLSConfig,
    calculate_gop_frames,
    format_avc_codec_string,
    parse_frame_rate,
)
from .hls_schemas import (
    HLSFailedRenditionInfo,
    HLSRenditionInfo,
    HLSResponse,
    HLSSegmentInfo,
    HLSSkippedRenditionInfo,
)
from .metadata import extract_video_metadata, get_ffprobe_executable
from .schemas import VideoMetadata
from .transcoder import get_ffmpeg_executable
from .transcoding_schemas import TranscodingResponse

_EXTINF_PATTERN = re.compile(r"^#EXTINF:([0-9]+(?:\.[0-9]+)?),")


def build_hls_ffmpeg_command(
    ffmpeg_bin: str,
    source_mp4: Path,
    playlist_path: Path,
    segment_pattern: Path,
    fps: float,
    crf: int,
    audio_bitrate: str,
    config: HLSConfig,
    has_audio: bool,
) -> List[str]:
    """Build FFmpeg argument list for closed-GOP, keyframe-aligned HLS segmentation."""
    gop_frames = calculate_gop_frames(fps, config.segment_duration)
    keyframe_expr = f"expr:gte(t,n_forced*{config.segment_duration})"

    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-v", "error",
        "-i", str(source_mp4.resolve()),
        "-map", "0:v:0",
        "-c:v", config.video_codec,
        "-preset", config.preset,
        "-crf", str(crf),
        "-pix_fmt", config.pixel_format,
        "-g", str(gop_frames),
        "-keyint_min", str(gop_frames),
        "-sc_threshold", "0",
        "-x264-params", "scenecut=0:open-gop=0",
        "-force_key_frames", keyframe_expr,
    ]

    if config.threads is not None and config.threads > 0:
        cmd.extend(["-threads", str(config.threads)])

    if has_audio:
        cmd.extend(["-map", "0:a:0", "-c:a", config.audio_codec, "-b:a", audio_bitrate])
    else:
        cmd.extend(["-an"])

    cmd.extend([
        "-sn",
        "-dn",
        "-f", "hls",
        "-hls_time", str(config.segment_duration),
        "-hls_playlist_type", config.playlist_type,
        "-hls_flags", "independent_segments",
        "-hls_segment_type", config.segment_type,
        "-hls_segment_filename", str(segment_pattern.resolve()),
        str(playlist_path.resolve()),
        "-y",
    ])
    return cmd


def _probe_segment(
    segment_path: Path,
    ffprobe_bin: str,
    timeout_seconds: int = 15,
) -> dict:
    """Inspect a single HLS MPEG-TS segment using FFprobe for streams and initial frame keyframe."""
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-read_intervals", "%+#5",
        "-show_streams",
        "-show_entries", "frame=stream_index,pts_time,pict_type,key_frame",
        "-of", "json",
        str(segment_path.resolve()),
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
        raise ValueError(f"FFprobe failed on segment '{segment_path.name}': {err}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid FFprobe JSON for segment '{segment_path.name}'.") from exc


def parse_media_playlist(playlist_path: Path) -> List[Tuple[float, str]]:
    """Parse an HLS media playlist and validate required M3U8 directives.

    Returns:
        List of (extinf_duration, segment_filename) tuples.
    """
    if not playlist_path.exists() or playlist_path.stat().st_size == 0:
        raise ValueError(f"Media playlist '{playlist_path.name}' is missing or empty.")

    lines = [line.strip() for line in playlist_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError(f"Playlist '{playlist_path.name}' missing #EXTM3U header.")
    if "#EXT-X-ENDLIST" not in lines:
        raise ValueError(f"VOD playlist '{playlist_path.name}' missing #EXT-X-ENDLIST.")
    if "#EXT-X-INDEPENDENT-SEGMENTS" not in lines:
        raise ValueError(f"Playlist '{playlist_path.name}' missing #EXT-X-INDEPENDENT-SEGMENTS.")
    if not any(l.startswith("#EXT-X-TARGETDURATION:") for l in lines):
        raise ValueError(f"Playlist '{playlist_path.name}' missing #EXT-X-TARGETDURATION.")

    entries: List[Tuple[float, str]] = []
    pending_duration: Optional[float] = None
    for line in lines:
        match = _EXTINF_PATTERN.match(line)
        if match:
            pending_duration = float(match.group(1))
            continue
        if line.startswith("#"):
            continue
        if pending_duration is None:
            raise ValueError(f"Segment entry '{line}' missing preceding #EXTINF directive.")
        if "/" in line or "\\" in line or ".." in line:
            raise ValueError(f"Unsafe segment path '{line}' in playlist.")
        entries.append((pending_duration, line))
        pending_duration = None

    if not entries:
        raise ValueError(f"Playlist '{playlist_path.name}' contains no media segments.")
    return entries


def build_master_playlist_content(renditions: List[HLSRenditionInfo]) -> str:
    """Generate master.m3u8 content from validated renditions ordered by bandwidth."""
    ordered = sorted(renditions, key=lambda r: (r.bandwidth, r.height))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for r in ordered:
        lines.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={r.bandwidth},AVERAGE-BANDWIDTH={r.average_bandwidth},'
            f'RESOLUTION={r.resolution},CODECS="{r.codecs}"'
        )
        lines.append(r.relative_playlist_uri)
    return "\n".join(lines) + "\n"


def _cleanup_abandoned_dirs(video_processed_dir: Path) -> None:
    """Remove leftover staging or backup directories from interrupted runs."""
    if not video_processed_dir.exists():
        return
    for entry in video_processed_dir.iterdir():
        if entry.is_dir() and (
            entry.name.startswith(".hls_staging_") or entry.name.startswith(".hls_backup_")
        ):
            shutil.rmtree(entry, ignore_errors=True)


def _promote_staging_to_hls(staging_dir: Path, hls_dir: Path) -> None:
    """Atomically replace hls_dir with validated staging_dir safely on Windows & POSIX.

    Preserves any existing hls_dir in a backup folder during promotion and rolls back
    if directory replacement fails.
    """
    backup_dir = hls_dir.parent / f".hls_backup_{uuid.uuid4().hex[:8]}"
    had_existing = hls_dir.exists()
    if had_existing:
        os.replace(hls_dir, backup_dir)

    try:
        os.replace(staging_dir, hls_dir)
    except Exception:
        if had_existing and backup_dir.exists() and not hls_dir.exists():
            try:
                os.replace(backup_dir, hls_dir)
            except Exception:
                pass
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


class HLSPackager:
    """Packages multi-resolution MP4 renditions into synchronized HLS streams."""

    def __init__(self, config: Optional[HLSConfig] = None) -> None:
        self.config = config or HLSConfig()

    def _discover_rendition_sources(
        self,
        video_processed_dir: Path,
        transcode_record: Optional[TranscodingResponse],
    ) -> Tuple[List[Tuple[str, Path]], List[HLSSkippedRenditionInfo], List[HLSFailedRenditionInfo]]:
        """Identify candidate MP4 renditions, preserving skip/failure diagnostics."""
        candidates: List[Tuple[str, Path]] = []
        skipped: List[HLSSkippedRenditionInfo] = []
        failed: List[HLSFailedRenditionInfo] = []

        if transcode_record is not None:
            for s in transcode_record.skipped_renditions:
                skipped.append(HLSSkippedRenditionInfo(rendition=s.rendition, reason=s.reason))
            for f in transcode_record.failed_renditions:
                failed.append(
                    HLSFailedRenditionInfo(
                        rendition=f.rendition,
                        error=f"MP4 transcoding previously failed: {f.error}",
                    )
                )
            for r in transcode_record.successful_renditions:
                mp4_path = video_processed_dir / r.rendition / "video.mp4"
                if mp4_path.exists() and mp4_path.is_file() and mp4_path.stat().st_size > 0:
                    candidates.append((r.rendition, mp4_path))
                else:
                    failed.append(
                        HLSFailedRenditionInfo(
                            rendition=r.rendition,
                            error=f"Expected MP4 rendition '{r.rendition}/video.mp4' is missing or empty.",
                        )
                    )
            return candidates, skipped, failed

        # Fallback discovery if transcode_status.json is absent but rendition directories exist
        for name in self.config.rendition_profiles:
            r_dir = video_processed_dir / name
            mp4_path = r_dir / "video.mp4"
            if r_dir.exists():
                if mp4_path.exists() and mp4_path.stat().st_size > 0:
                    candidates.append((name, mp4_path))
                else:
                    failed.append(
                        HLSFailedRenditionInfo(
                            rendition=name,
                            error=f"MP4 rendition '{name}/video.mp4' is missing or empty.",
                        )
                    )
        return candidates, skipped, failed

    def _validate_rendition_hls(
        self,
        video_id: str,
        rendition: str,
        staged_rendition_dir: Path,
        source_mp4_meta: VideoMetadata,
        ffprobe_bin: str,
    ) -> HLSRenditionInfo:
        """Validate generated playlist, every .ts segment, IDR keyframes, codecs, and durations."""
        staged_playlist = staged_rendition_dir / "playlist.m3u8"
        entries = parse_media_playlist(staged_playlist)

        segments: List[HLSSegmentInfo] = []
        total_duration = 0.0
        total_bytes = 0
        h264_profile: Optional[str] = None
        h264_level: Optional[int] = None
        has_audio = source_mp4_meta.audio_codec is not None

        for seg_dur, seg_name in entries:
            seg_path = staged_rendition_dir / seg_name
            if not seg_path.exists() or not seg_path.is_file():
                raise ValueError(f"Referenced HLS segment '{seg_name}' does not exist.")
            seg_size = seg_path.stat().st_size
            if seg_size == 0:
                raise ValueError(f"HLS segment '{seg_name}' is empty (0 bytes).")

            probe_data = _probe_segment(
                seg_path,
                ffprobe_bin=ffprobe_bin,
                timeout_seconds=self.config.ffprobe_timeout,
            )
            streams = probe_data.get("streams", [])
            frames = probe_data.get("frames", [])

            video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            if not video_stream:
                raise ValueError(f"Segment '{seg_name}' has no video stream.")

            v_codec = str(video_stream.get("codec_name", "")).lower()
            if "h264" not in v_codec and "avc" not in v_codec:
                raise ValueError(f"Segment '{seg_name}' video codec '{v_codec}' is not H.264.")

            s_w = int(video_stream.get("width") or 0)
            s_h = int(video_stream.get("height") or 0)
            if s_w != source_mp4_meta.width or s_h != source_mp4_meta.height:
                raise ValueError(
                    f"Segment '{seg_name}' dimensions {s_w}x{s_h} do not match expected {source_mp4_meta.width}x{source_mp4_meta.height}."
                )

            if has_audio:
                if not audio_stream or "aac" not in str(audio_stream.get("codec_name", "")).lower():
                    raise ValueError(f"Segment '{seg_name}' missing expected AAC audio stream.")
            else:
                if audio_stream is not None:
                    raise ValueError(f"Segment '{seg_name}' contains unexpected audio stream.")

            video_idx = int(video_stream.get("index", 0))
            video_frames = [f for f in frames if int(f.get("stream_index", video_idx)) == video_idx]
            if not video_frames:
                raise ValueError(f"Segment '{seg_name}' contains no video frames.")

            first_frame = video_frames[0]
            if int(first_frame.get("key_frame", 0)) != 1:
                raise ValueError(
                    f"Segment '{seg_name}' does not begin with a keyframe/IDR (pict_type={first_frame.get('pict_type')})."
                )
            pts_raw = first_frame.get("pts_time")
            if pts_raw is None:
                raise ValueError(f"Segment '{seg_name}' first frame has no pts_time.")

            if h264_profile is None:
                h264_profile = video_stream.get("profile")
                h264_level = video_stream.get("level")

            total_duration += seg_dur
            total_bytes += seg_size
            segments.append(
                HLSSegmentInfo(
                    filename=seg_name,
                    duration=round(seg_dur, 6),
                    file_size=seg_size,
                    start_time=round(float(pts_raw), 6),
                    starts_with_keyframe=True,
                )
            )

        if source_mp4_meta.duration > 0:
            allowed_drift = max(0.5, source_mp4_meta.duration * 0.15)
            if abs(total_duration - source_mp4_meta.duration) > allowed_drift:
                raise ValueError(
                    f"HLS playlist duration ({total_duration:.2f}s) diverges from source MP4 ({source_mp4_meta.duration:.2f}s)."
                )

        avg_bw = max(1, int(round((total_bytes * 8) / max(total_duration, 0.1))))
        peak_bw = max(
            avg_bw,
            max(int(round((s.file_size * 8) / max(s.duration, 0.1))) for s in segments),
        )
        codecs = format_avc_codec_string(h264_profile, h264_level, has_audio=has_audio)

        return HLSRenditionInfo(
            rendition=rendition,
            playlist_path=f"{video_id}/hls/{rendition}/playlist.m3u8",
            playlist_url=f"/api/videos/{video_id}/hls/{rendition}/playlist.m3u8",
            relative_playlist_uri=f"{rendition}/playlist.m3u8",
            width=source_mp4_meta.width,
            height=source_mp4_meta.height,
            resolution=source_mp4_meta.resolution,
            bandwidth=peak_bw,
            average_bandwidth=avg_bw,
            codecs=codecs,
            segment_count=len(segments),
            total_duration=round(total_duration, 6),
            segments=segments,
        )

    def _validate_cross_rendition_alignment(
        self,
        renditions: List[HLSRenditionInfo],
        fps_by_rendition: Dict[str, float],
        staging_dir: Path,
    ) -> Tuple[List[HLSRenditionInfo], List[HLSFailedRenditionInfo]]:
        """Verify segment counts, durations, and actual PTS start times align across renditions."""
        if len(renditions) <= 1:
            return renditions, []

        ref = renditions[0]
        min_fps = min((fps_by_rendition.get(r.rendition, 30.0) for r in renditions), default=30.0)
        # Allow up to 2 frames (or 80ms minimum) tolerance for container mux/timebase rounding
        tolerance = max(0.08, 2.0 / max(min_fps, 1.0))

        aligned: List[HLSRenditionInfo] = [ref]
        misaligned_failures: List[HLSFailedRenditionInfo] = []

        for candidate in renditions[1:]:
            mismatch_reason: Optional[str] = None
            if candidate.segment_count != ref.segment_count:
                mismatch_reason = (
                    f"Segment count {candidate.segment_count} does not match reference "
                    f"{ref.rendition} ({ref.segment_count})."
                )
            else:
                for idx, (ref_seg, cand_seg) in enumerate(zip(ref.segments, candidate.segments)):
                    if abs(cand_seg.duration - ref_seg.duration) > tolerance:
                        mismatch_reason = (
                            f"Segment {idx} duration {cand_seg.duration:.4f}s diverges from "
                            f"{ref.rendition} ({ref_seg.duration:.4f}s) beyond {tolerance:.4f}s tolerance."
                        )
                        break
                    if abs(cand_seg.start_time - ref_seg.start_time) > tolerance:
                        mismatch_reason = (
                            f"Segment {idx} start PTS {cand_seg.start_time:.4f}s diverges from "
                            f"{ref.rendition} ({ref_seg.start_time:.4f}s) beyond {tolerance:.4f}s tolerance."
                        )
                        break

            if mismatch_reason:
                shutil.rmtree(staging_dir / candidate.rendition, ignore_errors=True)
                misaligned_failures.append(
                    HLSFailedRenditionInfo(
                        rendition=candidate.rendition,
                        error=f"Segment alignment validation failed: {mismatch_reason}",
                    )
                )
            else:
                aligned.append(candidate)

        return aligned, misaligned_failures

    def package_hls(
        self,
        output_base_dir: Path,
        video_id: str,
        transcode_record: Optional[TranscodingResponse] = None,
    ) -> Tuple[HLSResponse, bool]:
        """Package available MP4 renditions into synchronized HLS playlists and MPEG-TS segments.

        Returns:
            Tuple of (HLSResponse, promoted_to_disk). `promoted_to_disk` is False if all
            renditions failed and any previous valid HLS output was preserved.
        """
        start_time = time.perf_counter()

        base_resolved = output_base_dir.resolve()
        video_processed_dir = (output_base_dir / video_id).resolve()
        if not video_processed_dir.is_relative_to(base_resolved) or video_processed_dir == base_resolved:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid video ID path.",
            )

        if not video_processed_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No transcoded renditions found for video ID '{video_id}'.",
            )

        # Discover binaries upfront so missing tools return 503 immediately
        ffmpeg_bin = get_ffmpeg_executable(self.config.ffmpeg_path)
        ffprobe_bin = get_ffprobe_executable(self.config.ffprobe_path)

        candidates, skipped_renditions, failed_renditions = self._discover_rendition_sources(
            video_processed_dir,
            transcode_record,
        )

        if not candidates and not skipped_renditions and not failed_renditions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No transcoded MP4 renditions found for video ID '{video_id}'.",
            )

        if not candidates and failed_renditions and not skipped_renditions:
            # All candidate MP4s were missing/unusable before packaging could start
            duration = round(time.perf_counter() - start_time, 3)
            resp = HLSResponse(
                video_id=video_id,
                status="failed",
                overall_status="failed",
                master_playlist_path=None,
                segment_duration=self.config.segment_duration,
                renditions=[],
                skipped_renditions=skipped_renditions,
                failed_renditions=failed_renditions,
                processing_duration=duration,
            )
            return resp, False

        if not candidates and skipped_renditions and not failed_renditions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Video ID '{video_id}' has no usable MP4 renditions (all renditions were skipped).",
            )

        _cleanup_abandoned_dirs(video_processed_dir)
        hls_dir = video_processed_dir / "hls"
        staging_dir = video_processed_dir / f".hls_staging_{uuid.uuid4().hex[:8]}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        successful_renditions: List[HLSRenditionInfo] = []
        fps_by_rendition: Dict[str, float] = {}

        try:
            for rendition, mp4_path in candidates:
                try:
                    mp4_meta = extract_video_metadata(
                        mp4_path,
                        timeout_seconds=self.config.ffprobe_timeout,
                        custom_ffprobe_path=self.config.ffprobe_path,
                    )
                except HTTPException as exc:
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"Source MP4 inspection failed: {exc.detail}",
                        )
                    )
                    continue
                except Exception as exc:
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"Source MP4 inspection failed: {str(exc)}",
                        )
                    )
                    continue

                fps = parse_frame_rate(mp4_meta.frame_rate)
                fps_by_rendition[rendition] = fps
                profile = self.config.rendition_profiles.get(rendition)
                crf = profile.crf if profile else 24
                audio_bitrate = profile.audio_bitrate if profile else "128k"

                staged_r_dir = staging_dir / rendition
                staged_r_dir.mkdir(parents=True, exist_ok=True)
                staged_playlist = staged_r_dir / "playlist.m3u8"
                seg_pattern = staged_r_dir / "segment_%03d.ts"

                cmd = build_hls_ffmpeg_command(
                    ffmpeg_bin=ffmpeg_bin,
                    source_mp4=mp4_path,
                    playlist_path=staged_playlist,
                    segment_pattern=seg_pattern,
                    fps=fps,
                    crf=crf,
                    audio_bitrate=audio_bitrate,
                    config=self.config,
                    has_audio=mp4_meta.audio_codec is not None,
                )

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
                    shutil.rmtree(staged_r_dir, ignore_errors=True)
                    stderr_tail = ""
                    if exc.stderr:
                        decoded = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
                        stderr_tail = f" Details: {decoded.strip()[-300:]}" if decoded.strip() else ""
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"FFmpeg HLS packaging timed out after {self.config.ffmpeg_timeout}s.{stderr_tail}",
                        )
                    )
                    continue
                except Exception as exc:
                    shutil.rmtree(staged_r_dir, ignore_errors=True)
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"Failed to execute FFmpeg HLS process: {str(exc)}",
                        )
                    )
                    continue

                if proc.returncode != 0:
                    shutil.rmtree(staged_r_dir, ignore_errors=True)
                    err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"FFmpeg HLS error: {err_msg}",
                        )
                    )
                    continue

                try:
                    rendition_info = self._validate_rendition_hls(
                        video_id=video_id,
                        rendition=rendition,
                        staged_rendition_dir=staged_r_dir,
                        source_mp4_meta=mp4_meta,
                        ffprobe_bin=ffprobe_bin,
                    )
                except Exception as val_exc:
                    shutil.rmtree(staged_r_dir, ignore_errors=True)
                    failed_renditions.append(
                        HLSFailedRenditionInfo(
                            rendition=rendition,
                            error=f"HLS validation failed: {str(val_exc)}",
                        )
                    )
                    continue

                successful_renditions.append(rendition_info)

            # Verify cross-rendition boundary alignment across all successful renditions
            successful_renditions, align_failures = self._validate_cross_rendition_alignment(
                successful_renditions,
                fps_by_rendition,
                staging_dir,
            )
            failed_renditions.extend(align_failures)

            if not successful_renditions:
                duration = round(time.perf_counter() - start_time, 3)
                resp = HLSResponse(
                    video_id=video_id,
                    status="failed",
                    overall_status="failed",
                    master_playlist_path=None,
                    segment_duration=self.config.segment_duration,
                    renditions=[],
                    skipped_renditions=skipped_renditions,
                    failed_renditions=failed_renditions,
                    processing_duration=duration,
                )
                return resp, False

            # Write master.m3u8 inside staging from validated renditions only
            staged_master = staging_dir / "master.m3u8"
            staged_master.write_text(build_master_playlist_content(successful_renditions), encoding="utf-8")

            overall_status = "partial" if failed_renditions else "completed"
            duration = round(time.perf_counter() - start_time, 3)
            response = HLSResponse(
                video_id=video_id,
                status=overall_status,
                overall_status=overall_status,
                master_playlist_path=f"{video_id}/hls/master.m3u8",
                master_playlist_url=f"/api/videos/{video_id}/hls/master.m3u8",
                segment_duration=self.config.segment_duration,
                renditions=successful_renditions,
                skipped_renditions=skipped_renditions,
                failed_renditions=failed_renditions,
                processing_duration=duration,
            )

            # Write hls_status.json inside staging prior to atomic swap
            staged_status = staging_dir / "hls_status.json"
            staged_status.write_text(response.model_dump_json(indent=2), encoding="utf-8")

            _promote_staging_to_hls(staging_dir, hls_dir)
            return response, True

        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
