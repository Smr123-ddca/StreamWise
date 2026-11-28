# StreamWise Adaptive Playback Integration (`player.html` + Decision Engine)

This document explains how `frontend/player.html` and `frontend/adaptive_controller.js` connect browser HLS playback (`hls.js` v1.5.15 via CDN, with native HLS fallback) to StreamWise's backend Decision Engine (`POST /api/videos/{video_id}/decisions`).

---

## 1. Runtime Architecture & Serving

- `GET /player` (served by `backend/app/main.py`) returns `frontend/player.html`.
- `GET /adaptive_controller.js` (served by `backend/app/main.py` with `application/javascript; charset=utf-8`) returns `frontend/adaptive_controller.js`.
- `frontend/adaptive_controller.js` is structured as a UMD/global script exporting `StreamWiseAdaptive` on `globalThis` / `window` in classic browser `<script>` contexts (and supporting Node ESM tests via `globalThis.StreamWiseAdaptive`).

---

## 2. Telemetry Collection (`TelemetryTracker`)

`TelemetryTracker` gathers playback and network metrics without fabricating defaults when measurements are absent:

1. **Fragment Download Throughput (`FRAG_LOADED`)**:
   - Reads `frag.stats.loaded` (or `frag.stats.total`) and `frag.stats.loading.end - frag.stats.loading.start` (clamped to at least `1.0` ms to guard against sub-millisecond localhost timer spikes).
   - Computes instant fragment throughput in kbps (`(loadedBytes * 8.0) / safeMs`) and maintains a sliding window (`maxSamples = 5`) from which sample variance (`throughput_variance`) is computed.
2. **Smoothed Throughput Estimate (`getThroughputEstimate`)**:
   - When fragment samples exist and `hls.bandwidthEstimate` (bps) is positive, blends them as `0.6 * (hlsBandwidthEstimateBps / 1000.0) + 0.4 * sampleMean` (`source = "hls_ewma+fragments"`).
   - If only fragment samples exist, uses `sampleMean` (`source = "measured_fragments"`).
   - If no fragment samples exist yet, falls back to `hls.bandwidthEstimate / 1000.0` (`source = "hls_bandwidth_estimate"`), or `navigator.connection.downlink * 1000.0` strictly as an optional initial hint (`source = "navigator_connection_hint"`).
   - If none are positive/available, returns `source = "unavailable"` and skips calling the Decision API until real telemetry is ready.
3. **Buffer Depth (`getBufferedAheadSeconds`)**:
   - Inspects `video.buffered` ranges containing `video.currentTime` (with a `-0.1` s tolerance at range start) and reports `end - currentTime`.

---

## 3. Decision API Communication (`AdaptiveDecisionController`)

`buildDecisionPayload(...)` constructs the JSON request body matching `POST /api/videos/{video_id}/decisions`:

```json
{
  "network": {
    "throughput_estimate_kbps": 2400.0,
    "safety_margin": 0.8,
    "throughput_variance": 12500.0
  },
  "player": {
    "buffer_s": 8.5,
    "min_buffer_s": 5.0,
    "max_buffer_s": 30.0,
    "last_rendition_id": "480p"
  },
  "viewer": {
    "mode": "balanced"
  }
}
```

### Request Gating & Stale-Response Protection

- **Non-overlapping requests**: `inFlight` blocks concurrent `POST /decisions` requests.
- **Trigger conditions (`shouldRequestDecision`)**: fires initially once valid throughput is available, every `requestIntervalMs` (`4000` ms default), when viewer preference changes (`setPreferenceMode`), when buffer crosses below `5.0` s or changes by `>= 2.0` s (`bufferDeltaThresholdS`), or when throughput changes by `>= 20%` (`throughputDeltaThreshold = 0.2`).
- **Stale video cancellation**: `resetForVideo(videoId)` increments `videoToken` and calls `AbortController.abort()` on any in-flight request; responses from older tokens or disabled states are ignored.
- **Timeout & Validation**: `requestTimeoutMs` defaults to `4000` ms; `validateDecisionResponse` verifies response structure, `video_id`, and non-empty `decisions[]`.

