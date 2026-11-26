"""Pydantic schemas for multi-resolution video transcoding requests and responses."""

from typing import List, Optional
from pydantic import BaseModel, Field


class RenditionInfo(BaseModel):
    """Details of a successfully transcoded video rendition."""

    rendition: str = Field(..., description="Name of rendition (e.g. '360p', '480p', '720p')")
    width: int = Field(..., description="Width of transcoded video in pixels")
    height: int = Field(..., description="Height of transcoded video in pixels")
    resolution: str = Field(..., description="Resolution formatted as {width}x{height}")
    file_path: str = Field(..., description="Filesystem path to transcoded video file")
    file_size: int = Field(..., description="Size of transcoded output in bytes")
    duration: float = Field(..., description="Duration of video in seconds")
    video_codec: str = Field(..., description="Video codec name (e.g. 'h264')")
    audio_codec: Optional[str] = Field(None, description="Audio codec name if present, else None")
    bitrate: Optional[int] = Field(None, description="Bitrate in bits/s")


class SkippedRenditionInfo(BaseModel):
    """Details of a skipped/omitted rendition and reason."""

    rendition: str = Field(..., description="Name of skipped rendition (e.g. '720p')")
    reason: str = Field(..., description="Reason why the rendition was omitted (e.g. upscaling prevention)")


class FailedRenditionInfo(BaseModel):
    """Details of a failed rendition and error."""

    rendition: str = Field(..., description="Name of failed rendition")
    error: str = Field(..., description="Error message or diagnostic details")


class TranscodingResponse(BaseModel):
    """Response payload representing the multi-resolution transcoding results."""

    video_id: str = Field(..., description="Unique UUID of the source video")
    status: str = Field(
        ...,
        description="Overall transcoding status: 'completed', 'partial', 'skipped', or 'failed'",
    )
    overall_status: str = Field(
        ...,
        description="Overall status identifier matching status",
    )
    successful_renditions: List[RenditionInfo] = Field(
        default_factory=list,
        description="List of successfully generated renditions",
    )
    skipped_renditions: List[SkippedRenditionInfo] = Field(
        default_factory=list,
        description="List of renditions skipped to prevent upscaling or invalid constraints",
    )
    failed_renditions: List[FailedRenditionInfo] = Field(
        default_factory=list,
        description="List of renditions that encountered errors during processing",
    )
    processing_duration: float = Field(
        ...,
        description="Processing duration in seconds",
    )
