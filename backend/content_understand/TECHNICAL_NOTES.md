# Technical Notes — Content Understanding Engine

## 1. Signal Definitions & Algorithms

- **Frame Extraction (`frame_extractor.py`)**:
  - Probes segment duration via `ffprobe` (`-show_entries format=duration`), falling back to `AnalysisConfig.default_segment_duration_sec` (`6.0s`) when absent.
  - Computes target extraction rate `fps = max(num_frames / max(duration, 0.25), 0.1)` rather than a fixed `fps=1/N`, because HLS segments are typically 2–6 seconds long and a fixed `fps=1/3` filter would yield only 1–2 frames on short clips.
  - Includes a single-frame fallback (`-frames:v 1`) for ultra-short trailing segments.

- **Scene Detection (`scene_detector.py`)**:
  - **Inter- and intra-segment cuts**: Uses 256-bin grayscale histograms normalized with `cv2.NORM_L1` before `cv2.compareHist(..., cv2.HISTCMP_BHATTACHARYYA)`. Explicit L1 normalization guarantees the Bhattacharyya distance stays bounded in `[0.0, 1.0]` across differing frame resolutions.
  - Compares the final extracted frame of segment `i - 1` against the first frame of segment `i`, as well as consecutive frames within segment `i`.
  - **Full-clip scene timestamps**: Wraps `scenedetect.ContentDetector(threshold, min_scene_len)` in `get_scene_changes()`. Note that `ContentDetector` defaults to `min_scene_len=15` frames; callers analyzing short clips at low FPS should pass an appropriately smaller `min_scene_len`.

- **Visual Complexity (`complexity_analyzer.py`)**:
  - Applies Gaussian blur (`blur_kernel_size=5`) followed by Canny edge detection (`50`, `150`).
  - Computes `edge_density = count_nonzero(edges) / edges.size`, scaled by `complexity_normalization_factor=5.0` and clamped to `[0.0, 1.0]`.

- **OCR & Text Density (`ocr_analyzer.py`)**:
  - **Primary (`tesseract`)**: Uses `pytesseract.image_to_data` with confidence filtering (`> 60`). Bounding boxes are rasterized onto a binary coverage mask (`uint8`) so overlapping word boxes are not double-counted when computing `text_density`.
  - **Fallback (`opencv_text_region`)**: When `pytesseract` or the `tesseract` binary is unavailable, applies combined top-hat and black-hat morphological filtering `(13, 3)` (handling both dark-on-light slides and light-on-dark IDEs), horizontal Sobel gradient, Otsu thresholding (gated by minimum contrast std `>= 4.0`), morphological closing `(15, 3)`, and contour bounding-box geometry/fill filtering. It outputs genuine `text_density` coverage without fabricating recognized text (`detected_text_sample=""`, `word_count=0`). Set `ocr_fallback_mode="strict"` to require Tesseract.

- **Contextual Importance Scoring (`importance_scorer.py`)**:
  - Weighted linear combination clamped to `[0.0, 1.0]` with configurable `ImportanceWeights`:
    - `visual_complexity`: `0.30`
    - `text_density`: `0.40`
    - `scene_change_bonus`: `0.20`
    - `text_flag_bonus`: `0.10`
  - `content_type_hint` is strictly an informational label (`code_screen`, `presentation_slide`, `diagram_or_whiteboard`, `dynamic_scene`, `talking_head_or_simple`) and is never used to hardcode playback decisions.

## 2. Known Limitations & Assumptions

- In environments without the Tesseract system binary (`tesseract.exe`) or `pytesseract`, `detected_text_sample` and `word_count` remain empty/`0` while `text_density` and `has_significant_text` are measured via `opencv_text_region`.
- `fastapi` is optional at import time (`api.py` sets `FASTAPI_AVAILABLE = False` if not installed) while keeping handler functions (`analyze_video_handler`, `get_metadata_handler`, `get_segment_metadata_handler`, `health_handler`) testable without FastAPI; FastAPI `TestClient` tests require installing `fastapi`.
- When both an `.m3u8` media playlist and raw `.ts` files exist in a segment directory, `ContentAnalyzer.discover_segments()` prioritizes the `.m3u8` playlist for authoritative `#EXTINF` durations.
