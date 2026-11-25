"""Visual complexity estimation using Gaussian smoothing and Canny edge density."""

import logging
from pathlib import Path
from typing import Sequence
import cv2
import numpy as np

logger = logging.getLogger(__name__)


def calculate_visual_complexity(
    image_path: Path,
    blur_kernel_size: int = 5,
    canny_threshold1: int = 50,
    canny_threshold2: int = 150,
    normalization_factor: float = 5.0,
) -> float:
    """Return normalized edge-based visual complexity in [0.0, 1.0].

    Higher scores correspond to denser edges (e.g., code, diagrams, dense slides).
    """
    if not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    if blur_kernel_size < 1 or blur_kernel_size % 2 == 0:
        raise ValueError("blur_kernel_size must be an odd positive integer.")
    if canny_threshold1 < 0 or canny_threshold2 < 0 or canny_threshold1 > canny_threshold2:
        raise ValueError("Canny thresholds must be non-negative with threshold1 <= threshold2.")
    if normalization_factor <= 0.0:
        raise ValueError("normalization_factor must be greater than 0.")

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        raise ValueError(f"Failed to decode image file: {image_path}")

    blurred = cv2.GaussianBlur(img, (blur_kernel_size, blur_kernel_size), 0)
    edges = cv2.Canny(blurred, threshold1=canny_threshold1, threshold2=canny_threshold2)

    edge_density = float(np.count_nonzero(edges)) / float(edges.size)
    normalized = min(edge_density * normalization_factor, 1.0)
    return round(normalized, 4)


def analyze_frames_complexity(
    frame_paths: Sequence[Path],
    blur_kernel_size: int = 5,
    canny_threshold1: int = 50,
    canny_threshold2: int = 150,
    normalization_factor: float = 5.0,
) -> float:
    """Compute mean visual complexity across a non-empty sequence of frames."""
    if not frame_paths:
        raise ValueError("frame_paths must contain at least one frame.")

    scores = [
        calculate_visual_complexity(
            path,
            blur_kernel_size=blur_kernel_size,
            canny_threshold1=canny_threshold1,
            canny_threshold2=canny_threshold2,
            normalization_factor=normalization_factor,
        )
        for path in frame_paths
    ]
    mean_score = round(sum(scores) / len(scores), 4)
    logger.debug("Computed mean visual complexity %.4f across %d frame(s).", mean_score, len(scores))
    return mean_score
