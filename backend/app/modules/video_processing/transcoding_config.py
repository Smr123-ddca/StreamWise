"""Centralized configuration for multi-resolution video transcoding.

This module defines the encoding parameters, quality profiles, and dimension
calculation logic for the StreamWise transcoding engine.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


def to_even(n: float, max_bound: int = 0) -> int:
    """Round a number to the nearest even integer, optionally capped by max_bound.

    H.264 macroblocks and standard YUV420p chroma subsampling require both
    width and height to be even numbers (divisible by 2).
    """
    val = int(round(n))
    if val % 2 != 0:
        if max_bound > 0 and val + 1 > max_bound:
            val = val - 1
        else:
            val = val + 1
    return max(2, val)


@dataclass(frozen=True)
class RenditionProfile:
    """Configuration for a standardized video rendition quality tier.

    Attributes:
        name: Rendition identifier (e.g. '360p', '480p', '720p').
        target_height: Nominal target height in pixels (360, 480, 720).
        max_width: Maximum permitted width in pixels (640, 854, 1280).
        max_height: Maximum permitted height in pixels (360, 480, 720).
        crf: Constant Rate Factor for x264 (lower = higher quality, 0-51).
        audio_bitrate: Target AAC audio bitrate (e.g. '96k', '128k').
        video_bitrate_max: Optional VBV maxrate limit.
        bufsize: Optional VBV buffer size.
    """

    name: str
    target_height: int
    max_width: int
    max_height: int
    crf: int
    audio_bitrate: str = "128k"
    video_bitrate_max: Optional[str] = None
    bufsize: Optional[str] = None


# Standard Video Rendition Ladder:
# - 360p (max 640x360): CRF 26, audio 96k. Efficient baseline for bandwidth-constrained streaming.
# - 480p (max 854x480): CRF 24, audio 128k. Standard definition balance between fidelity and size.
# - 720p (max 1280x720): CRF 22, audio 128k. High-definition tier preserving sharp detail.
DEFAULT_RENDITION_PROFILES: List[RenditionProfile] = [
    RenditionProfile(
        name="360p",
        target_height=360,
        max_width=640,
        max_height=360,
        crf=26,
        audio_bitrate="96k",
    ),
    RenditionProfile(
        name="480p",
        target_height=480,
        max_width=854,
        max_height=480,
        crf=24,
        audio_bitrate="128k",
    ),
    RenditionProfile(
        name="720p",
        target_height=720,
        max_width=1280,
        max_height=720,
        crf=22,
        audio_bitrate="128k",
    ),
]


@dataclass
class TranscodingConfig:
    """Global configuration for the FFmpeg transcoding engine.

    Attributes:
        video_codec: FFmpeg video encoder ('libx264').
        audio_codec: FFmpeg audio encoder ('aac').
        preset: x264 encoding speed preset ('fast', 'medium', etc.).
        pixel_format: Pixel format for universal compatibility ('yuv420p').
        ffmpeg_timeout: Timeout in seconds for an individual transcode process.
        rendition_profiles: Ordered list of rendition profiles to generate.
        ffmpeg_path: Explicit path to ffmpeg binary or None for auto-discovery.
        ffprobe_path: Explicit path to ffprobe binary or None for auto-discovery.
    """

    video_codec: str = "libx264"
    audio_codec: str = "aac"
    preset: str = "fast"
    pixel_format: str = "yuv420p"
    ffmpeg_timeout: int = 120
    threads: Optional[int] = None
    rendition_profiles: List[RenditionProfile] = field(
        default_factory=lambda: list(DEFAULT_RENDITION_PROFILES)
    )
    ffmpeg_path: Optional[str] = None
    ffprobe_path: Optional[str] = None


def calculate_rendition_dimensions(
    src_width: int,
    src_height: int,
    profile: RenditionProfile,
) -> Tuple[Optional[Tuple[int, int]], Optional[str]]:
    """Compute target dimensions for a rendition preserving aspect ratio without upscaling.

    Rules enforced:
    1. Aspect Ratio Preservation: Scaled dimensions must maintain the source aspect ratio.
    2. No Upscaling: Neither target width nor target height may exceed source dimensions.
       If source height is smaller than target height, the rendition is omitted.
    3. Maximum Resolution Bounds: Output width <= profile.max_width, height <= profile.max_height.
    4. Even Dimensions: Output dimensions are rounded to even numbers suitable for H.264.

    Args:
        src_width: Source video width in pixels.
        src_height: Source video height in pixels.
        profile: The rendition profile being evaluated.

    Returns:
        Tuple of ((width, height), None) if the rendition can be safely produced.
        Tuple of (None, reason) if the rendition cannot be produced without upscaling.
    """
    if src_width <= 0 or src_height <= 0:
        return None, f"Invalid source dimensions: {src_width}x{src_height}."

    # Prevent upscaling if source height is smaller than nominal target height
    if src_height < profile.target_height:
        return (
            None,
            f"Source height ({src_height}p) is smaller than target rendition height ({profile.target_height}p) - upscaling prohibited.",
        )

    # Primary scaling attempt: anchor to target_height
    scale = profile.target_height / float(src_height)
    cand_height = profile.target_height
    cand_width = to_even(src_width * scale, max_bound=profile.max_width)

    # If candidate width exceeds maximum allowed profile width (e.g. ultrawide aspect ratios)
    if cand_width > profile.max_width:
        cand_width = profile.max_width
        cand_height = to_even(src_height * (profile.max_width / float(src_width)), max_bound=profile.max_height)

    # Check that candidate dimensions do not upscale beyond source dimensions
    if cand_width > src_width or cand_height > src_height:
        return (
            None,
            f"Computed resolution {cand_width}x{cand_height} exceeds source dimensions {src_width}x{src_height} - upscaling prohibited.",
        )

    # Check bounds against maximum resolution profile
    if cand_width > profile.max_width or cand_height > profile.max_height:
        return (
            None,
            f"Computed resolution {cand_width}x{cand_height} exceeds profile maximum {profile.max_width}x{profile.max_height}.",
        )

    return (cand_width, cand_height), None
