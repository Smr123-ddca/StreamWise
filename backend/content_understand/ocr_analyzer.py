"""OCR and text-density analysis using Tesseract with OpenCV fallback."""

import logging
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Dict, List, Optional, Sequence
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)
_TESSERACT_LOCK = threading.Lock()


def find_tesseract_executable(custom_path: Optional[str] = None) -> Optional[str]:
    """Locate the Tesseract OCR binary if installed on the host."""
    override = custom_path or os.getenv("TESSERACT_CMD")
    if override:
        override_path = Path(override)
        if override_path.is_file():
            return str(override_path)
        resolved_override = shutil.which(override)
        if resolved_override:
            return resolved_override

    resolved = shutil.which("tesseract")
    if resolved:
        return resolved

    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_app_data = os.getenv("LOCALAPPDATA", "")

    candidates = [
        Path(program_files) / "Tesseract-OCR" / "tesseract.exe",
        Path(program_files_x86) / "Tesseract-OCR" / "tesseract.exe",
    ]
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Tesseract-OCR" / "tesseract.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


def is_tesseract_available(custom_path: Optional[str] = None) -> bool:
    """Return True if both pytesseract and the tesseract binary are usable."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return find_tesseract_executable(custom_path) is not None


def _load_bgr_image(image_path: Path) -> np.ndarray:
    """Load an image in BGR color format with explicit validation."""
    if not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    img = cv2.imread(str(image_path))
    if img is None or img.size == 0:
        raise ValueError(f"Failed to decode image file: {image_path}")
    return img


def _analyze_with_tesseract(
    img: np.ndarray,
    tesseract_cmd: str,
    confidence_threshold: int,
    text_density_threshold: float,
    word_count_threshold: int,
) -> Dict[str, Any]:
    """Run pytesseract bounding-box OCR and compute non-overlapping text coverage."""
    import pytesseract

    pil_image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    with _TESSERACT_LOCK:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        data = pytesseract.image_to_data(pil_image, output_type=pytesseract.Output.DICT)

    height, width = img.shape[:2]
    coverage_mask = np.zeros((height, width), dtype=np.uint8)
    words: List[str] = []

    for idx, raw_text in enumerate(data.get("text", [])):
        text = (raw_text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][idx])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < confidence_threshold:
            continue

        x = max(0, int(data["left"][idx]))
        y = max(0, int(data["top"][idx]))
        box_w = max(0, int(data["width"][idx]))
        box_h = max(0, int(data["height"][idx]))
        x2 = min(width, x + box_w)
        y2 = min(height, y + box_h)
        if x2 > x and y2 > y:
            coverage_mask[y:y2, x:x2] = 1
            words.append(text)

    total_pixels = float(height * width)
    text_density = round(min(float(np.count_nonzero(coverage_mask)) / total_pixels, 1.0), 4)
    word_count = len(words)

    return {
        "detected_text": " ".join(words),
        "text_density": text_density,
        "word_count": word_count,
        "has_significant_text": (
            text_density > text_density_threshold or word_count > word_count_threshold
        ),
        "ocr_engine": "tesseract",
    }


def _analyze_with_opencv_text_regions(
    img: np.ndarray,
    text_density_threshold: float,
) -> Dict[str, Any]:
    """Estimate text-region coverage using OpenCV morphological gradient when Tesseract is absent.

    Measures pixel area of high-contrast horizontal stroke clusters without fabricating
    recognized text strings.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]

    # Top-hat and black-hat capture both light-on-dark (code) and dark-on-light (slides) text
    stroke_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 3))
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, stroke_kernel)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, stroke_kernel)
    combined = cv2.max(tophat, blackhat)

    # Require minimum contrast so uniform/near-blank frames do not trigger Otsu noise
    if float(np.std(combined)) < 4.0:
        return {
            "detected_text": "",
            "text_density": 0.0,
            "word_count": 0,
            "has_significant_text": False,
            "ocr_engine": "opencv_text_region",
        }

    grad_x = cv2.Sobel(combined, cv2.CV_32F, 1, 0, ksize=3)
    grad_abs = cv2.convertScaleAbs(grad_x)

    _, binary = cv2.threshold(grad_abs, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    coverage_mask = np.zeros((height, width), dtype=np.uint8)
    max_box_h = max(int(height * 0.30), 24)
    max_box_w = int(width * 0.92)

    for contour in contours:
        x, y, box_w, box_h = cv2.boundingRect(contour)
        if box_h < 6 or box_w < 8:
            continue
        if box_h > max_box_h or box_w > max_box_w:
            continue
        if box_w * box_h < 48:
            continue

        roi = binary[y : y + box_h, x : x + box_w]
        fill_ratio = float(np.count_nonzero(roi)) / float(roi.size)
        if fill_ratio < 0.12:
            continue

        coverage_mask[y : y + box_h, x : x + box_w] = 1

    total_pixels = float(height * width)
    text_density = round(min(float(np.count_nonzero(coverage_mask)) / total_pixels, 1.0), 4)

    return {
        "detected_text": "",
        "text_density": text_density,
        "word_count": 0,
        "has_significant_text": text_density > text_density_threshold,
        "ocr_engine": "opencv_text_region",
    }


def analyze_text_density(
    image_path: Path,
    tesseract_cmd: Optional[str] = None,
    confidence_threshold: int = 60,
    text_density_threshold: float = 0.05,
    word_count_threshold: int = 10,
    fallback_mode: str = "opencv_text_region",
) -> Dict[str, Any]:
    """Analyze a single frame for OCR text and text coverage density."""
    img = _load_bgr_image(image_path)

    resolved_tesseract = find_tesseract_executable(tesseract_cmd)
    if resolved_tesseract is not None:
        try:
            import pytesseract

            tesseract_error = getattr(pytesseract, "TesseractError", ())
            runtime_errors = (OSError, RuntimeError) + (
                (tesseract_error,) if isinstance(tesseract_error, type) else ()
            )
            try:
                return _analyze_with_tesseract(
                    img=img,
                    tesseract_cmd=resolved_tesseract,
                    confidence_threshold=confidence_threshold,
                    text_density_threshold=text_density_threshold,
                    word_count_threshold=word_count_threshold,
                )
            except runtime_errors as exc:
                logger.warning("Tesseract OCR runtime error (%s); falling back.", exc)
                if fallback_mode == "strict":
                    raise RuntimeError(f"Tesseract OCR failed: {exc}") from exc
        except ImportError:
            logger.warning("pytesseract package not installed; falling back from Tesseract OCR.")

    if fallback_mode == "strict":
        logger.error("Strict OCR mode requested but Tesseract is unavailable.")
        raise RuntimeError(
            "Tesseract OCR is not available (missing pytesseract package or tesseract binary)."
        )
    if fallback_mode == "none":
        logger.debug("Tesseract unavailable and fallback_mode='none'; returning empty OCR result.")
        return {
            "detected_text": "",
            "text_density": 0.0,
            "word_count": 0,
            "has_significant_text": False,
            "ocr_engine": "none",
        }

    logger.debug("Using OpenCV text-region fallback for %s.", image_path.name)
    return _analyze_with_opencv_text_regions(
        img=img,
        text_density_threshold=text_density_threshold,
    )


def analyze_frames_text_density(
    frame_paths: Sequence[Path],
    tesseract_cmd: Optional[str] = None,
    confidence_threshold: int = 60,
    text_density_threshold: float = 0.05,
    word_count_threshold: int = 10,
    text_sample_max_chars: int = 120,
    fallback_mode: str = "opencv_text_region",
) -> Dict[str, Any]:
    """Aggregate OCR/text-density measurements across all frames of a segment."""
    if not frame_paths:
        raise ValueError("frame_paths must contain at least one frame.")

    frame_results = [
        analyze_text_density(
            path,
            tesseract_cmd=tesseract_cmd,
            confidence_threshold=confidence_threshold,
            text_density_threshold=text_density_threshold,
            word_count_threshold=word_count_threshold,
            fallback_mode=fallback_mode,
        )
        for path in frame_paths
    ]

    avg_density = round(
        sum(r["text_density"] for r in frame_results) / len(frame_results), 4
    )
    max_words = max(r["word_count"] for r in frame_results)
    has_sig_text = any(r["has_significant_text"] for r in frame_results)

    longest_text = max((r["detected_text"] for r in frame_results), key=len, default="")
    if text_sample_max_chars <= 3:
        sample = longest_text[: max(text_sample_max_chars, 0)]
    elif len(longest_text) > text_sample_max_chars:
        sample = longest_text[: text_sample_max_chars - 3].rstrip() + "..."
    else:
        sample = longest_text

    engines = {r["ocr_engine"] for r in frame_results}
    ocr_engine = next(iter(engines)) if len(engines) == 1 else "mixed"

    return {
        "detected_text_sample": sample,
        "text_density": avg_density,
        "word_count": max_words,
        "has_significant_text": has_sig_text,
        "ocr_engine": ocr_engine,
    }
