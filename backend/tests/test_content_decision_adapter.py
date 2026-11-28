"""Unit tests for ContentDecisionAdapter covering all 16 integration requirements."""

import unittest

from backend.app.modules.decision_engine.models import (
    NetworkState,
    PlayerState,
    PreferenceMode,
    SceneType,
    ViewerPreferences,
)
from backend.app.modules.integration import (
    DEFAULT_MOTION_FALLBACK,
    DEFAULT_SALIENCY_FALLBACK,
    ContentDecisionAdapter,
    IntegrationAdapterError,
    calculate_segment_bitrate_kbps,
    map_content_type_to_scene_type,
)
from backend.app.modules.video_processing.hls_schemas import (
    HLSFailedRenditionInfo,
    HLSRenditionInfo,
    HLSResponse,
    HLSSegmentInfo,
    HLSSkippedRenditionInfo,
)
from backend.content_understand.models import (
    SegmentAnalysis,
    SegmentMetadata as ContentSegmentMetadata,
    VideoAnalysisResult,
)


def _make_cu_segment(
    video_id: str,
    idx: int,
    start: float,
    dur: float,
    complexity: float = 0.5,
    text_density: float = 0.2,
    importance: float = 0.75,
    hint: str = "presentation_slide",
    scene_change: bool = False,
    text_sample: str = "Sample slide text",
    word_count: int = 3,
    has_text: bool = True,
) -> ContentSegmentMetadata:
    return ContentSegmentMetadata(
        video_id=video_id,
        segment_id=f"seg_{idx:03d}",
        segment_index=idx,
        start_time_sec=start,
        end_time_sec=start + dur,
        duration_sec=dur,
        analysis=SegmentAnalysis(
            visual_complexity=complexity,
            text_density=text_density,
            scene_change_detected=scene_change,
            detected_text_sample=text_sample,
            word_count=word_count,
            has_significant_text=has_text,
            importance_score=importance,
            content_type_hint=hint,
            ocr_engine="opencv_text_region",
        ),
        frames_analyzed=3,
        analysis_timestamp="2026-11-28T00:00:00Z",
    )


def _make_hls_rendition(
    name: str,
    width: int,
    height: int,
    sizes: list[int],
    durations: list[float],
    pts_origin: float = 1.466667,
) -> HLSRenditionInfo:
    segments = []
    cur_pts = pts_origin
    for i, (sz, dur) in enumerate(zip(sizes, durations)):
        segments.append(
            HLSSegmentInfo(
                filename=f"segment_{i:03d}.ts",
                duration=dur,
                file_size=sz,
                start_time=round(cur_pts, 6),
                starts_with_keyframe=True,
            )
        )
        cur_pts += dur
    total_dur = sum(durations)
    return HLSRenditionInfo(
        rendition=name,
        playlist_path=f"vid/hls/{name}/playlist.m3u8",
        playlist_url=f"/api/videos/vid/hls/{name}/playlist.m3u8",
        relative_playlist_uri=f"{name}/playlist.m3u8",
        width=width,
        height=height,
        resolution=f"{width}x{height}",
        bandwidth=900000,
        average_bandwidth=800000,
        codecs="avc1.64001f,mp4a.40.2",
        segment_count=len(segments),
        total_duration=total_dur,
        segments=segments,
    )


