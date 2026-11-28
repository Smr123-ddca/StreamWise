# StreamWise Integration Module (`backend/app/modules/integration/`)

The `integration` module connects StreamWise's **ContentUnderstanding Engine** (`backend/content_understand/`) and **HLS Packaging Engine** (`backend/app/modules/video_processing/`) with the **Decision Engine** (`backend/app/modules/decision_engine/`), enabling content-aware per-segment quality allocation without coupling analysis, packaging, or allocation internals.

---

## 1. Architecture & Data Flow

```text
ContentUnderstanding                HLS Packaging Engine
(content_metadata.json)             (hls_status.json)
  VideoAnalysisResult                 HLSResponse
         \                                 /
          \                               /
           v                             v
         ContentDecisionAdapter (content_decision_adapter.py)
           - Validate video_id, segment counts, durations, timelines
           - Align playlist-relative offsets with FFprobe PTS origins
           - Compute per-segment rendition bitrates from .ts file sizes
           - Map visual_complexity & conservative scene_type
           - Record documented fallbacks (motion, saliency, face_presence)
           - Preserve educational metadata in AdaptedSegmentContext
                         |
                         v
            AdaptationResult.decision_request
                         |
                         v
            DecisionService.decide(...)
                         |
                         v
       Per-Segment Rendition Decisions & Explanations
```

---

## 2. Segment Matching & Timestamp Handling

- **Chronological Index Alignment**: `ContentUnderstanding` segments are sorted by `segment_index` (`0..N-1`) and matched 1:1 with each successful HLS rendition's segments (`0..N-1`). Filenames are never required to match across modules.
- **Disparate Timestamp Origins**:
  - `ContentUnderstanding` reports playlist-relative timestamps (`start_time_sec`, starting at `0.0`).
  - `HLSSegmentInfo.start_time` stores FFprobe presentation timestamps (PTS), which include MPEG-TS container offsets (e.g., `1.466667s`).
  - The adapter normalizes each timeline relative to its segment-0 origin (`seg.start_time - origin`) and verifies relative timeline consistency and segment durations within `duration_tolerance_sec` (default `0.25s`).
- **Strict Failure Policy**: Video ID mismatches, segment count mismatches, duration mismatches, or timeline gaps/jumps raise `IntegrationAdapterError` (mapped to HTTP `422`) rather than associating analysis results with the wrong segment.

---

## 3. Per-Segment Rendition Bitrate Calculation

For each segment `i` and each **successful** HLS rendition (omitting `skipped_renditions` and `failed_renditions`):

```text
bitrate_kbps = (file_size_bytes * 8) / (duration_seconds * 1000)
```

