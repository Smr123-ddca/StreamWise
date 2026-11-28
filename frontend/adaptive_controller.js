/**
 * StreamWise Adaptive Playback Controller
 *
 * Dual-environment (Browser global + Node CommonJS) module handling:
 * - Reliable hls.js fragment telemetry collection (throughput & variance)
 * - Backend rendition ID <-> hls.js level index mapping by dimensions & URL
 * - Segment-level decision selection aligned to upcoming playback boundaries
 * - Non-overlapping Decision API requests with stale-video cancellation & ABR fallback
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.StreamWiseAdaptive = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ADAPTATION_MODES = Object.freeze({
    NORMAL: "normal",
    DECISION_ENGINE: "decision_engine",
    FALLBACK: "fallback",
  });

  const PREFERENCE_MODES = new Set(["quality_first", "balanced", "data_saver"]);

  /**
   * Map a backend rendition ID (e.g. '480p') and its metadata to an hls.js level index.
   * Matches strictly by (width, height) first, then URL path segment, then height.
   * Never assumes hls.levels order matches backend rendition or bitrate order.
   */
  function mapRenditionToHlsLevel(renditionId, segmentRenditions, hlsLevels) {
    if (!renditionId || !Array.isArray(hlsLevels) || hlsLevels.length === 0) {
      return null;
    }

    const meta = Array.isArray(segmentRenditions)
      ? segmentRenditions.find((r) => r && r.id === renditionId)
      : null;

    if (meta && Number.isFinite(meta.width) && Number.isFinite(meta.height)) {
      const exactIdx = hlsLevels.findIndex(
        (lvl) => lvl && lvl.width === meta.width && lvl.height === meta.height
      );
      if (exactIdx !== -1) {
        return exactIdx;
      }
    }

    const escaped = String(renditionId).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const urlPattern = new RegExp(`(^|/)${escaped}(/|$)`, "i");
    const urlIdx = hlsLevels.findIndex((lvl) => {
      if (!lvl) return false;
      const urls = Array.isArray(lvl.url) ? lvl.url : lvl.url ? [lvl.url] : [];
      return urls.some((u) => urlPattern.test(String(u)));
    });
    if (urlIdx !== -1) {
      return urlIdx;
    }

    if (meta && Number.isFinite(meta.height)) {
      const heightIdx = hlsLevels.findIndex((lvl) => lvl && lvl.height === meta.height);
      if (heightIdx !== -1) {
        return heightIdx;
      }
    }

    return null;
  }

  /**
   * Compute buffered seconds ahead of currentTime from TimeRanges-like object.
   */
  function getBufferedAheadSeconds(buffered, currentTime) {
    if (!buffered || typeof buffered.length !== "number") {
      return 0;
    }
    const cur = Number.isFinite(currentTime) && currentTime >= 0 ? currentTime : 0;
    for (let i = 0; i < buffered.length; i += 1) {
      const start = buffered.start(i);
      const end = buffered.end(i);
      if (cur >= start - 0.1 && cur <= end) {
        return Math.max(0, end - cur);
      }
    }
    return 0;
  }

  /**
   * Build validated request payload matching POST /api/videos/{video_id}/decisions.
   */
  function buildDecisionPayload({
    throughputKbps,
    throughputVariance = null,
    safetyMargin = 0.8,
    bufferS = 0,
    minBufferS = 5.0,
    maxBufferS = 30.0,
    lastRenditionId = null,
    preferenceMode = "balanced",
  }) {
    if (!Number.isFinite(throughputKbps) || throughputKbps <= 0) {
      throw new Error("Reliable positive throughput estimate is required before calling Decision API.");
    }
    const mode = PREFERENCE_MODES.has(preferenceMode) ? preferenceMode : "balanced";
    const safeMargin = Math.min(1.0, Math.max(0.01, Number(safetyMargin) || 0.8));
    const safeMinBuf = Math.max(0, Number(minBufferS) || 5.0);
    const safeMaxBuf = Math.max(safeMinBuf, Number(maxBufferS) || 30.0);
    const safeBuf = Math.max(0, Number(bufferS) || 0);

    const network = {
      throughput_estimate_kbps: Number(throughputKbps.toFixed(2)),
      safety_margin: Number(safeMargin.toFixed(2)),
    };
    if (Number.isFinite(throughputVariance) && throughputVariance >= 0) {
      network.throughput_variance = Number(throughputVariance.toFixed(2));
    }

    return {
      network,
      player: {
        buffer_s: Number(safeBuf.toFixed(2)),
        min_buffer_s: safeMinBuf,
        max_buffer_s: safeMaxBuf,
        last_rendition_id: lastRenditionId || null,
      },
      viewer: {
        mode,
      },
    };
  }

  /**
   * Validate response returned by POST /api/videos/{video_id}/decisions.
   */
  function validateDecisionResponse(data, expectedVideoId) {
    if (!data || typeof data !== "object") {
      throw new Error("Decision API returned an empty or non-JSON payload.");
    }
    if (expectedVideoId && data.video_id && data.video_id !== expectedVideoId) {
      throw new Error(`Decision response video_id '${data.video_id}' does not match '${expectedVideoId}'.`);
    }
    if (!Array.isArray(data.decisions) || data.decisions.length === 0) {
      throw new Error("Decision API returned no segment decisions.");
    }
    for (const dec of data.decisions) {
      if (!dec || typeof dec.segment_id !== "string" || typeof dec.rendition_id !== "string") {
        throw new Error("Malformed segment decision entry in Decision API response.");
      }
    }
    return data;
  }

  /**
   * Tracks fragment download throughput samples and computes EWMA/mean + variance.
   */
  class TelemetryTracker {
    constructor(maxSamples = 5) {
      this.maxSamples = maxSamples;
      this.samplesKbps = [];
      this.lastSource = "unavailable";
    }

    reset() {
      this.samplesKbps = [];
      this.lastSource = "unavailable";
    }

    /**
     * Record a completed hls.js fragment load (`FRAG_LOADED` event payload).
     */
    recordFragmentLoad(frag) {
      if (!frag || !frag.stats) return null;
      const stats = frag.stats;
      const loadedBytes = Number(stats.loaded || stats.total || 0);
      const loading = stats.loading || {};
      const startMs = Number(loading.start);
      const endMs = Number(loading.end);
      const elapsedMs = endMs - startMs;

      if (!Number.isFinite(loadedBytes) || loadedBytes <= 0 || !Number.isFinite(elapsedMs) || elapsedMs <= 0) {
        return null;
      }

      // Guard against localhost sub-millisecond cache/timer spikes; clamp min 1ms
      const safeMs = Math.max(1.0, elapsedMs);
      // bytes * 8 / ms == kbits / s == kbps
      const kbps = (loadedBytes * 8.0) / safeMs;
      this.samplesKbps.push(kbps);
      if (this.samplesKbps.length > this.maxSamples) {
        this.samplesKbps.shift();
      }
      this.lastSource = "measured_fragments";
      return kbps;
    }

    /**
     * Obtain best available throughput estimate in kbps without fabricating data.
     */
    getThroughputEstimate(hlsBandwidthEstimateBps = null, navigatorConnection = null) {
      if (this.samplesKbps.length > 0) {
        const mean = this.samplesKbps.reduce((acc, v) => acc + v, 0) / this.samplesKbps.length;
        const variance =
          this.samplesKbps.length > 1
            ? this.samplesKbps.reduce((acc, v) => acc + (v - mean) ** 2, 0) / this.samplesKbps.length
            : 0;

        // Blend with hls.js EWMA bandwidthEstimate when available for smoothing
        let estimate = mean;
        let source = "measured_fragments";
        if (Number.isFinite(hlsBandwidthEstimateBps) && hlsBandwidthEstimateBps > 0) {
          const hlsKbps = hlsBandwidthEstimateBps / 1000.0;
          estimate = 0.6 * hlsKbps + 0.4 * mean;
          source = "hls_ewma+fragments";
        }
        return { kbps: estimate, variance, source };
      }

      if (Number.isFinite(hlsBandwidthEstimateBps) && hlsBandwidthEstimateBps > 0) {
        return {
          kbps: hlsBandwidthEstimateBps / 1000.0,
          variance: null,
          source: "hls_bandwidth_estimate",
        };
      }

      if (
        navigatorConnection &&
        Number.isFinite(navigatorConnection.downlink) &&
        navigatorConnection.downlink > 0
      ) {
        return {
          kbps: navigatorConnection.downlink * 1000.0,
          variance: null,
          source: "navigator_connection_hint",
        };
      }

      return { kbps: null, variance: null, source: "unavailable" };
    }
  }

  /**
   * Coordinates periodic Decision API calls, segment-level switching, and ABR fallback.
   */
  class AdaptiveDecisionController {
    constructor({
      fetchFn,
      apiBase,
      onApplyLevel,
      onRestoreAbr,
      onStatusUpdate,
      onLog,
      requestIntervalMs = 4000,
      requestTimeoutMs = 4000,
      fallbackRetryMs = 12000,
      minSwitchingCooldownMs = 2500,
      throughputDeltaThreshold = 0.2,
      bufferDeltaThresholdS = 2.0,
    }) {
      this.fetchFn = fetchFn;
      this.apiBase = apiBase;
      this.onApplyLevel = onApplyLevel;
      this.onRestoreAbr = onRestoreAbr;
      this.onStatusUpdate = onStatusUpdate || (() => {});
      this.onLog = onLog || (() => {});
      this.requestIntervalMs = requestIntervalMs;
      this.requestTimeoutMs = requestTimeoutMs;
      this.fallbackRetryMs = fallbackRetryMs;
      this.minSwitchingCooldownMs = minSwitchingCooldownMs;
      this.throughputDeltaThreshold = throughputDeltaThreshold;
      this.bufferDeltaThresholdS = bufferDeltaThresholdS;

      this.telemetry = new TelemetryTracker();
      this.enabled = true;
      this.preferenceMode = "balanced";
      this.manualOverrideActive = false;
      this.mode = ADAPTATION_MODES.DECISION_ENGINE;

      this.videoId = null;
      this.videoToken = 0;
      this.inFlight = false;
      this.abortController = null;
      this.lastRequestAt = 0;
      this.lastFailureAt = 0;
      this.lastRequestThroughput = null;
      this.lastRequestBuffer = null;
      this.lastSwitchAt = 0;
      this.lastAppliedSegmentIndex = -1;
      this.lastSelectedRenditionId = null;

      this.decisionPlan = null; // { decisionsByIndex, adaptedSegments, rawResponse }
      this.lastLoadedSn = -1;
    }

    resetForVideo(videoId) {
      this.cancelInFlight();
      this.videoId = videoId || null;
      this.videoToken += 1;
      this.telemetry.reset();
      this.decisionPlan = null;
      this.lastRequestAt = 0;
      this.lastFailureAt = 0;
      this.lastRequestThroughput = null;
      this.lastRequestBuffer = null;
      this.lastSwitchAt = 0;
      this.lastAppliedSegmentIndex = -1;
      this.lastSelectedRenditionId = null;
      this.lastLoadedSn = -1;
      this.manualOverrideActive = false;
      this.mode = this.enabled ? ADAPTATION_MODES.DECISION_ENGINE : ADAPTATION_MODES.NORMAL;
    }

    cancelInFlight() {
      if (this.abortController) {
        this.abortController.abort();
        this.abortController = null;
      }
      this.inFlight = false;
    }

    setEnabled(enabled) {
      this.enabled = Boolean(enabled);
      if (!this.enabled) {
        this.cancelInFlight();
        this.mode = ADAPTATION_MODES.NORMAL;
        if (!this.manualOverrideActive) {
          this.onRestoreAbr("Decision Engine disabled; normal hls.js ABR active.");
        }
      } else {
        this.mode = ADAPTATION_MODES.DECISION_ENGINE;
        this.lastFailureAt = 0;
        this.lastRequestAt = 0;
      }
      this.emitStatus();
    }

    setPreferenceMode(mode) {
      if (!PREFERENCE_MODES.has(mode)) return;
      this.preferenceMode = mode;
      // Allow immediate re-evaluation on explicit viewer preference change
      this.lastRequestAt = 0;
      this.lastFailureAt = 0;
    }

    setManualOverride(active) {
      this.manualOverrideActive = Boolean(active);
      if (this.manualOverrideActive) {
        this.onLog("Manual quality lock active; Decision Engine switching paused until Auto is selected.");
      } else if (this.enabled) {
        this.mode = ADAPTATION_MODES.DECISION_ENGINE;
        this.lastRequestAt = 0;
      }
      this.emitStatus();
    }

    onFragmentLoaded(frag) {
      if (frag && Number.isInteger(frag.sn) && frag.sn >= 0) {
        this.lastLoadedSn = Math.max(this.lastLoadedSn, frag.sn);
      }
      return this.telemetry.recordFragmentLoad(frag);
    }

    shouldRequestDecision({ nowMs, throughputKbps, bufferS, force = false }) {
      if (!this.enabled || !this.videoId || this.manualOverrideActive) return false;
      if (this.inFlight) return false;
      if (!Number.isFinite(throughputKbps) || throughputKbps <= 0) return false;

      if (this.mode === ADAPTATION_MODES.FALLBACK) {
        if (!force && nowMs - this.lastFailureAt < this.fallbackRetryMs) {
          return false;
        }
      }

      if (force || !this.decisionPlan) return true;

      if (nowMs - this.lastRequestAt >= this.requestIntervalMs) {
        return true;
      }

      if (this.lastRequestThroughput && this.lastRequestThroughput > 0) {
        const relDelta = Math.abs(throughputKbps - this.lastRequestThroughput) / this.lastRequestThroughput;
        if (relDelta >= this.throughputDeltaThreshold) {
          return true;
        }
      }

      if (this.lastRequestBuffer !== null) {
        if (Math.abs(bufferS - this.lastRequestBuffer) >= this.bufferDeltaThresholdS) {
          return true;
        }
        if (bufferS < 5.0 && this.lastRequestBuffer >= 5.0) {
          return true;
        }
      }

      return false;
    }

    /**
     * Resolve the upcoming segment index based on buffered playback position and last loaded fragment.
     */
    getUpcomingSegmentIndex(currentTime, bufferedAheadS) {
      if (!this.decisionPlan || !Array.isArray(this.decisionPlan.adaptedSegments)) {
        return null;
      }
      const segs = this.decisionPlan.adaptedSegments;
      if (segs.length === 0) return null;

      // Prefer next unbuffered time position, bounded by lastLoadedSn + 1
      const targetTime = Math.max(0, (Number(currentTime) || 0) + Math.max(0, Number(bufferedAheadS) || 0));
      let timeIdx = segs.length - 1;
      for (let i = 0; i < segs.length; i += 1) {
        const start = Number(segs[i].playlist_start_time_sec || 0);
        const dur = Number(segs[i].duration_s || 0);
        if (targetTime < start + dur - 0.05) {
          timeIdx = segs[i].segment_index;
          break;
        }
      }

      if (this.lastLoadedSn >= 0) {
        const nextSn = Math.min(segs.length - 1, this.lastLoadedSn + 1);
        return Math.max(timeIdx, nextSn);
      }
      return timeIdx;
    }

    /**
     * Apply the backend decision corresponding to the upcoming segment index.
     */
    applySegmentDecision({ currentTime, bufferedAheadS, hlsLevels, nowMs = Date.now() }) {
      if (!this.enabled || this.manualOverrideActive || this.mode !== ADAPTATION_MODES.DECISION_ENGINE) {
        return null;
      }
      if (!this.decisionPlan || !Array.isArray(hlsLevels) || hlsLevels.length === 0) {
        return null;
      }

      const targetIdx = this.getUpcomingSegmentIndex(currentTime, bufferedAheadS);
      if (targetIdx === null) return null;

      const entry = this.decisionPlan.decisionsByIndex.get(targetIdx);
      if (!entry) return null;

      const { decision, segmentContext } = entry;
      const mappedLevel = mapRenditionToHlsLevel(
        decision.rendition_id,
        segmentContext ? segmentContext.renditions : null,
        hlsLevels
      );

      if (mappedLevel === null) {
        this.onLog(
          `Decision rendition '${decision.rendition_id}' for ${decision.segment_id} could not be mapped to hls.levels; restoring ABR.`
        );
        this.enterFallback(`Unmapped rendition '${decision.rendition_id}'`);
        return null;
      }

      // Stability / anti-oscillation guard: avoid redundant switches or rapid ping-ponging
      const changedRendition = this.lastSelectedRenditionId !== decision.rendition_id;
      if (
        changedRendition &&
        this.lastSelectedRenditionId !== null &&
        this.lastAppliedSegmentIndex !== targetIdx &&
        nowMs - this.lastSwitchAt < this.minSwitchingCooldownMs &&
        bufferedAheadS > 6.0
      ) {
        return null;
      }

      if (changedRendition || this.lastAppliedSegmentIndex !== targetIdx) {
        this.onApplyLevel(mappedLevel, decision, segmentContext, changedRendition);
        if (changedRendition) {
          this.lastSwitchAt = nowMs;
        }
        this.lastSelectedRenditionId = decision.rendition_id;
        this.lastAppliedSegmentIndex = targetIdx;
      }

      this.emitStatus({
        lastDecisionStatus: `Applied (${decision.segment_id} -> ${decision.rendition_id})`,
        lastExplanation: decision.explanation ? decision.explanation.human : "",
        lastDecision: decision,
        segmentContext,
      });

      return { segmentIndex: targetIdx, levelIndex: mappedLevel, decision };
    }

    enterFallback(reason) {
      this.mode = ADAPTATION_MODES.FALLBACK;
      this.lastFailureAt = Date.now();
      this.decisionPlan = null;
      if (!this.manualOverrideActive) {
        this.onRestoreAbr(`Fallback to hls.js ABR (${reason})`);
      }
      this.emitStatus({
        lastDecisionStatus: `Fallback (${reason})`,
      });
    }

    emitStatus(extra = {}) {
      this.onStatusUpdate({
        mode: this.mode,
        lastSelectedRenditionId: this.lastSelectedRenditionId,
        ...extra,
      });
    }

    async requestDecisions({
      throughputKbps,
      throughputVariance,
      bufferS,
      minBufferS = 5.0,
      maxBufferS = 30.0,
      currentTime = 0,
      hlsLevels = [],
    }) {
      const requestVideoId = this.videoId;
      const requestToken = this.videoToken;
      if (!this.enabled || !requestVideoId || this.inFlight) return null;

      let payload;
      try {
        payload = buildDecisionPayload({
          throughputKbps,
          throughputVariance,
          safetyMargin: 0.8,
          bufferS,
          minBufferS,
          maxBufferS,
          lastRenditionId: this.lastSelectedRenditionId,
          preferenceMode: this.preferenceMode,
        });
      } catch (err) {
        this.onLog(`Skipped decision request: ${err.message}`);
        return null;
      }

      this.inFlight = true;
      this.lastRequestAt = Date.now();
      this.lastRequestThroughput = throughputKbps;
      this.lastRequestBuffer = bufferS;

      const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      this.abortController = controller;
      const timeoutHandle =
        controller && this.requestTimeoutMs > 0
          ? setTimeout(() => controller.abort(), this.requestTimeoutMs)
          : null;

      try {
        const res = await this.fetchFn(
          `${this.apiBase}/api/videos/${encodeURIComponent(requestVideoId)}/decisions`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller ? controller.signal : undefined,
          }
        );

        if (requestToken !== this.videoToken || !this.enabled) {
          return null;
        }

        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try {
            const errJson = await res.json();
            if (errJson && errJson.detail) detail = errJson.detail;
          } catch (_) {}
          throw new Error(detail);
        }

        const raw = await res.json();
        if (requestToken !== this.videoToken || !this.enabled) {
          return null;
        }

        const validated = validateDecisionResponse(raw, requestVideoId);
        const adaptedSegments = Array.isArray(validated.adapted_segments) ? validated.adapted_segments : [];
        const contextById = new Map();
        for (const ctx of adaptedSegments) {
          contextById.set(ctx.segment_id, ctx);
        }

        const decisionsByIndex = new Map();
        validated.decisions.forEach((dec, idx) => {
          const ctx = contextById.get(dec.segment_id) || adaptedSegments[idx] || null;
          const segIdx = ctx && Number.isInteger(ctx.segment_index) ? ctx.segment_index : idx;
          decisionsByIndex.set(segIdx, { decision: dec, segmentContext: ctx });
        });

        this.decisionPlan = {
          decisionsByIndex,
          adaptedSegments,
          rawResponse: validated,
        };
        this.mode = ADAPTATION_MODES.DECISION_ENGINE;

        this.applySegmentDecision({
          currentTime,
          bufferedAheadS: bufferS,
          hlsLevels,
          nowMs: Date.now(),
        });

        return validated;
      } catch (err) {
        if (requestToken !== this.videoToken || !this.enabled) {
          return null;
        }
        const message = err && err.name === "AbortError" ? "Decision API request timed out" : err.message;
        this.onLog(`Decision API error: ${message}. Restoring hls.js ABR.`);
        this.enterFallback(message);
        return null;
      } finally {
        if (timeoutHandle) clearTimeout(timeoutHandle);
        if (this.abortController === controller) {
          this.abortController = null;
        }
        this.inFlight = false;
      }
    }
  }

  return {
    ADAPTATION_MODES,
    PREFERENCE_MODES,
    TelemetryTracker,
    AdaptiveDecisionController,
    mapRenditionToHlsLevel,
    getBufferedAheadSeconds,
    buildDecisionPayload,
    validateDecisionResponse,
  };
});