class ContentDecisionAdapterTestCase(unittest.TestCase):
    """Tests for ContentDecisionAdapter segment alignment, bitrate calculation, and feature mapping."""

    def setUp(self):
        self.video_id = "11111111-1111-1111-1111-111111111111"
        self.adapter = ContentDecisionAdapter()
        self.network = NetworkState(throughput_estimate_kbps=2500.0)
        self.player = PlayerState(buffer_s=12.0)

    def _base_analysis(self, durations=(4.0, 4.0, 1.0)) -> VideoAnalysisResult:
        segs = []
        t = 0.0
        for idx, d in enumerate(durations):
            segs.append(_make_cu_segment(self.video_id, idx, t, d, complexity=0.2 + 0.2 * idx))
            t += d
        return VideoAnalysisResult(video_id=self.video_id, total_segments=len(segs), segments=segs)

    def _base_hls(self, durations=(4.0, 4.0, 1.0), pts_origin=1.466667) -> HLSResponse:
        r360 = _make_hls_rendition("360p", 640, 360, [200_000, 220_000, 60_000], list(durations), pts_origin)
        r480 = _make_hls_rendition("480p", 854, 480, [400_000, 420_000, 110_000], list(durations), pts_origin)
        r720 = _make_hls_rendition("720p", 1280, 720, [800_000, 840_000, 220_000], list(durations), pts_origin)
        return HLSResponse(
            video_id=self.video_id,
            status="completed",
            overall_status="completed",
            master_playlist_path=f"{self.video_id}/hls/master.m3u8",
            master_playlist_url=f"/api/videos/{self.video_id}/hls/master.m3u8",
            segment_duration=4,
            renditions=[r360, r480, r720],
            skipped_renditions=[],
            failed_renditions=[],
            processing_duration=1.2,
        )

    def test_01_valid_analysis_and_hls_produce_decision_request(self):
        """1. Valid analysis and HLS metadata produce a valid DecisionRequest."""
        res = self.adapter.adapt(self._base_analysis(), self._base_hls(), self.network, self.player)
        req = res.decision_request
        self.assertEqual(res.video_id, self.video_id)
        self.assertEqual(len(req.segments), 3)
        self.assertEqual([s.index for s in req.segments], [0, 1, 2])
        self.assertEqual(len(req.segments[0].renditions), 3)
        self.assertAlmostEqual(req.segments[0].complexity, 0.2)

    def test_02_segment_count_mismatch_rejected(self):
        """2. Segment count mismatch between analysis and HLS is rejected."""
        analysis = self._base_analysis(durations=(4.0, 4.0))
        hls = self._base_hls(durations=(4.0, 4.0, 1.0))
        with self.assertRaises(IntegrationAdapterError) as ctx:
            self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertIn("segment count mismatch", str(ctx.exception).lower())

    def test_03_video_id_mismatch_rejected(self):
        """3. Video ID mismatch is rejected."""
        analysis = self._base_analysis()
        hls = self._base_hls().model_copy(update={"video_id": "22222222-2222-2222-2222-222222222222"})
        with self.assertRaises(IntegrationAdapterError) as ctx:
            self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertIn("video id mismatch", str(ctx.exception).lower())

    def test_04_duration_mismatch_rejected(self):
        """4. Duration mismatch exceeding tolerance is rejected."""
        analysis = self._base_analysis(durations=(4.0, 4.0, 1.0))
        hls = self._base_hls(durations=(4.0, 6.0, 1.0))
        with self.assertRaises(IntegrationAdapterError) as ctx:
            self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertIn("duration mismatch", str(ctx.exception).lower())

    def test_05_timeline_mismatch_detected(self):
        """5. Non-contiguous timeline or PTS jump is detected and rejected."""
        analysis = self._base_analysis(durations=(4.0, 4.0, 1.0))
        hls = self._base_hls(durations=(4.0, 4.0, 1.0))
        # Inject a PTS jump on segment 2 in all renditions
        for r in hls.renditions:
            r.segments[2] = r.segments[2].model_copy(update={"start_time": r.segments[2].start_time + 2.0})
        with self.assertRaises(IntegrationAdapterError) as ctx:
            self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertIn("pts", str(ctx.exception).lower())

    def test_06_timestamp_origin_differences_handled_correctly(self):
        """6. Different absolute timestamp origins (0.0 vs FFprobe PTS 1.466667 / 100.0) align cleanly."""
        analysis = self._base_analysis(durations=(4.0, 4.0, 1.0))
        hls = self._base_hls(durations=(4.0, 4.0, 1.0), pts_origin=100.466667)
        res = self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertEqual(res.adapted_segments[0].playlist_start_time_sec, 0.0)
        self.assertEqual(res.adapted_segments[0].hls_relative_start_time_sec, 0.0)
        self.assertAlmostEqual(res.adapted_segments[1].hls_relative_start_time_sec, 4.0)
        self.assertAlmostEqual(res.adapted_segments[2].hls_relative_start_time_sec, 8.0)

    def test_07_rendition_bitrate_calculations_correct(self):
        """7. Segment bitrate is calculated per segment using (file_size * 8) / (duration * 1000)."""
        # 200,000 bytes over 4.0s = 400.0 kbps; 60,000 bytes over 1.0s = 480.0 kbps
        self.assertAlmostEqual(calculate_segment_bitrate_kbps(200_000, 4.0), 400.0)
        res = self.adapter.adapt(self._base_analysis(), self._base_hls(), self.network, self.player)
        seg0_360 = res.decision_request.segments[0].renditions[0]
        seg2_360 = res.decision_request.segments[2].renditions[0]
        self.assertAlmostEqual(seg0_360.bitrate_kbps, 400.0)
        self.assertAlmostEqual(seg2_360.bitrate_kbps, 480.0)

    def test_08_renditions_sorted_by_calculated_bitrate(self):
        """8. Renditions are sorted strictly ascending by calculated bitrate even if resolution order differs."""
        analysis = self._base_analysis(durations=(4.0,))
        # Make 720p smaller in bytes than 480p for this segment, and supply 720p first
        r720 = _make_hls_rendition("720p", 1280, 720, [300_000], [4.0])  # 600 kbps
        r480 = _make_hls_rendition("480p", 854, 480, [450_000], [4.0])   # 900 kbps
        r360 = _make_hls_rendition("360p", 640, 360, [150_000], [4.0])   # 300 kbps
        hls = HLSResponse(
            video_id=self.video_id,
            status="completed",
            overall_status="completed",
            master_playlist_path=f"{self.video_id}/hls/master.m3u8",
            segment_duration=4,
            renditions=[r720, r480, r360],
            processing_duration=1.0,
        )
        res = self.adapter.adapt(analysis, hls, self.network, self.player)
        ids = [r.id for r in res.decision_request.segments[0].renditions]
        bitrates = [r.bitrate_kbps for r in res.decision_request.segments[0].renditions]
        self.assertEqual(ids, ["360p", "720p", "480p"])
        self.assertEqual(bitrates, sorted(bitrates))

    def test_09_failed_and_skipped_renditions_excluded(self):
        """9. Failed and skipped HLS renditions are excluded and recorded in excluded_renditions."""
        analysis = self._base_analysis(durations=(4.0,))
        r360 = _make_hls_rendition("360p", 640, 360, [200_000], [4.0])
        hls = HLSResponse(
            video_id=self.video_id,
            status="partial",
            overall_status="partial",
            master_playlist_path=f"{self.video_id}/hls/master.m3u8",
            segment_duration=4,
            renditions=[r360],
            skipped_renditions=[HLSSkippedRenditionInfo(rendition="720p", reason="Upscaling prohibited")],
            failed_renditions=[HLSFailedRenditionInfo(rendition="480p", error="Corrupted MP4")],
            processing_duration=1.0,
        )
        res = self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertEqual([r.id for r in res.decision_request.segments[0].renditions], ["360p"])
        self.assertEqual(res.excluded_renditions["skipped"][0]["rendition"], "720p")
        self.assertEqual(res.excluded_renditions["failed"][0]["rendition"], "480p")

    def test_10_invalid_segment_sizes_and_durations_rejected(self):
        """10. Invalid segment file sizes or non-positive durations are rejected."""
        with self.assertRaises(IntegrationAdapterError):
            calculate_segment_bitrate_kbps(0, 4.0)
        with self.assertRaises(IntegrationAdapterError):
            calculate_segment_bitrate_kbps(1000, 0.0)

        analysis = self._base_analysis(durations=(4.0,))
        hls = self._base_hls(durations=(4.0,))
        hls.renditions[0].segments[0] = hls.renditions[0].segments[0].model_copy(update={"file_size": 0})
        with self.assertRaises(IntegrationAdapterError):
            self.adapter.adapt(analysis, hls, self.network, self.player)

    def test_11_missing_motion_and_saliency_use_documented_fallbacks(self):
        """11. Missing motion and saliency use 0.5 fallbacks and never copy importance_score."""
        analysis = VideoAnalysisResult(
            video_id=self.video_id,
            total_segments=1,
            segments=[_make_cu_segment(self.video_id, 0, 0.0, 4.0, complexity=0.33, importance=0.97)],
        )
        hls = self._base_hls(durations=(4.0,))
        res = self.adapter.adapt(analysis, hls, self.network, self.player)
        seg = res.decision_request.segments[0]
        self.assertEqual(seg.motion, DEFAULT_MOTION_FALLBACK)
        self.assertEqual(seg.saliency, DEFAULT_SALIENCY_FALLBACK)
        self.assertNotEqual(seg.saliency, 0.97)
        self.assertTrue(res.adapted_segments[0].fallback_fields["motion"].is_fallback)
        self.assertTrue(res.adapted_segments[0].fallback_fields["saliency"].is_fallback)

    def test_12_face_presence_remains_none(self):
        """12. face_presence remains None and is documented as fallback."""
        res = self.adapter.adapt(self._base_analysis(), self._base_hls(), self.network, self.player)
        for seg in res.decision_request.segments:
            self.assertIsNone(seg.face_presence)
        self.assertIsNone(res.adapted_segments[0].fallback_fields["face_presence"].value)

    def test_13_conservative_scene_classification_mapping(self):
        """13. Conservative scene mapping ignores scene_change_detected->TRANSITION."""
        hints_expected = [
            ("code_screen", SceneType.STATIC),
            ("presentation_slide", SceneType.STATIC),
            ("diagram_or_whiteboard", SceneType.STATIC),
            ("dynamic_scene", SceneType.OTHER),
            ("talking_head_or_simple", SceneType.OTHER),
            ("unknown", SceneType.OTHER),
            ("future_unseen_hint", SceneType.OTHER),
        ]
        for hint, expected in hints_expected:
            self.assertEqual(map_content_type_to_scene_type(hint), expected)

        # scene_change_detected=True must NOT force SceneType.TRANSITION
        cu_seg = _make_cu_segment(self.video_id, 0, 0.0, 4.0, hint="code_screen", scene_change=True)
        res = self.adapter.adapt(
            VideoAnalysisResult(video_id=self.video_id, total_segments=1, segments=[cu_seg]),
            self._base_hls(durations=(4.0,)),
            self.network,
            self.player,
        )
        self.assertEqual(res.decision_request.segments[0].scene_type, SceneType.STATIC)
        self.assertTrue(res.adapted_segments[0].scene_change_detected)

    def test_14_educational_metadata_preserved(self):
        """14. Educational signals are preserved in adapted_segments without alteration."""
        cu_seg = _make_cu_segment(
            self.video_id, 0, 0.0, 4.0,
            complexity=0.42, text_density=0.31, importance=0.88,
            hint="code_screen", scene_change=True, text_sample="def solve():", word_count=7, has_text=True,
        )
        res = self.adapter.adapt(
            VideoAnalysisResult(video_id=self.video_id, total_segments=1, segments=[cu_seg]),
            self._base_hls(durations=(4.0,)),
            self.network,
            self.player,
        )
        ctx = res.adapted_segments[0]
        self.assertAlmostEqual(ctx.visual_complexity, 0.42)
        self.assertAlmostEqual(ctx.text_density, 0.31)
        self.assertAlmostEqual(ctx.importance_score, 0.88)
        self.assertTrue(ctx.has_significant_text)
        self.assertEqual(ctx.detected_text_sample, "def solve():")
        self.assertEqual(ctx.word_count, 7)
        self.assertEqual(ctx.content_type_hint, "code_screen")
        self.assertEqual(ctx.mapped_scene_type, "static")

    def test_15_single_rendition_video_works(self):
        """15. Single-rendition HLS video adapts cleanly and passes Decision Engine allocation."""
        analysis = self._base_analysis(durations=(4.0, 2.0))
        r360 = _make_hls_rendition("360p", 640, 360, [200_000, 100_000], [4.0, 2.0])
        hls = HLSResponse(
            video_id=self.video_id,
            status="completed",
            overall_status="completed",
            master_playlist_path=f"{self.video_id}/hls/master.m3u8",
            segment_duration=4,
            renditions=[r360],
            processing_duration=1.0,
        )
        res = self.adapter.adapt(analysis, hls, self.network, self.player, ViewerPreferences(PreferenceMode.BALANCED))
        self.assertEqual(len(res.decision_request.segments[0].renditions), 1)
        self.assertEqual(res.decision_request.segments[0].renditions[0].id, "360p")

    def test_16_lower_resolution_source_video_works(self):
        """16. Lower-resolution source (only 360p/480p, 720p skipped) works accurately."""
        analysis = self._base_analysis(durations=(4.0,))
        r360 = _make_hls_rendition("360p", 640, 360, [200_000], [4.0])
        r480 = _make_hls_rendition("480p", 854, 480, [400_000], [4.0])
        hls = HLSResponse(
            video_id=self.video_id,
            status="completed",
            overall_status="completed",
            master_playlist_path=f"{self.video_id}/hls/master.m3u8",
            segment_duration=4,
            renditions=[r360, r480],
            skipped_renditions=[HLSSkippedRenditionInfo(rendition="720p", reason="Source height 480p < 720p")],
            processing_duration=1.0,
        )
        res = self.adapter.adapt(analysis, hls, self.network, self.player)
        self.assertEqual([r.id for r in res.decision_request.segments[0].renditions], ["360p", "480p"])
        self.assertEqual(res.excluded_renditions["skipped"][0]["rendition"], "720p")


if __name__ == "__main__":
    unittest.main()