---

## 4. Mapping & Segment-Level Switching Execution

### Rendition Mapping (`mapRenditionToHlsLevel`)

`hls.levels` order is never assumed to match backend bitrate or rendition order. For a target `decision.rendition_id` (e.g., `"480p"`), matching proceeds in order:

1. Exact `(width, height)` match against `segmentContext.renditions`.
2. URL path segment match (`(^|/)<renditionId>(/|$)`) on `lvl.url`.
3. Fallback `height` match from `segmentContext.renditions`.

If mapping fails, the controller logs the mismatch and enters ABR fallback.

### Next-Boundary Segment Switching

- `getUpcomingSegmentIndex(currentTime, bufferedAheadS)` computes `targetTime = currentTime + bufferedAheadS`, finds the corresponding segment in `adapted_segments` (`playlist_start_time_sec`, `duration_s`), and bounds it below by `lastLoadedSn + 1`.
- `applySegmentDecision(...)` looks up the decision for `targetIdx` and invokes `onApplyLevel(mappedLevel, ...)`, which sets `hls.nextLevel = mappedLevel` in `frontend/player.html`.
- Using `hls.nextLevel` (rather than forcing `hls.currentLevel` immediately mid-buffer) queues quality switches for the next unbuffered fragment boundary without flushing or interrupting already-buffered segments.
- **Anti-oscillation cooldown**: When switching renditions (`changedRendition`) at a new segment index while `bufferedAheadS > 6.0` s, switches faster than `minSwitchingCooldownMs` (`2500` ms) are suppressed.

---

## 5. Fallback, Manual Override, and Native HLS Limits

- **Fallback Mode (`ADAPTATION_MODES.FALLBACK`)**: Triggered on network/HTTP/timeout errors, malformed Decision API responses, or unmapped renditions. Calls `onRestoreAbr` (`hls.currentLevel = -1; hls.nextLevel = -1`) so `hls.js` native ABR takes over seamlessly, retrying the Decision API after `fallbackRetryMs` (`12000` ms).
- **Toggle Off (`#adaptive-toggle`)**: Sets mode to `ADAPTATION_MODES.NORMAL`, aborts pending requests, and restores `hls.js` ABR (`-1`).
- **Manual Quality Override (`#quality-select`)**: Selecting a specific quality sets `manualOverrideActive = true` and pauses Decision Engine switching until `"Auto"` (`-1`) is re-selected.
- **Native HLS Fallback (e.g., Safari without MSE)**: Browser plays `master.m3u8` natively; fine-grained `hls.nextLevel` programmatic switching is unavailable in native `<video>` mode, and the UI reports normal native playback mode.

---

## 6. UI Controls & Diagnostics (`frontend/player.html`)

- **Enable/Disable Toggle (`#adaptive-toggle`)**: Switches between backend Decision Engine control and `hls.js` ABR.
- **Preference Selector (`#preference-select`)**: `quality_first`, `balanced`, `data_saver`. Changing the mode resets cooldowns so the controller requests updated decisions promptly.
- **Diagnostics Readout**:
  - Current/target bitrate (`#bitrate-value`)
  - Estimated throughput (`#throughput-value`)
  - Active adaptation mode badge (`#adaptation-mode-badge`: `decision_engine`, `fallback`, or `normal`)
  - Last selected rendition ID (`#last-rendition-value`)
  - Decision status & per-segment human explanation (`#decision-status-value`, `#decision-explanation-value`)

---

## 7. Startup & Verification Commands

Start the FastAPI backend from the repository root:

```powershell
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/player` in a browser, enter a processed `video_id` (with MP4 transcode + HLS packaging + ContentUnderstanding analysis), click **Load Video**, and play while observing quality switching, preference changes, and event logs.

Run automated tests:

```powershell
node --test frontend/test_adaptive_controller.mjs
python -m pytest backend/tests/test_player_decision_integration.py -v
python -m pytest backend/tests backend/content_understand/tests -v
```