- Uses actual `.ts` segment byte sizes and SI `1000` bits/kbps conversion (not `1024`, and not the rendition's overall average bitrate).
- Represents **estimated delivered segment bitrate**, including MPEG-TS container framing and AAC audio overhead when audio is present.
- Each segment's `Rendition` list is sorted strictly ascending by calculated `bitrate_kbps` as required by the Decision Engine (without assuming resolution order always equals bitrate order).
- Non-positive segment sizes or durations are explicitly rejected.

---

## 4. Feature Mapping & Fallback Policy

| ContentUnderstanding Field | Decision Engine Field | Mapping / Value | Fallback? |
| :--- | :--- | :--- | :--- |
| `visual_complexity` | `complexity` | Preserved normalized value `[0.0, 1.0]` | No |
| *(unavailable)* | `motion` | `0.5` neutral fallback | **Yes** |
| *(unavailable)* | `saliency` | `0.5` neutral fallback (never copied from `importance_score`) | **Yes** |
| *(unavailable)* | `face_presence` | `None` (optional field) | **Yes** |
| `content_type_hint` | `scene_type` | Conservative mapping below | No |

### Conservative Scene Mapping

| `content_type_hint` | `SceneType` |
| :--- | :--- |
| `code_screen` | `STATIC` |
| `presentation_slide` | `STATIC` |
| `diagram_or_whiteboard` | `STATIC` |
| `dynamic_scene` | `OTHER` |
| `talking_head_or_simple` | `OTHER` |
| `unknown` / unrecognized | `OTHER` |

- `scene_change_detected` is **not** automatically mapped to `SceneType.TRANSITION`, as an intra-segment cut does not establish that the whole segment is a transition.

### Preserved Educational Signals
`text_density`, `importance_score`, `has_significant_text`, `detected_text_sample`, `word_count`, `content_type_hint`, `scene_change_detected`, and `ocr_engine` are preserved inside `adapted_segments` in the integrated response.

> **Important Note**: Educational signals such as `importance_score` and `text_density` are preserved by the adapter for downstream visibility and future allocator extensions, **but are not necessarily consumed by the current Decision Engine allocator**, which scores priority using `motion`, `saliency`, `complexity`, `scene_type`, and `face_presence`.

---

## 5. Integrated API Endpoint

### `POST /api/videos/{video_id}/decisions`

Loads stored `content_metadata.json` (`ContentAnalyzer.load_metadata`) and `hls_status.json` (`load_hls_status`), aligns segments via `ContentDecisionAdapter`, and calls `DecisionService.decide(...)` with caller-supplied network/player state.

**Example Request:**
```json
{
  "network": {
    "throughput_estimate_kbps": 1800.0,
    "safety_margin": 0.8
  },
  "player": {
    "buffer_s": 12.0,
    "min_buffer_s": 5.0,
    "max_buffer_s": 30.0,
    "last_rendition_id": null
  },
  "viewer": {
    "mode": "balanced"
  }
}
```

**Example Response (`200 OK`, abbreviated):**
```json
{
  "video_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "decisions": [
    {
      "segment_id": "seg_000",
      "rendition_id": "480p",
      "bitrate_kbps": 840.0,
      "priority": 1.077,
      "utility": 0.72,
      "explanation": {
        "top_factors": [
          {"factor": "motion", "contribution": 0.125},
          {"factor": "saliency", "contribution": 0.125},
          {"factor": "scene_type", "contribution": 0.07}
        ],
        "utility_gain_vs_lower": 0.19,
        "utility_gain_vs_higher": 0.11,
        "stopping_constraint": "bandwidth",
        "switch_suppressed": false,
        "human": "Selected 480p at priority 1.08; next upgrade gain is 0.110, stopped by bandwidth."
      }
    }
  ],
  "window_budget_kbit": 8265.6,
  "used_kbit": 4200.0,
  "predicted_min_buffer_s": 11.2,
  "constraints_binding": [],
  "adapted_segments": [
    {
      "segment_id": "seg_000",
      "segment_index": 0,
      "duration_s": 4.0,
      "visual_complexity": 0.42,
      "text_density": 0.31,
      "importance_score": 0.88,
      "has_significant_text": true,
      "content_type_hint": "presentation_slide",
      "mapped_scene_type": "static",
      "fallback_fields": {
        "motion": {"value": 0.5, "is_fallback": true, "reason": "..."},
        "saliency": {"value": 0.5, "is_fallback": true, "reason": "..."},
        "face_presence": {"value": null, "is_fallback": true, "reason": "..."}
      }
    }
  ],
  "integration_metadata": {
    "excluded_renditions": {"skipped": [], "failed": []},
    "duration_tolerance_sec": 0.25,
    "bitrate_estimation_note": "...",
    "fallback_fields": ["motion", "saliency", "face_presence"]
  }
}
```

### Error Responses
- **400 Bad Request**: Invalid `video_id` UUID.
- **404 Not Found**: Content analysis metadata (`content_metadata.json`) or HLS metadata (`hls_status.json`) is missing.
- **422 Unprocessable Content**: Invalid network/player/viewer state, corrupted metadata, or segment alignment mismatch.

---

## 6. Known Limitations & Future Improvements

- **Motion & Saliency Fallbacks**: `motion` (`0.5`) and `saliency` (`0.5`) currently use documented neutral fallbacks until optical-flow motion vectors and visual saliency estimators are added to ContentUnderstanding.
- **Allocator Educational Awareness**: Extending `DecisionEngine` priority weights to directly incorporate `importance_score` and `text_density` (boosting legible text segments like slides/code) is deferred to the decision-engine tuning phase.
- **Container Overhead in Bitrate**: Segment bitrates reflect total `.ts` file sizes (video + audio + TS overhead).
