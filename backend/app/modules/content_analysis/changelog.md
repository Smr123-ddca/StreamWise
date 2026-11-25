# Content Understanding Engine — Changelog & Developer Guide

> **Module Owners:** Content Understanding (`backend/content_understand/` & `backend/app/modules/content_analysis/`)  
> **Branch:** `ContentUnderstanding` (branched from `main` at `33c8c83`)  
> **Last Updated:** 2026-11-25

---

## 1. Changelog

### `[Unreleased]` — `ContentUnderstanding` Branch (2026-11-25)

#### Added
- **Content Understanding Engine (`backend/content_understand/`)**:
  - `frame_extractor.py`: Safe `subprocess` wrappers (`shell=False`) for `ffprobe` duration probing and `ffmpeg` evenly-spaced representative frame extraction (`jpg`/`png`) across HLS `.ts`/`.m4s`/`.mp4` segments, with single-frame fallback for ultra-short clips, timeout validation, and `MediaExtractionError`.
  - `scene_detector.py`: L1-normalized grayscale histogram comparison (`cv2.HISTCMP_BHATTACHARYYA`) for intra- and inter-segment transitions plus `PySceneDetect` `ContentDetector` wrapper (`get_scene_changes`).
  - `complexity_analyzer.py`: Gaussian blur + Canny edge density scoring normalized to `[0.0, 1.0]`.
  - `ocr_analyzer.py`: Thread-safe (`_TESSERACT_LOCK`) `pytesseract` bounding-box coverage mask OCR (`>= confidence_threshold`) with an OpenCV morphological gradient text-region fallback (`opencv_text_region`) when Tesseract is unavailable or errors at runtime.
  - `importance_scorer.py`: Configurable weighted contextual importance score `[0.0, 1.0]` (`visual_complexity=0.30`, `text_density=0.40`, `scene_change_bonus=0.20`, `text_flag_bonus=0.10`) and informational `content_type_hint`.
  - `analyzer.py`: End-to-end `ContentAnalyzer` supporting HLS master playlists (`#EXT-X-STREAM-INF`), nested rendition directories (preferring highest resolution/bandwidth), cumulative playlist timestamp alignment when segments are missing, `prev_segment_last_frame` cross-segment continuity, configurable `max_segments` / `analysis_timeout_sec` limits, and atomic `content_metadata.json` writes via `os.replace()`.
  - `models.py`: Contract-aligned Pydantic schemas (`SegmentInput` with JSON path serializer, `SegmentAnalysis`, `SegmentMetadata`, `VideoAnalysisResult`, `AnalyzeVideoRequest`).
  - `config.py`: `AnalysisConfig` and `ImportanceWeights` with kernel/Canny threshold validation and dynamic `get_analysis_config()`.
  - `api.py`: Transport-agnostic handlers and FastAPI `/analysis` router (`GET /analysis/health`, `POST /analysis/analyze/{video_id}`, `GET /analysis/metadata/{video_id}`, `GET /analysis/metadata/{video_id}/{segment_id}`) using `asyncio.to_thread()` and lazy module-level `router`/`app` exports.
  - `examples/`: Example generator (`generate_examples.py`) and sample outputs (`lecture_slides_metadata.json`, `coding_tutorial_metadata.json`, `animated_explainer_metadata.json`).
  - `tests/`: 43 unit, API, and integration tests covering synthetic `.ts` segments, HLS master playlists, security/path-confinement guards, OCR fallbacks, and timeouts.
- **FastAPI & App Integration (`backend/app/`)**:
  - `backend/app/modules/content_analysis/__init__.py`: Re-exports `ContentAnalyzer`, `AnalysisConfig`, `ImportanceWeights`, `build_router`, and Pydantic schemas seamlessly whether run from repository root (`backend.*`) or from `backend/` (`app.*`).
  - `backend/app/api/analysis_routes.py`: Mounts `/analysis/*` routes with `get_content_analyzer()` aligned to `Settings.storage_dir`, `ffmpeg_path`, `ffprobe_path`, and `tesseract_cmd`.
  - `backend/app/api/__init__.py` & `backend/app/main.py`: Exported and registered `analysis_router` on the main FastAPI `app`.
  - `pyproject.toml`: Configured `pytest` `pythonpath = ["."]` and `testpaths`.

