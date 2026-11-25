"""Configurable thresholds, weights, and runtime settings for Content Understanding."""

import os
from pathlib import Path
from typing import Dict, Literal, Optional
from pydantic import BaseModel, Field, model_validator


class ImportanceWeights(BaseModel):
    """Weights used to combine content signals into an importance score."""

    visual_complexity: float = Field(default=0.30, ge=0.0, le=1.0)
    text_density: float = Field(default=0.40, ge=0.0, le=1.0)
    scene_change_bonus: float = Field(default=0.20, ge=0.0, le=1.0)
    text_flag_bonus: float = Field(default=0.10, ge=0.0, le=1.0)

    def as_dict(self) -> Dict[str, float]:
        """Return weights as a plain dictionary."""
        return {
            "visual_complexity": self.visual_complexity,
            "text_density": self.text_density,
            "scene_change_bonus": self.scene_change_bonus,
            "text_flag_bonus": self.text_flag_bonus,
        }


class AnalysisConfig(BaseModel):
    """Central configuration for all thresholds, paths, and pipeline parameters."""

    analyzer_version: str = Field(default="0.1.0")

    # Storage paths
    storage_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("STREAMWISE_STORAGE_DIR", "storage"))
    )
    processed_subdir: str = Field(default="processed")
    segments_subdir: str = Field(default="segments")
    metadata_filename: str = Field(default="content_metadata.json")

    # Executable paths (optional overrides; auto-discovered if None)
    ffmpeg_path: Optional[str] = Field(
        default_factory=lambda: os.getenv("FFMPEG_PATH") or None
    )
    ffprobe_path: Optional[str] = Field(
        default_factory=lambda: os.getenv("FFPROBE_PATH") or None
    )
    tesseract_cmd: Optional[str] = Field(
        default_factory=lambda: os.getenv("TESSERACT_CMD") or None
    )

    # Frame extraction
    frames_per_segment: int = Field(default=3, ge=1, le=30)
    frame_image_format: Literal["jpg", "png"] = Field(default="jpg")
    ffmpeg_timeout_sec: int = Field(default=30, ge=1, le=300)
    ffprobe_timeout_sec: int = Field(default=15, ge=1, le=120)
    default_segment_duration_sec: float = Field(default=6.0, gt=0.0)

    # Scene detection
    scene_change_threshold: float = Field(default=0.40, gt=0.0, le=1.0)
    scenedetect_threshold: float = Field(default=27.0, gt=0.0)

    # Visual complexity (Canny edges)
    blur_kernel_size: int = Field(default=5, ge=1)
    canny_threshold1: int = Field(default=50, ge=0)
    canny_threshold2: int = Field(default=150, ge=0)
    complexity_normalization_factor: float = Field(default=5.0, gt=0.0)

    # OCR and text density
    ocr_confidence_threshold: int = Field(default=60, ge=0, le=100)
    text_density_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    significant_word_count_threshold: int = Field(default=10, ge=0)
    text_sample_max_chars: int = Field(default=120, ge=10, le=1000)
    ocr_fallback_mode: Literal["opencv_text_region", "none", "strict"] = Field(
        default="opencv_text_region"
    )

    # Content type hint thresholds
    slide_text_density_threshold: float = Field(default=0.12, ge=0.0, le=1.0)
    diagram_complexity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)

    # Pipeline resource limits
    max_segments: int = Field(default=500, ge=1)
    analysis_timeout_sec: float = Field(default=600.0, gt=0.0)

    # Importance scoring weights
    weights: ImportanceWeights = Field(default_factory=ImportanceWeights)

    @model_validator(mode="after")
    def validate_kernel_and_canny(self) -> "AnalysisConfig":
        """Ensure blur kernel size is odd/positive and Canny thresholds are ordered."""
        if self.blur_kernel_size % 2 == 0:
            raise ValueError("blur_kernel_size must be an odd positive integer.")
        if self.canny_threshold1 > self.canny_threshold2:
            raise ValueError("canny_threshold1 must be less than or equal to canny_threshold2.")
        return self

    def resolve_processed_root(self) -> Path:
        """Resolve the processed storage directory across common working directories."""
        base = self.storage_dir
        if base.is_absolute():
            return base / self.processed_subdir

        cwd_candidate = (Path.cwd() / base).resolve()
        if cwd_candidate.exists():
            return cwd_candidate / self.processed_subdir

        module_dir = Path(__file__).resolve().parent
        backend_dir = module_dir.parent
        repo_root = backend_dir.parent
        for root in (repo_root, backend_dir):
            candidate = (root / base).resolve()
            if candidate.exists():
                return candidate / self.processed_subdir

        default_root = backend_dir if str(base).startswith("..") else repo_root
        return (default_root / base / self.processed_subdir).resolve()


def get_analysis_config() -> AnalysisConfig:
    """Return a fresh AnalysisConfig instance reflecting current environment settings."""
    return AnalysisConfig()
