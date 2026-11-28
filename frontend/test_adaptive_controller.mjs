import test from "node:test";
import assert from "node:assert/strict";
import "./adaptive_controller.js";

const {
  ADAPTATION_MODES,
  TelemetryTracker,
  AdaptiveDecisionController,
  mapRenditionToHlsLevel,
  getBufferedAheadSeconds,
  buildDecisionPayload,
  validateDecisionResponse,
} = globalThis.StreamWiseAdaptive;

test("1. mapRenditionToHlsLevel matches by dimensions regardless of level ordering", () => {
  // Out-of-order hls.levels (720p first, 360p second, 480p third)
  const hlsLevels = [
    { width: 1280, height: 720, bitrate: 1500000, url: ["http://localhost:8000/api/videos/v/hls/720p/playlist.m3u8"] },
    { width: 640, height: 360, bitrate: 450000, url: ["http://localhost:8000/api/videos/v/hls/360p/playlist.m3u8"] },
    { width: 854, height: 480, bitrate: 850000, url: ["http://localhost:8000/api/videos/v/hls/480p/playlist.m3u8"] },
  ];
  const segmentRenditions = [
    { id: "360p", width: 640, height: 360, bitrate_kbps: 450 },
    { id: "480p", width: 854, height: 480, bitrate_kbps: 850 },
    { id: "720p", width: 1280, height: 720, bitrate_kbps: 1500 },
  ];

  assert.equal(mapRenditionToHlsLevel("360p", segmentRenditions, hlsLevels), 1);
  assert.equal(mapRenditionToHlsLevel("480p", segmentRenditions, hlsLevels), 2);
  assert.equal(mapRenditionToHlsLevel("720p", segmentRenditions, hlsLevels), 0);
  assert.equal(mapRenditionToHlsLevel("1080p", segmentRenditions, hlsLevels), null);
});

test("2. TelemetryTracker computes throughput and variance from fragment stats without fabrication", () => {
  const tracker = new TelemetryTracker(5);
  // Initially unavailable when no fragment, no hls estimate, no navigator.connection
  assert.equal(tracker.getThroughputEstimate(null, null).kbps, null);

  // 200,000 bytes in 1000ms => 1600 kbps
  const sample1 = tracker.recordFragmentLoad({
    stats: { loaded: 200000, loading: { start: 100, end: 1100 } },
  });
  assert.equal(sample1, 1600);

  // 300,000 bytes in 1000ms => 2400 kbps
  const sample2 = tracker.recordFragmentLoad({
    stats: { loaded: 300000, loading: { start: 1200, end: 2200 } },
  });
  assert.equal(sample2, 2400);

  const est = tracker.getThroughputEstimate(2000000, null);
  assert.ok(Math.abs(est.kbps - 2000) < 0.01);
  assert.ok(est.variance > 0);
  assert.equal(est.source, "hls_ewma+fragments");
});

test("3. buildDecisionPayload enforces backend schema and preference modes", () => {
  const payload = buildDecisionPayload({
    throughputKbps: 1850.5,
    throughputVariance: 120.25,
    bufferS: 10.5,
    lastRenditionId: "480p",
    preferenceMode: "data_saver",
  });
  assert.deepEqual(payload, {
    network: {
      throughput_estimate_kbps: 1850.5,
      throughput_variance: 120.25,
      safety_margin: 0.8,
    },
    player: {
      buffer_s: 10.5,
      min_buffer_s: 5,
      max_buffer_s: 30,
      last_rendition_id: "480p",
    },
    viewer: {
      mode: "data_saver",
    },
  });

  assert.throws(() => buildDecisionPayload({ throughputKbps: 0, bufferS: 5 }));
});