#### Changed (Shared / Video Processing Coordination)
- `backend/requirements.txt`: Chained `-r content_understand/requirements.txt` so installing backend dependencies covers Content Understanding packages.
- `backend/app/config/settings.py`:
  - Added optional `ffmpeg_path` and `tesseract_cmd` fields and configured `env_file` to load `backend/.env` regardless of working directory.
  - Extracted shared `resolve_storage_directory()` so uploads and processed analysis read/write the same storage root and custom relative `STORAGE_DIR` paths are preserved.
- `backend/app/modules/video_processing/service.py`:
  - Reuses `resolve_storage_directory()` from `app.config.settings`.
  - Runs `extract_video_metadata()` via `asyncio.to_thread()` so upload metadata extraction does not block the FastAPI event loop.
- `backend/app/modules/video_processing/metadata.py`:
  - Verified `os.path.isfile()` in `get_ffprobe_executable()`, caught `OSError` -> HTTP `503` on subprocess execution, and safely cast `width`/`height` via `int(...)` returning HTTP `422` for non-numeric dimensions.
- `backend/app/modules/video_processing/uploader.py`:
  - Removed redundant duplicate `cleanup_upload()` calls inside `try:` before raising `HTTPException`.
- `backend/tests/test_video_upload.py`:
  - Added `test_11_empty_storage_dir_and_non_numeric_dimensions_rejected` (total: 12 tests in `backend/tests/`).

#### Security & Reliability Hardening
- Validated `video_id` against `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (rejecting `/`, `\`, and `..`).
- Confined HTTP `segments_dir` overrides to the configured storage root and `output_path` overrides to that video's processed directory (`video_dir`), rejecting directory targets with HTTP `422`.
- Confined `.m3u8` variant and segment URIs to the playlist's directory.
- Fixed standalone `build_router()` default dependency so FastAPI does not expose `AnalysisConfig` in HTTP request bodies.

---

## 2. Instructions for Other Developers

### Local Setup & Running Tests

1. **Install Python dependencies** (from repository root or `backend/`):
   ```bash
   pip install -r backend/requirements.txt
   ```
2. **System binaries**:
   - **Required:** `ffmpeg` and `ffprobe` on `PATH` (or set `FFMPEG_PATH` / `FFPROBE_PATH` in `backend/.env`).
   - **Optional:** `tesseract` binary (or set `TESSERACT_CMD` in `backend/.env`). If Tesseract is absent, OCR falls back automatically to `opencv_text_region` (measuring real text-region coverage without fabricating text).
3. **Run the FastAPI server**:
   - From repository root:
     ```bash
     python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
     ```
   - Or from `backend/`:
     ```bash
     uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
     ```
   - OpenAPI docs are available at `http://127.0.0.1:8000/docs`.
4. **Run test suites** (from repository root):
   ```bash
   python -m unittest discover -s backend/content_understand/tests -v
   python -m unittest discover -s backend/tests -v
   ```

---

### For Person 1 — Video Infrastructure (`video_processing`)

- **Expected processed HLS output directory:**
  Write HLS segments and playlists under:
  - `storage/processed/{video_id}/segments/` (e.g., `playlist.m3u8` + `seg_000.ts`, `seg_001.ts`, ...), **or**
  - `storage/processed/{video_id}/master.m3u8` referencing rendition subdirectories (e.g., `360p/playlist.m3u8`, `480p/playlist.m3u8`, `720p/playlist.m3u8`).
- **Rendition selection:** `ContentAnalyzer` parses `#EXT-X-STREAM-INF` (`RESOLUTION` / `BANDWIDTH`) and automatically selects the **highest-resolution** variant for visual/OCR accuracy. Keep segment numbering and `#EXTINF` durations aligned across `360p`/`480p`/`720p` renditions so segment indices and timestamps match Decision Engine requests.
- **Note on shared edits:** Check the non-blocking `asyncio.to_thread` and validation updates made in `backend/app/modules/video_processing/service.py`, `metadata.py`, and `uploader.py` when merging.

---

### For Person 3 — Decision Intelligence (`decision_engine`)

