"""API integration tests for POST /api/videos/{video_id}/decisions."""

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app
from backend.app.modules.video_processing.transcoder import get_ffmpeg_executable
from backend.content_understand.models import (
    SegmentAnalysis,
    SegmentMetadata as ContentSegmentMetadata,
    VideoAnalysisResult,
)


def find_ffmpeg() -> str | None:
    try:
        return get_ffmpeg_executable()
    except Exception:
        return None


class IntegratedDecisionsAPITestCase(unittest.TestCase):
    """End-to-end API integration tests linking upload -> transcode -> HLS -> analysis -> decisions."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()
        cls.mp4_5s_bytes = b""

        if cls.ffmpeg_bin:
            fixture = cls.temp_path / "decisions_fixture.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=5:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=5",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "5",
                    str(fixture), "-y",
                ],
                check=True,
                timeout=30,
            )
            cls.mp4_5s_bytes = fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp(dir=self.temp_path))
        self.test_settings = Settings(
            storage_dir=str(self.run_dir),
            max_upload_size_mb=500,
            ffmpeg_path=self.ffmpeg_bin,
            transcoding_timeout_seconds=60,
            hls_segment_duration_seconds=4,
            hls_timeout_seconds=60,
        )
        app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _upload_transcode_hls_and_analyze(self) -> str:
        if not self.mp4_5s_bytes:
            self.skipTest("FFmpeg unavailable.")
        up = self.client.post(
            "/api/videos/upload",
            files={"file": ("lecture.mp4", io.BytesIO(self.mp4_5s_bytes), "video/mp4")},
        )
        self.assertEqual(up.status_code, 200, up.text)
        vid = up.json()["video_id"]
        self.assertEqual(self.client.post(f"/api/videos/{vid}/transcode").status_code, 200)
        self.assertEqual(self.client.post(f"/api/videos/{vid}/hls").status_code, 200)
        self.assertEqual(self.client.post(f"/analysis/analyze/{vid}").status_code, 200)
        return vid

    def test_01_successful_integrated_decision_request(self):
        """1. Full pipeline produces valid content-aware segment decisions via POST /api/videos/{id}/decisions."""
        vid = self._upload_transcode_hls_and_analyze()

        payload = {
            "network": {"throughput_estimate_kbps": 1800.0, "safety_margin": 0.8},
            "player": {"buffer_s": 12.0, "min_buffer_s": 5.0, "max_buffer_s": 30.0},
            "viewer": {"mode": "balanced"},
        }
        res = self.client.post(f"/api/videos/{vid}/decisions", json=payload)
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["video_id"], vid)
        self.assertEqual(len(data["decisions"]), 2)
        self.assertIn("window_budget_kbit", data)
        self.assertIn("used_kbit", data)
        self.assertIn("predicted_min_buffer_s", data)
        self.assertIn("constraints_binding", data)
        self.assertLessEqual(data["used_kbit"], data["window_budget_kbit"] + 1e-6)

        for dec, seg_ctx in zip(data["decisions"], data["adapted_segments"]):
            self.assertIn(dec["rendition_id"], {"360p", "480p", "720p"})
            self.assertIn("explanation", dec)
            self.assertIn("human", dec["explanation"])
            self.assertIn("importance_score", seg_ctx)
            self.assertIn("text_density", seg_ctx)
            self.assertEqual(seg_ctx["fallback_fields"]["motion"]["value"], 0.5)
            self.assertEqual(seg_ctx["fallback_fields"]["saliency"]["value"], 0.5)
            self.assertIsNone(seg_ctx["fallback_fields"]["face_presence"]["value"])

        self.assertEqual(
            set(data["integration_metadata"]["fallback_fields"]),
            {"motion", "saliency", "face_presence"},
        )

    def test_02_missing_analysis_returns_404(self):
        """2. Missing content analysis metadata returns 404 explaining the prerequisite."""
        if not self.mp4_5s_bytes:
            self.skipTest("FFmpeg unavailable.")
        up = self.client.post(
            "/api/videos/upload",
            files={"file": ("no_analysis.mp4", io.BytesIO(self.mp4_5s_bytes), "video/mp4")},
        )
        vid = up.json()["video_id"]
        self.client.post(f"/api/videos/{vid}/transcode")
        self.client.post(f"/api/videos/{vid}/hls")

        res = self.client.post(
            f"/api/videos/{vid}/decisions",
            json={"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": 10}},
        )
        self.assertEqual(res.status_code, 404)
        self.assertIn("analysis", res.json()["detail"].lower())

    def test_03_missing_hls_metadata_returns_404(self):
        """3. Missing HLS metadata returns 404 explaining the prerequisite."""
        vid = "33333333-3333-3333-3333-333333333333"
        video_dir = self.run_dir / "processed" / vid
        video_dir.mkdir(parents=True, exist_ok=True)
        # Write valid analysis metadata without HLS packaging
        analysis = VideoAnalysisResult(
            video_id=vid,
            total_segments=1,
            segments=[
                ContentSegmentMetadata(
                    video_id=vid,
                    segment_id="seg_000",
                    segment_index=0,
                    start_time_sec=0.0,
                    end_time_sec=4.0,
                    duration_sec=4.0,
                    analysis=SegmentAnalysis(
                        visual_complexity=0.5,
                        text_density=0.1,
                        scene_change_detected=False,
                        has_significant_text=False,
                        importance_score=0.4,
                        content_type_hint="talking_head_or_simple",
                    ),
                    frames_analyzed=3,
                    analysis_timestamp="2026-11-28T00:00:00Z",
                )
            ],
        )
        (video_dir / "content_metadata.json").write_text(analysis.model_dump_json(), encoding="utf-8")

        res = self.client.post(
            f"/api/videos/{vid}/decisions",
            json={"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": 10}},
        )
        self.assertEqual(res.status_code, 404)
        self.assertIn("hls", res.json()["detail"].lower())

    def test_04_invalid_adapter_input_returns_422(self):
        """4. Adapter mismatch (e.g. mismatched segment count between analysis and HLS) returns 422."""
        vid = self._upload_transcode_hls_and_analyze()
        # Overwrite content_metadata.json with mismatched segment count
        bad_analysis = VideoAnalysisResult(
            video_id=vid,
            total_segments=1,
            segments=[
                ContentSegmentMetadata(
                    video_id=vid,
                    segment_id="seg_000",
                    segment_index=0,
                    start_time_sec=0.0,
                    end_time_sec=4.0,
                    duration_sec=4.0,
                    analysis=SegmentAnalysis(
                        visual_complexity=0.5,
                        text_density=0.1,
                        scene_change_detected=False,
                        has_significant_text=False,
                        importance_score=0.4,
                        content_type_hint="code_screen",
                    ),
                    frames_analyzed=3,
                    analysis_timestamp="2026-11-28T00:00:00Z",
                )
            ],
        )
        (self.run_dir / "processed" / vid / "content_metadata.json").write_text(
            bad_analysis.model_dump_json(), encoding="utf-8"
        )

        res = self.client.post(
            f"/api/videos/{vid}/decisions",
            json={"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": 10}},
        )
        self.assertEqual(res.status_code, 422)
        self.assertIn("segment count mismatch", res.json()["detail"].lower())

    def test_05_invalid_network_player_state_and_bad_video_id(self):
        """5. Invalid video_id returns 400 and invalid network/player state returns 422."""
        self.assertEqual(
            self.client.post(
                "/api/videos/not-a-uuid/decisions",
                json={"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": 10}},
            ).status_code,
            400,
        )

        vid = "44444444-4444-4444-4444-444444444444"
        bad_payloads = [
            {"network": {"throughput_estimate_kbps": 0}, "player": {"buffer_s": 10}},
            {"network": {"throughput_estimate_kbps": 1500, "safety_margin": 1.5}, "player": {"buffer_s": 10}},
            {"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": -1}},
            {"network": {"throughput_estimate_kbps": 1500}, "player": {"buffer_s": 10}, "viewer": {"mode": "invalid_mode"}},
        ]
        for bad in bad_payloads:
            res = self.client.post(f"/api/videos/{vid}/decisions", json=bad)
            self.assertEqual(res.status_code, 422, f"Expected 422 for {bad}")

    def test_06_existing_decide_endpoint_remains_functional(self):
        """6. Existing standalone /decide, /health, and /schema endpoints remain functional."""
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/schema").status_code, 200)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/analysis/health").status_code, 200)

        standalone_payload = {
            "segments": [
                {
                    "segment_id": "s0",
                    "index": 0,
                    "duration_s": 4.0,
                    "complexity": 0.6,
                    "motion": 0.5,
                    "saliency": 0.5,
                    "scene_type": "static",
                    "renditions": [
                        {"id": "360p", "bitrate_kbps": 500, "height": 360, "width": 640},
                        {"id": "720p", "bitrate_kbps": 1500, "height": 720, "width": 1280},
                    ],
                }
            ],
            "network": {"throughput_estimate_kbps": 2000},
            "player": {"buffer_s": 12},
        }
        res = self.client.post("/decide", json=standalone_payload)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()["decisions"]), 1)


if __name__ == "__main__":
    unittest.main()
