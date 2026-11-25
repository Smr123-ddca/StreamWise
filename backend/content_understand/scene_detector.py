"""Scene transition detection using OpenCV histogram comparison and PySceneDetect."""

import logging
from pathlib import Path
from typing import List, Optional, Sequence
import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _load_grayscale(image_path: Path) -> np.ndarray:
    """Load an image in grayscale, raising an explicit error if missing or unreadable."""
    if not image_path.is_file():
        raise FileNotFoundError(f"Frame image not found: {image_path}")
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        raise ValueError(f"Failed to decode image file: {image_path}")
    return img


def compute_histogram_distance(frame1_path: Path, frame2_path: Path) -> float:
    """Compute normalized Bhattacharyya histogram distance [0.0, 1.0] between two frames."""
    img1 = _load_grayscale(frame1_path)
    img2 = _load_grayscale(frame2_path)

    hist1 = cv2.calcHist([img1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([img2], [0], None, [256], [0, 256])

    # Normalize histograms so comparisons remain resolution-independent and bounded
    cv2.normalize(hist1, hist1, alpha=1.0, beta=0.0, norm_type=cv2.NORM_L1)
    cv2.normalize(hist2, hist2, alpha=1.0, beta=0.0, norm_type=cv2.NORM_L1)

    distance = float(cv2.compareHist(hist1, hist2, cv2.HISTCMP_BHATTACHARYYA))
    return max(0.0, min(distance, 1.0))


def detect_scene_change(
    frame1_path: Path,
    frame2_path: Path,
    threshold: float = 0.40,
) -> bool:
    """Return True if histogram difference between two frames exceeds threshold."""
    return compute_histogram_distance(frame1_path, frame2_path) > threshold


def detect_segment_scene_change(
    frame_paths: Sequence[Path],
    prev_segment_last_frame: Optional[Path] = None,
    threshold: float = 0.40,
) -> bool:
    """Detect whether a scene transition occurs entering or inside a segment."""
    if not frame_paths:
        return False

    if prev_segment_last_frame is not None and prev_segment_last_frame.is_file():
        dist = compute_histogram_distance(prev_segment_last_frame, frame_paths[0])
        if dist > threshold:
            logger.debug("Inter-segment scene change detected (distance=%.4f > %.4f).", dist, threshold)
            return True

    for idx in range(len(frame_paths) - 1):
        dist = compute_histogram_distance(frame_paths[idx], frame_paths[idx + 1])
        if dist > threshold:
            logger.debug("Intra-segment scene change detected (distance=%.4f > %.4f).", dist, threshold)
            return True

    return False


def get_scene_changes(
    video_path: Path,
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> List[float]:
    """Detect scene change timestamps (seconds) in a video using PySceneDetect."""
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    from scenedetect import ContentDetector, detect

    scene_list = detect(
        str(video_path),
        ContentDetector(threshold=threshold, min_scene_len=min_scene_len),
    )
    # First scene starts at 0.0; subsequent scenes mark transition timestamps
    timestamps: List[float] = []
    for scene in scene_list[1:]:
        tc = scene[0]
        sec = tc.seconds if hasattr(tc, "seconds") else tc.get_seconds()
        timestamps.append(round(float(sec), 4))
    return timestamps
