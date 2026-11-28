"""Tests for player serving routes and full upload -> HLS -> analysis -> decisions integration."""

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


def find_ffmpeg() -> str | None:
    try:
        return get_ffmpeg_executable()
    except Exception:
        return None


class PlayerDecisionIntegrationTestCase(unittest.TestCase):
    """Verify player static asset delivery, JS unit tests, and preference mode decisions."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()
        cls.mp4_9s_bytes = b""

        if cls.ffmpeg_bin:
            fixture = cls.temp_path / "player_decision_9s.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=9:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=9",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "9",
                    str(fixture), "-y",
                ],
                check=True,
                timeout=30,
            )
            cls.mp4_9s_bytes = fixture.read_bytes()

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

    def test_01_player_and_adaptive_controller_served_cleanly(self):
        """1. Verify GET /player and GET /adaptive_controller.js serve valid UI & controller code."""
        res_html = self.client.get("/player")
        self.assertEqual(res_html.status_code, 200)
        self.assertIn("text/html", res_html.headers["content-type"])
        self.assertIn("adaptive_controller.js", res_html.text)
        self.assertIn("adaptive-toggle", res_html.text)
        self.assertIn("preference-select", res_html.text)
        self.assertIn("quality_first", res_html.text)
        self.assertIn("data_saver", res_html.text)

        res_js = self.client.get("/adaptive_controller.js")
        self.assertEqual(res_js.status_code, 200)
        self.assertIn("javascript", res_js.headers["content-type"])
        self.assertIn("AdaptiveDecisionController", res_js.text)
        self.assertIn("mapRenditionToHlsLevel", res_js.text)

    def test_02_frontend_adaptive_controller_node_unit_tests(self):
        """2. Execute Node built-in unit tests for frontend/adaptive_controller.js."""
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("Node.js unavailable on PATH.")

        repo_root = Path(__file__).resolve().parent.parent.parent
        test_file = repo_root / "frontend" / "test_adaptive_controller.mjs"
        proc = subprocess.run(
            [node_bin, "--test", str(test_file)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    def test_03_player_decision_workflow_across_preference_modes(self):
        """3. Verify full upload -> transcode -> HLS -> analysis -> /decisions across preference modes."""
        if not self.mp4_9s_bytes:
            self.skipTest("FFmpeg unavailable.")

        up = self.client.post(
            "/api/videos/upload",
            files={"file": ("player_flow.mp4", io.BytesIO(self.mp4_9s_bytes), "video/mp4")},
        )
        self.assertEqual(up.status_code, 200, up.text)
        vid = up.json()["video_id"]

        self.assertEqual(self.client.post(f"/api/videos/{vid}/transcode").status_code, 200)
        self.assertEqual(self.client.post(f"/api/videos/{vid}/hls").status_code, 200)
        self.assertEqual(self.client.post(f"/analysis/analyze/{vid}").status_code, 200)

        for mode in ("quality_first", "balanced", "data_saver"):
            res = self.client.post(
                f"/api/videos/{vid}/decisions",
                json={
                    "network": {"throughput_estimate_kbps": 1500.0, "throughput_variance": 50.0, "safety_margin": 0.8},
                    "player": {"buffer_s": 10.0, "min_buffer_s": 5.0, "max_buffer_s": 30.0, "last_rendition_id": "360p"},
                    "viewer": {"mode": mode},
                },
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["video_id"], vid)
            self.assertEqual(len(data["decisions"]), 3)
            self.assertEqual(len(data["adapted_segments"]), 3)
            for seg in data["adapted_segments"]:
                self.assertIn("renditions", seg)
                for r in seg["renditions"]:
                    self.assertIn("width", r)
                    self.assertIn("height", r)
                    self.assertIn("bitrate_kbps", r)


if __name__ == "__main__":
    unittest.main()
