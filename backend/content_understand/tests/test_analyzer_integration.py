"""Integration tests running the full ContentAnalyzer pipeline on 3 educational video samples."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.content_understand.analyzer import ContentAnalyzer, _parse_m3u8_segments
from backend.content_understand.config import AnalysisConfig, get_analysis_config
from backend.content_understand.models import SegmentInput, VideoAnalysisResult
from backend.content_understand.tests._synthetic_media import create_sample_educational_segments


class AnalyzerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage_dir = self.root / "storage"
        self.config = AnalysisConfig(storage_dir=self.storage_dir, frames_per_segment=2)
        self.analyzer = ContentAnalyzer(config=self.config)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_sample(self, video_id: str, sample_kind: str) -> VideoAnalysisResult:
        seg_dir = self.storage_dir / "processed" / video_id / "segments"
        create_sample_educational_segments(seg_dir, sample_kind=sample_kind, duration_sec=2.0)
        result = self.analyzer.analyze_video(video_id=video_id)

        meta_file = self.storage_dir / "processed" / video_id / "content_metadata.json"
        self.assertTrue(meta_file.is_file())
        parsed = VideoAnalysisResult.model_validate(json.loads(meta_file.read_text(encoding="utf-8")))
        self.assertEqual(parsed.total_segments, 3)
        self.assertEqual(len(parsed.segments), 3)
        return result

    def test_sample_1_lecture_slides_pipeline(self) -> None:
        result = self._run_sample("lecture_sample", "lecture_slides")
        blank_seg, slide1_seg, slide2_seg = result.segments

        # Slide segments should have higher complexity, text density, and importance than blank segment
        self.assertLess(blank_seg.analysis.visual_complexity, slide1_seg.analysis.visual_complexity)
        self.assertGreater(slide1_seg.analysis.text_density, 0.05)
        self.assertTrue(slide1_seg.analysis.has_significant_text)
        self.assertTrue(slide1_seg.analysis.scene_change_detected)
        self.assertGreater(slide1_seg.analysis.importance_score, blank_seg.analysis.importance_score)
        self.assertEqual(slide2_seg.start_time_sec, 4.0)
        self.assertEqual(slide2_seg.end_time_sec, 6.0)

    def test_sample_2_coding_tutorial_pipeline(self) -> None:
        result = self._run_sample("coding_sample", "coding_tutorial")
        blank_seg, code1_seg, code2_seg = result.segments

        self.assertGreater(code1_seg.analysis.visual_complexity, blank_seg.analysis.visual_complexity)
        self.assertGreater(code1_seg.analysis.text_density, 0.05)
        self.assertTrue(code1_seg.analysis.has_significant_text)
        self.assertTrue(code1_seg.analysis.scene_change_detected)
        self.assertGreater(code1_seg.analysis.importance_score, blank_seg.analysis.importance_score)
        self.assertGreater(code2_seg.analysis.importance_score, blank_seg.analysis.importance_score)

    def test_sample_3_animated_explainer_pipeline(self) -> None:
        result = self._run_sample("explainer_sample", "animated_explainer")
        seg0, seg1, seg2 = result.segments

        for seg in (seg0, seg1, seg2):
            self.assertGreater(seg.analysis.visual_complexity, 0.0)
            self.assertGreaterEqual(seg.analysis.importance_score, 0.0)
            self.assertLessEqual(seg.analysis.importance_score, 1.0)
            self.assertEqual(seg.frames_analyzed, 2)
        # Transition from light theme (seg0) to dark theme (seg1) should trigger scene change
        self.assertTrue(seg1.analysis.scene_change_detected)

    def test_missing_video_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.analyzer.analyze_video("nonexistent_video_id")

    def test_segment_input_is_json_serializable(self) -> None:
        seg = SegmentInput(
            segment_id="seg_000",
            segment_index=0,
            file_path=self.root / "seg_000.ts",
            start_time_sec=0.0,
            end_time_sec=2.0,
            duration_sec=2.0,
        )
        dumped = seg.model_dump()
        self.assertIsInstance(dumped["file_path"], str)
        self.assertEqual(json.loads(json.dumps(dumped))["segment_id"], "seg_000")

    def test_empty_frames_guard_and_limits(self) -> None:
        seg = SegmentInput(
            segment_id="seg_000",
            segment_index=0,
            file_path=self.root / "seg_000.ts",
            start_time_sec=0.0,
            end_time_sec=2.0,
            duration_sec=2.0,
        )
        with patch("backend.content_understand.analyzer.extract_frames", return_value=[]):
            with self.assertRaises(RuntimeError):
                self.analyzer.analyze_segment("vid", seg, self.root / "frames")

        limited = ContentAnalyzer(config=AnalysisConfig(storage_dir=self.storage_dir, max_segments=1))
        with self.assertRaises(ValueError):
            limited.analyze_segments("vid", [seg, seg.model_copy(update={"segment_index": 1, "segment_id": "seg_001"})])

        with patch("backend.content_understand.analyzer.time.monotonic", side_effect=[0.0, 10.0]):
            with self.assertRaises(TimeoutError):
                self.analyzer.analyze_segments("vid", [seg], save_output=False, total_timeout_sec=1.0)

    def test_m3u8_missing_segment_logs_warning(self) -> None:
        playlist = self.root / "playlist.m3u8"
        playlist.write_text("#EXTM3U\n#EXTINF:2.0,\nmissing_seg.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        with self.assertLogs("backend.content_understand.analyzer", level="WARNING") as logs:
            entries = _parse_m3u8_segments(playlist)
        self.assertEqual(entries, [])
        self.assertTrue(any("missing_seg.ts" in msg for msg in logs.output))

    def test_get_analysis_config_reflects_runtime_env_changes(self) -> None:
        with patch.dict(os.environ, {"TESSERACT_CMD": "custom_tesseract_a"}):
            cfg_a = get_analysis_config()
        with patch.dict(os.environ, {"TESSERACT_CMD": "custom_tesseract_b"}):
            cfg_b = get_analysis_config()
        self.assertEqual(cfg_a.tesseract_cmd, "custom_tesseract_a")
        self.assertEqual(cfg_b.tesseract_cmd, "custom_tesseract_b")

    def test_master_playlist_prefers_highest_rendition_and_preserves_missing_offsets(self) -> None:
        vid_dir = self.storage_dir / "processed" / "multi_hls"
        dir_360 = vid_dir / "360p"
        dir_720 = vid_dir / "720p"
        create_sample_educational_segments(dir_360, sample_kind="lecture_slides", duration_sec=2.0)
        create_sample_educational_segments(dir_720, sample_kind="coding_tutorial", duration_sec=2.0)

        master = vid_dir / "master.m3u8"
        master.write_text(
            "\n".join(
                [
                    "#EXTM3U",
                    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360",
                    "360p/playlist.m3u8",
                    "#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720",
                    "720p/playlist.m3u8",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        discovered = self.analyzer.discover_segments("multi_hls")
        self.assertEqual(len(discovered), 3)
        self.assertTrue(all("720p" in str(seg.file_path) for seg in discovered))

        # Removing seg_001.ts from 720p playlist should preserve seg_002's 4.0..6.0 timeline offset
        (dir_720 / "seg_001.ts").unlink()
        with self.assertLogs("backend.content_understand.analyzer", level="WARNING"):
            discovered_gap = self.analyzer.discover_segments("multi_hls")
        self.assertEqual(len(discovered_gap), 2)
        self.assertEqual(discovered_gap[0].start_time_sec, 0.0)
        self.assertEqual(discovered_gap[1].start_time_sec, 4.0)
        self.assertEqual(discovered_gap[1].end_time_sec, 6.0)

    def test_security_and_validation_guards(self) -> None:
        with self.assertRaises(ValueError):
            self.analyzer.discover_segments("../escape")

        bad_playlist = self.root / "bad.m3u8"
        bad_playlist.write_text("#EXTM3U\n#EXTINF:2.0,\n../secret.ts\n#EXT-X-ENDLIST\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            _parse_m3u8_segments(bad_playlist)

        seg_dir = self.storage_dir / "processed" / "vid_dup" / "segments"
        create_sample_educational_segments(seg_dir, sample_kind="lecture_slides", duration_sec=2.0)
        segments = self.analyzer.discover_segments("vid_dup")
        with self.assertRaises(ValueError):
            self.analyzer.analyze_segment("vid_dup", segments[0], self.root / "frames", frames_per_segment=0)

        dup_built = self.analyzer._build_segment_inputs(
            [(segments[0].file_path, 2.0), (segments[0].file_path, 2.0)]
        )
        self.assertEqual(len({s.segment_id for s in dup_built}), 2)

    def test_resolution_hint_ignores_parent_video_id_resolution_token(self) -> None:
        vid_dir = self.storage_dir / "processed" / "lecture_360p_demo"
        create_sample_educational_segments(vid_dir / "360p", sample_kind="lecture_slides", duration_sec=2.0)
        create_sample_educational_segments(vid_dir / "720p", sample_kind="coding_tutorial", duration_sec=2.0)

        discovered = self.analyzer.discover_segments("lecture_360p_demo")
        self.assertEqual(len(discovered), 3)
        self.assertTrue(all("720p" in str(seg.file_path) for seg in discovered))


if __name__ == "__main__":
    unittest.main()