test("4. Segment-level decisions apply at upcoming segment boundaries and prevent concurrent overlap", async () => {
  const applied = [];
  const restored = [];
  let resolveFetch;
  let fetchCalls = 0;

  const hlsLevels = [
    { width: 640, height: 360, bitrate: 400000, url: ["360p/playlist.m3u8"] },
    { width: 854, height: 480, bitrate: 800000, url: ["480p/playlist.m3u8"] },
    { width: 1280, height: 720, bitrate: 1600000, url: ["720p/playlist.m3u8"] },
  ];

  const sampleResponse = {
    video_id: "vid-1",
    decisions: [
      { segment_id: "seg_000", rendition_id: "360p", bitrate_kbps: 400, explanation: { human: "Seg 0 static" } },
      { segment_id: "seg_001", rendition_id: "720p", bitrate_kbps: 1600, explanation: { human: "Seg 1 complex" } },
    ],
    adapted_segments: [
      {
        segment_id: "seg_000",
        segment_index: 0,
        duration_s: 4.0,
        playlist_start_time_sec: 0.0,
        renditions: [
          { id: "360p", width: 640, height: 360, bitrate_kbps: 400 },
          { id: "480p", width: 854, height: 480, bitrate_kbps: 800 },
          { id: "720p", width: 1280, height: 720, bitrate_kbps: 1600 },
        ],
      },
      {
        segment_id: "seg_001",
        segment_index: 1,
        duration_s: 4.0,
        playlist_start_time_sec: 4.0,
        renditions: [
          { id: "360p", width: 640, height: 360, bitrate_kbps: 400 },
          { id: "480p", width: 854, height: 480, bitrate_kbps: 800 },
          { id: "720p", width: 1280, height: 720, bitrate_kbps: 1600 },
        ],
      },
    ],
  };

  const controller = new AdaptiveDecisionController({
    fetchFn: () => {
      fetchCalls += 1;
      return new Promise((resolve) => {
        resolveFetch = resolve;
      });
    },
    apiBase: "http://127.0.0.1:8000",
    minSwitchingCooldownMs: 0,
    onApplyLevel: (lvl, dec) => applied.push({ lvl, seg: dec.segment_id, rendition: dec.rendition_id }),
    onRestoreAbr: (reason) => restored.push(reason),
  });

  controller.resetForVideo("vid-1");
  controller.onFragmentLoaded({ sn: 0, stats: { loaded: 200000, loading: { start: 0, end: 1000 } } });

  // Launch first request; verify concurrent request is blocked while inFlight
  const p1 = controller.requestDecisions({
    throughputKbps: 1600,
    throughputVariance: 0,
    bufferS: 4.0,
    currentTime: 0.5,
    hlsLevels,
  });
  assert.equal(controller.inFlight, true);
  assert.equal(
    controller.shouldRequestDecision({ nowMs: Date.now(), throughputKbps: 1600, bufferS: 4.0, force: true }),
    false
  );

  resolveFetch({ ok: true, json: async () => sampleResponse });
  await p1;

  assert.equal(fetchCalls, 1);
  assert.equal(controller.inFlight, false);
  // Since sn=0 already loaded, upcoming segment is index 1 -> 720p (hls level 2)
  assert.deepEqual(applied, [{ lvl: 2, seg: "seg_001", rendition: "720p" }]);
  assert.equal(restored.length, 0);
});

test("5. API failure restores normal ABR fallback; disabling restores ABR; stale video ignored", async () => {
  const restored = [];
  let modeObserved = null;
  let resolveSlow;

  const controller = new AdaptiveDecisionController({
    fetchFn: () => new Promise((resolve) => { resolveSlow = resolve; }),
    apiBase: "http://127.0.0.1:8000",
    onApplyLevel: () => {},
    onRestoreAbr: (reason) => restored.push(reason),
    onStatusUpdate: (s) => { modeObserved = s.mode; },
  });

  // Stale video guard: start request for vid-A, switch to vid-B before vid-A resolves
  controller.resetForVideo("vid-A");
  const stalePromise = controller.requestDecisions({
    throughputKbps: 1500,
    throughputVariance: 0,
    bufferS: 8.0,
    hlsLevels: [{ width: 640, height: 360 }],
  });
  controller.resetForVideo("vid-B");
  resolveSlow({
    ok: true,
    json: async () => ({
      video_id: "vid-A",
      decisions: [{ segment_id: "seg_000", rendition_id: "360p" }],
      adapted_segments: [],
    }),
  });
  const staleResult = await stalePromise;
  assert.equal(staleResult, null);
  assert.equal(controller.decisionPlan, null);

  // API 500 error triggers fallback and restores ABR
  controller.fetchFn = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ detail: "Internal server error" }),
  });
  await controller.requestDecisions({
    throughputKbps: 1500,
    throughputVariance: 0,
    bufferS: 8.0,
    hlsLevels: [{ width: 640, height: 360 }],
  });
  assert.equal(modeObserved, ADAPTATION_MODES.FALLBACK);
  assert.equal(restored.length, 1);

  // Disabling Decision Engine restores normal mode and ABR
  controller.setEnabled(false);
  assert.equal(modeObserved, ADAPTATION_MODES.NORMAL);
  assert.equal(restored.length, 2);
});

test("6. getBufferedAheadSeconds and validateDecisionResponse edge cases", () => {
  const fakeBuffered = {
    length: 1,
    start: () => 0,
    end: () => 8.5,
  };
  assert.equal(getBufferedAheadSeconds(fakeBuffered, 2.5), 6.0);
  assert.throws(() => validateDecisionResponse({ video_id: "other", decisions: [] }, "expected"));
});
