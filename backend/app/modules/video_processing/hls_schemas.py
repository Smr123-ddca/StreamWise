"""Pydantic schemas for HLS packaging requests, segment validation, and status responses."""

from typing import List, Optional
from pydantic import BaseModel, Field


class HLSSegmentInfo(BaseModel):
    """Metadata and keyframe validation details for a single HLS segment."""

    filename: str = Field(..., description="Segment filename (e.g. 'segment_000.ts')")
    duration: float = Field(..., description="Segment duration from #EXTINF in seconds")
    file_size: int = Field(..., description="Segment file size in bytes")
    start_time: float = Field(..., description="Presentation timestamp (PTS) of the first video frame")
    starts_with_keyframe: bool = Field(..., description="Whether the segment begins with a valid keyframe")


class HLSRenditionInfo(BaseModel):
    """Details of a successfully packaged and validated HLS rendition."""

    rendition: str = Field(..., description="Rendition tier name (e.g. '360p', '480p', '720p')")
    playlist_path: str = Field(..., description="Storage-relative path to the rendition media playlist")
    playlist_url: Optional[str] = Field(None, description="HTTP playback URL for the rendition playlist")
    relative_playlist_uri: str = Field(..., description="Playlist path relative to master.m3u8")
    width: int = Field(..., description="Video width in pixels")
    height: int = Field(..., description="Video height in pixels")
    resolution: str = Field(..., description="Resolution string formatted as {width}x{height}")
    bandwidth: int = Field(..., description="Peak segment bitrate in bits/s")
    average_bandwidth: int = Field(..., description="Average bitrate across segments in bits/s")
    codecs: str = Field(..., description="RFC 6381 CODECS string for master playlist")
    segment_count: int = Field(..., description="Number of media segments generated")
    total_duration: float = Field(..., description="Total playlist duration in seconds")
    segments: List[HLSSegmentInfo] = Field(default_factory=list, description="Validated media segments")


class HLSSkippedRenditionInfo(BaseModel):
    """Details of a rendition skipped before HLS packaging."""

    rendition: str = Field(..., description="Name of skipped rendition")
    reason: str = Field(..., description="Reason the rendition was omitted")


class HLSFailedRenditionInfo(BaseModel):
    """Details of a rendition that failed HLS packaging or validation."""

    rendition: str = Field(..., description="Name of failed rendition")
    error: str = Field(..., description="Diagnostic error message")


class HLSResponse(BaseModel):
    """Structured response payload for HLS packaging and status retrieval."""

    video_id: str = Field(..., description="UUID of the packaged video")
    status: str = Field(
        ...,
        description="Overall HLS status: 'completed', 'partial', 'failed', or 'processing'",
    )
    overall_status: str = Field(..., description="Overall status identifier matching status")
    master_playlist_path: Optional[str] = Field(
        None,
        description="Storage-relative path to master.m3u8 when at least one rendition succeeds",
    )
    master_playlist_url: Optional[str] = Field(
        None,
        description="HTTP playback URL for master.m3u8 when at least one rendition succeeds",
    )
    segment_duration: int = Field(..., description="Target HLS segment duration in seconds")
    renditions: List[HLSRenditionInfo] = Field(
        default_factory=list,
        description="Successfully packaged and validated HLS renditions",
    )
    skipped_renditions: List[HLSSkippedRenditionInfo] = Field(
        default_factory=list,
        description="Renditions omitted (e.g. skipped during MP4 transcoding)",
    )
    failed_renditions: List[HLSFailedRenditionInfo] = Field(
        default_factory=list,
        description="Renditions that failed HLS segmentation or alignment validation",
    )
    processing_duration: float = Field(..., description="Total HLS packaging time in seconds")
