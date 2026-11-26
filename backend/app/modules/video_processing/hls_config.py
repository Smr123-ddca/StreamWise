"""Centralized configuration for HLS (HTTP Live Streaming) packaging.

Defines segment duration, GOP/keyframe alignment helpers, MPEG-TS muxing
options, and RFC 6381 codec string formatting for master playlists.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from .transcoding_config import DEFAULT_RENDITION_PROFILES, RenditionProfile


def parse_frame_rate(frame_rate_str: str) -> float:
    """Parse an FFprobe frame rate string (e.g. '30/1', '24000/1001', '29.97') to float."""
    if not frame_rate_str:
        return 30.0
    raw = frame_rate_str.strip()
    try:
        if "/" in raw:
            num_str, den_str = raw.split("/", 1)
            num = float(num_str)
            den = float(den_str)
            if den > 0 and num > 0:
                return num / den
        else:
            val = float(raw)
            if val > 0:
                return val
    except (ValueError, ZeroDivisionError):
        pass
    return 30.0


def calculate_gop_frames(fps: float, segment_duration: int) -> int:
    """Calculate a fixed GOP size in frames aligned with the target segment duration.

    Uses math.ceil so that for fractional frame rates (e.g. 23.976 or 29.97 fps),
    the GOP upper bound never triggers a keyframe one frame before `-force_key_frames`.
    """
    safe_fps = fps if fps > 0 else 30.0
    safe_dur = max(1, segment_duration)
    return max(1, math.ceil(safe_fps * safe_dur))


def format_avc_codec_string(
    profile: Optional[str],
    level: Optional[int],
    has_audio: bool,
) -> str:
    """Format an RFC 6381 CODECS attribute string from H.264 profile/level metadata."""
    profile_map = {
        "constrained baseline": "42e0",
        "baseline": "42e0",
        "main": "4d40",
        "high": "6400",
        "high 10": "6e00",
    }
    normalized = (profile or "high").strip().lower()
    profile_hex = profile_map.get(normalized, "6400")

    try:
        lvl_int = int(level) if level and int(level) > 0 else 31
    except (ValueError, TypeError):
        lvl_int = 31
    level_hex = f"{lvl_int:02x}"

    codecs = f"avc1.{profile_hex}{level_hex}"
    if has_audio:
        codecs += ",mp4a.40.2"
    return codecs


@dataclass
class HLSConfig:
    """Configuration for the FFmpeg HLS segmentation and packaging engine."""

    segment_duration: int = 4
    segment_type: str = "mpegts"
    playlist_type: str = "vod"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    preset: str = "fast"
    pixel_format: str = "yuv420p"
    ffmpeg_timeout: int = 180
    ffprobe_timeout: int = 15
    threads: Optional[int] = None
    ffmpeg_path: Optional[str] = None
    ffprobe_path: Optional[str] = None
    rendition_profiles: Dict[str, RenditionProfile] = field(
        default_factory=lambda: {p.name: p for p in DEFAULT_RENDITION_PROFILES}
    )
