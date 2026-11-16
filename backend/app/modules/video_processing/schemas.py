"""Pydantic schemas for video processing, validation, and metadata."""

from typing import Optional
from pydantic import BaseModel, Field


class VideoMetadata(BaseModel):
    """Extracted technical metadata from a media file."""

    duration: float = Field(..., description="Duration of video in seconds")
    width: int = Field(..., description="Width of video in pixels")
    height: int = Field(..., description="Height of video in pixels")
    resolution: str = Field(..., description="Resolution formatted as {width}x{height}")
    frame_rate: str = Field(..., description="Video stream frame rate representation (e.g. 30/1)")
    video_codec: str = Field(..., description="Video codec name (e.g. h264, vp9, hevc)")
    audio_codec: Optional[str] = Field(None, description="Audio codec name if present, else None")
    container: str = Field(..., description="Container format names")
    bitrate: Optional[int] = Field(None, description="Overall or video stream bitrate in bits/s")
    file_size: int = Field(..., description="File size in bytes")


class VideoUploadResponse(BaseModel):
    """Response payload returned upon successful video upload and validation."""

    video_id: str = Field(..., description="Unique UUID for the uploaded video")
    original_filename: str = Field(..., description="Original filename preserved from client upload")
    status: str = Field(default="uploaded", description="Current status of the video record")
    upload_size: int = Field(..., description="Size of uploaded video in bytes")
    metadata: VideoMetadata = Field(..., description="Extracted video metadata")
    created_at: str = Field(..., description="ISO-8601 formatted creation timestamp")
