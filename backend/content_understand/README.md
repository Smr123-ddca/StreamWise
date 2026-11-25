# StreamWise — Content Understanding Engine (`backend/content_understand`)

The **Content Understanding Engine** analyzes HLS video segments (`.ts` / `.m4s` / `.mp4`) and produces structured, segment-level JSON metadata for the Adaptive Quality Decision Engine. It computes visual complexity, text/OCR density, scene transitions, and a normalized contextual importance score without making playback or bitrate decisions.

## Module Structure

```text
backend/content_understand/
├── __init__.py
├── analyzer.py              # Pipeline orchestration & HLS segment/playlist discovery
├── api.py                   # FastAPI router (/analysis/*) + transport-agnostic handlers
├── complexity_analyzer.py   # Gaussian blur + Canny edge density scoring
├── config.py                # Configurable weights, thresholds, paths (AnalysisConfig)
├── frame_extractor.py       # FFmpeg/FFprobe duration probing & frame extraction
├── importance_scorer.py     # Normalized weighted importance scoring & content_type_hint
├── models.py                # Pydantic schemas for segment and video metadata contracts
├── ocr_analyzer.py          # Tesseract OCR (pytesseract) + OpenCV text-region fallback
├── scene_detector.py        # Normalized Bhattacharyya histogram + PySceneDetect wrapper
├── requirements.txt         # Module Python dependencies
├── examples/
│   ├── generate_examples.py
│   ├── lecture_slides_metadata.json
│   ├── coding_tutorial_metadata.json
│   └── animated_explainer_metadata.json
└── tests/
    ├── _synthetic_media.py
    ├── test_frame_extractor.py
    ├── test_scene_detector.py
    ├── test_complexity_analyzer.py
    ├── test_ocr_analyzer.py
    ├── test_importance_scorer.py
    ├── test_analyzer_integration.py
    └── test_api.py
```

## API Endpoints (`/analysis`)

- `GET /analysis/health`: Returns module readiness and binary/package capabilities (`ffmpeg`, `ffprobe`, `scenedetect`, `tesseract`).
- `POST /analysis/analyze/{video_id}`: Runs the analysis pipeline on segments in `storage/processed/{video_id}/segments/` (or custom `segments_dir`), saves `content_metadata.json`, and returns `VideoAnalysisResult`.
- `GET /analysis/metadata/{video_id}`: Returns pre-computed `VideoAnalysisResult` JSON (`404` if not yet analyzed).
- `GET /analysis/metadata/{video_id}/{segment_id}`: Returns `SegmentMetadata` for a single segment (`404` if missing).

### Mounting into `backend/app/main.py`

```python
from backend.content_understand.api import router as content_analysis_router
app.include_router(content_analysis_router)
```

Or run standalone when `fastapi` and `uvicorn` are installed:

```bash
uvicorn backend.content_understand.api:app --reload --port 8001
```

## Running Tests & Generating Example Outputs

From the repository root:

```bash
python -m unittest discover -s backend/content_understand/tests -v
python -m backend.content_understand.examples.generate_examples
```

## Dependencies & System Binaries

- **Required binaries**: `ffmpeg` and `ffprobe` on `PATH` (or via `FFMPEG_PATH` / `FFPROBE_PATH`).
- **Optional OCR binary**: `tesseract` (`TESSERACT_CMD` or `C:\Program Files\Tesseract-OCR\tesseract.exe`) + `pytesseract`. When Tesseract is unavailable, `ocr_analyzer.py` defaults to `ocr_fallback_mode="opencv_text_region"`, which measures genuine text-region pixel coverage via OpenCV morphological gradients while leaving `detected_text_sample` empty (`""`) and `ocr_engine="opencv_text_region"`.
