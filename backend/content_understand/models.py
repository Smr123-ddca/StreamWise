"""Pydantic schemas defining the Content Understanding metadata contract."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, field_serializer


class SegmentInput(BaseModel):
    """Input descriptor for a single video segment to analyze."""

    segment_id: str = Field(..., description="Unique segment identifier (e.g., seg_001)")
    segment_index: int = Field(..., ge=0, description="Zero-based segment order index")
    file_path: Path = Field(..., description="Filesystem path to the segment media file")
    start_time_sec: float = Field(..., ge=0.0, description="Segment start timestamp in seconds")
    end_time_sec: float = Field(..., ge=0.0, description="Segment end timestamp in seconds")
    duration_sec: float = Field(..., ge=0.0, description="Segment duration in seconds")

    @field_serializer("file_path")
    def serialize_file_path(self, value: Path) -> str:
        """Serialize filesystem path as string for JSON compatibility."""
        return str(value)


class SegmentAnalysis(BaseModel):
    """Computed visual and textual metrics for a single video segment."""

    visual_complexity: float = Field(
        ..., ge=0.0, le=1.0, description="Normalized edge-based visual complexity [0.0, 1.0]"
    )
    text_density: float = Field(
        ..., ge=0.0, le=1.0, description="Ratio of frame area covered by text [0.0, 1.0]"
    )
    scene_change_detected: bool = Field(
        ..., description="Whether a visual scene transition was detected for this segment"
    )
    detected_text_sample: str = Field(
        default="", description="Sample of recognized text from representative frames"
    )
    word_count: int = Field(
        default=0, ge=0, description="Count of recognized words above confidence threshold"
    )
    has_significant_text: bool = Field(
        ..., description="True if text density or word count exceeds configured thresholds"
    )
    importance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Normalized contextual importance score [0.0, 1.0]"
    )
    content_type_hint: str = Field(
        default="general",
        description="Informational classification hint (e.g., code_screen, presentation_slide)",
    )
    ocr_engine: str = Field(
        default="tesseract",
        description="OCR/text-density backend used ('tesseract', 'opencv_text_region', 'mixed', or 'none')",
    )


class SegmentMetadata(BaseModel):
    """Full metadata record for an analyzed segment, aligned with the Decision Engine contract."""

    video_id: str = Field(..., description="Unique identifier of the parent video")
    segment_id: str = Field(..., description="Stable segment identifier (e.g., seg_000)")
    segment_index: int = Field(..., ge=0, description="Zero-based segment sequence index")
    start_time_sec: float = Field(..., ge=0.0, description="Start time in seconds")
    end_time_sec: float = Field(..., ge=0.0, description="End time in seconds")
    duration_sec: float = Field(..., ge=0.0, description="Duration in seconds")
    analysis: SegmentAnalysis = Field(..., description="Computed content analysis signals")
    frames_analyzed: int = Field(..., ge=0, description="Number of frames analyzed for this segment")
    analysis_timestamp: str = Field(..., description="UTC ISO-8601 timestamp of analysis completion")
    analyzer_version: str = Field(default="0.1.0", description="Content analyzer version")


class VideoAnalysisResult(BaseModel):
    """Top-level analysis output for an entire segmented video."""

    video_id: str = Field(..., description="Unique video identifier")
    total_segments: int = Field(..., ge=0, description="Total number of analyzed segments")
    analysis_version: str = Field(default="0.1.0", description="Analyzer schema/pipeline version")
    segments: List[SegmentMetadata] = Field(
        default_factory=list, description="Ordered list of per-segment metadata objects"
    )


class AnalyzeVideoRequest(BaseModel):
    """Optional request body for POST /analysis/analyze/{video_id}."""

    segments_dir: Optional[str] = Field(
        default=None, description="Optional override directory containing .ts/.mp4 segments"
    )
    output_path: Optional[str] = Field(
        default=None, description="Optional override path for saving content_metadata.json"
    )
    frames_per_segment: Optional[int] = Field(
        default=None, ge=1, le=30, description="Optional override for frames extracted per segment"
    )