#### Option A: Import in Python (Backend-to-Backend)
```python
from app.modules.content_analysis import ContentAnalyzer, VideoAnalysisResult
# Or from repo root:
# from backend.app.modules.content_analysis import ContentAnalyzer, VideoAnalysisResult

analyzer = ContentAnalyzer()
result: VideoAnalysisResult = analyzer.load_metadata(video_id="your_video_id")
# To run analysis on-demand if not yet analyzed:
# result = analyzer.analyze_video(video_id="your_video_id")
```

#### Option B: HTTP Endpoints
- `POST /analysis/analyze/{video_id}` — Trigger analysis and persist `storage/processed/{video_id}/content_metadata.json`
- `GET /analysis/metadata/{video_id}` — Retrieve full `VideoAnalysisResult`
- `GET /analysis/metadata/{video_id}/{segment_id}` — Retrieve single `SegmentMetadata`

#### Output Schema Contract (`VideoAnalysisResult`)
```json
{
  "video_id": "coding_tutorial_001",
  "total_segments": 3,
  "analysis_version": "0.1.0",
  "segments": [
    {
      "video_id": "coding_tutorial_001",
      "segment_id": "seg_001",
      "segment_index": 1,
      "start_time_sec": 4.0,
      "end_time_sec": 8.0,
      "duration_sec": 4.0,
      "analysis": {
        "visual_complexity": 0.1485,
        "text_density": 0.1093,
        "scene_change_detected": true,
        "detected_text_sample": "",
        "word_count": 0,
        "has_significant_text": true,
        "importance_score": 0.3883,
        "content_type_hint": "presentation_slide",
        "ocr_engine": "opencv_text_region"
      },
      "frames_analyzed": 3,
      "analysis_timestamp": "2026-11-25T01:53:22Z",
      "analyzer_version": "0.1.0"
    }
  ]
}
```
- **Units & Ranges:**
  - `start_time_sec`, `end_time_sec`, `duration_sec`: `float` in **seconds** (`>= 0.0`).
  - `visual_complexity`, `text_density`, `importance_score`: normalized `float` in **`[0.0, 1.0]`**.
  - `ocr_engine`: `"tesseract"`, `"opencv_text_region"`, `"mixed"`, or `"none"`. When running without the Tesseract OS binary, `detected_text_sample` is `""` and `word_count` is `0`, while `text_density` and `has_significant_text` remain populated via OpenCV.
  - `content_type_hint`: informational only (`code_screen`, `presentation_slide`, `diagram_or_whiteboard`, `dynamic_scene`, `talking_head_or_simple`).
- See real generated samples in `backend/content_understand/examples/`.

---

### For Person 4 — Frontend, Playback & Evaluation (`frontend`)

- **Endpoints mounted on `backend.app.main:app`:**
  - `GET /analysis/health` — Check FFmpeg, FFprobe, PySceneDetect, and Tesseract availability.
  - `POST /analysis/analyze/{video_id}` — Optional JSON body:
    ```json
    {
      "frames_per_segment": 3
    }
    ```
    *(Avoid sending `segments_dir` or `output_path` from the browser; they are restricted to the server's configured storage root for that `video_id`.)*
  - `GET /analysis/metadata/{video_id}` — Fetch segment timeline (`start_time_sec`, `end_time_sec`, `importance_score`, `visual_complexity`, `text_density`, `scene_change_detected`, `content_type_hint`) for visualizing segment-level quality allocation and explanations.
  - `GET /analysis/metadata/{video_id}/{segment_id}` — Fetch single-segment analysis details.
- **HTTP Status Codes to handle in UI:**
  - `200`: Success
  - `404`: Segmented video directory or pre-computed `content_metadata.json` not found (trigger `POST /analysis/analyze/{video_id}` first)
  - `422`: Invalid `video_id`, corrupt/unreadable segment (`MediaExtractionError`), or segment count exceeds `max_segments` (`500`)
  - `503`: Missing `ffmpeg` / `ffprobe` (or `tesseract` in `strict` mode)
  - `504`: Analysis exceeded timeout (`analysis_timeout_sec` / `ffmpeg_timeout_sec`)
  - `500`: Corrupted `content_metadata.json` on disk
- **CORS:** `http://localhost:5173`, `http://127.0.0.1:5173`, and `http://localhost:3000` are already allowed in `backend/app/main.py`.
