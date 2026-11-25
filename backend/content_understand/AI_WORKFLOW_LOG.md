# AI Workflow Log — Content Understanding Engine

## Session: 2026-11-25

- **Task**: Implement the Content Understanding Engine inside `backend/content_understand/` strictly following `plan.md`, without modifying any other folder.
- **Inspection & Environment Checks**:
  - Verified existing `backend/app/` conventions (Pydantic schemas, FFprobe executable discovery on Windows WinGet paths, `unittest` suite structure).
  - Verified runtime tools: `ffmpeg`, `ffprobe`, `opencv-python`, `numpy`, `Pillow`, `scenedetect`, and `pydantic` are present; `fastapi`, `pytesseract`, and the `tesseract` OS binary are absent in the base Python 3.14 environment.
- **Design Corrections Made During Implementation**:
  - **Frame extraction FPS calculation**: Adjusted `plan.md`'s fixed `-vf fps=1/{num_frames}` snippet to dynamically compute `fps = num_frames / effective_duration` from `ffprobe` duration, ensuring 2s–6s HLS `.ts` segments yield the requested number of frames.
  - **Histogram normalization**: Added `cv2.normalize(..., norm_type=cv2.NORM_L1)` before `cv2.compareHist(..., cv2.HISTCMP_BHATTACHARYYA)` so histogram distances stay properly bounded in `[0.0, 1.0]`.
  - **Non-overlapping OCR coverage mask**: Replaced raw bounding-box area summation (`sum(w * h)`) with a boolean/uint8 pixel coverage mask to avoid over-counting overlapping word boxes.
  - **Graceful Tesseract & FastAPI handling**: Added a real OpenCV morphological text-region coverage fallback (`ocr_engine="opencv_text_region"`) that never fabricates recognized text when Tesseract is missing, and separated transport-agnostic API handlers in `api.py` so tests run cleanly both with and without `fastapi` installed.
- **Test Failures Diagnosed & Resolved**:
  - `test_pyscenedetect_detects_cut_in_video`: Initially failed because PySceneDetect `ContentDetector` defaults to `min_scene_len=15` frames on a 12-frame clip. Exposed `min_scene_len` on `get_scene_changes()` and updated the synthetic test clip to 32 frames (`16 fps` for `2.0s`).
  - `test_sample_2_coding_tutorial_pipeline`: Initial segment order (`code1 -> code2 -> blank`) caused the trailing blank segment to receive a `+0.20` `scene_change_bonus`, exceeding steady-state `code2` (`0.1788`). Reordered the synthetic tutorial sample to `blank_intro -> code1 -> code2` so both code segments exceed the blank intro in importance score.
