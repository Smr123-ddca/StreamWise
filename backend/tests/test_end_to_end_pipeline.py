"""End-to-end integration tests for the complete StreamWise video processing pipeline.

Covers the full lifecycle from multipart upload through validation, metadata extraction,
storage isolation, multi-resolution transcoding, concurrency protection, failure states,
and status retrieval.
"""

import concurrent.futures
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app
from backend.app.modules.video_processing.metadata import (
    extract_video_metadata,
    get_ffprobe_executable,
)
from backend.app.modules.video_processing.service import resolve_uploads_directory
from backend.app.modules.video_processing.transcoder import get_ffmpeg_executable


def find_ffmpeg() -> str | None:
    """Locate ffmpeg executable for generating test media fixtures."""
    try:
        return get_ffmpeg_executable()
    except Exception:
        return None


class EndToEndPipelineTestCase(unittest.TestCase):
    """Integration tests covering upload -> validation -> transcode -> status."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()

        cls.mp4_720p_utf8_bytes = b""
        cls.mp4_240p_bytes = b""

        if cls.ffmpeg_bin:
            # 1. 720p fixture with non-ASCII UTF-8 metadata tag to verify Windows encoding safety
            f_720 = cls.temp_path / "e2e_720p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                    "-metadata", "title=StreamWise_Audit_Tést_🎬",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "1",
                    str(f_720), "-y",
                ],
                check=True,
                timeout=20,
            )
            cls.mp4_720p_utf8_bytes = f_720.read_bytes()

            # 2. Sub-360p fixture (320x240) to test skip behavior across all renditions
            f_240 = cls.temp_path / "e2e_240p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "1",
                    str(f_240), "-y",
                ],
                check=True,
                timeout=20,
            )
            cls.mp4_240p_bytes = f_240.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp(dir=self.temp_path))
        self.uploads_path = self.run_dir / "uploads"
        self.processed_path = self.run_dir / "processed"
        self.test_settings = Settings(
            storage_dir=str(self.run_dir),
            max_upload_size_mb=500,
            ffmpeg_path=self.ffmpeg_bin,
            transcoding_timeout_seconds=30,
        )
        app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def test_01_full_upload_to_transcode_to_status_lifecycle(self):
        """1. Complete lifecycle: upload (with UTF-8 metadata) -> transcode -> verify outputs -> status."""
        if not self.mp4_720p_utf8_bytes:
            self.skipTest("FFmpeg unavailable.")

        # Step 1: Upload via API
        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("audit_720p.mp4", io.BytesIO(self.mp4_720p_utf8_bytes), "video/mp4")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        up_data = up_res.json()
        video_id = up_data["video_id"]
        self.assertEqual(up_data["status"], "uploaded")
        self.assertEqual(up_data["metadata"]["resolution"], "1280x720")

        source_file = self.uploads_path / video_id / "source.mp4"
        self.assertTrue(source_file.exists())
        source_hash_before = hashlib.sha256(source_file.read_bytes()).hexdigest()

        # Step 2: Initiate transcoding via API
        tc_res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(tc_res.status_code, 200, tc_res.text)
        tc_data = tc_res.json()

        self.assertEqual(tc_data["video_id"], video_id)
        self.assertEqual(tc_data["status"], "completed")
        self.assertEqual(tc_data["overall_status"], "completed")
        self.assertEqual(len(tc_data["successful_renditions"]), 3)
        self.assertEqual(tc_data["skipped_renditions"], [])
        self.assertEqual(tc_data["failed_renditions"], [])
        self.assertGreaterEqual(tc_data["processing_duration"], 0.0)

        # Step 3: Verify source file remains untouched
        self.assertEqual(
            hashlib.sha256(source_file.read_bytes()).hexdigest(),
            source_hash_before,
        )

        # Step 4: Verify all renditions are accessible, valid, and no temp files linger
        expected_dims = {"360p": (640, 360), "480p": (854, 480), "720p": (1280, 720)}
        for name, (exp_w, exp_h) in expected_dims.items():
            rendition_dir = self.processed_path / video_id / name
            out_file = rendition_dir / "video.mp4"
            tmp_file = rendition_dir / "video.tmp.mp4"
            self.assertTrue(out_file.exists())
            self.assertFalse(tmp_file.exists())
            self.assertGreater(out_file.stat().st_size, 0)

            probed = extract_video_metadata(out_file)
            self.assertEqual((probed.width, probed.height), (exp_w, exp_h))
            self.assertIn("h264", probed.video_codec.lower())
            self.assertIn("aac", probed.audio_codec.lower())

        # Step 5: Verify status endpoint matches transcoding result
        st_res = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(st_res.status_code, 200, st_res.text)
        self.assertEqual(st_res.json(), tc_data)

    def test_02_smaller_video_no_upscaling_e2e(self):
        """2. Verify 320x240 video skips all higher renditions and status reflects 'skipped'."""
        if not self.mp4_240p_bytes:
            self.skipTest("FFmpeg unavailable.")

        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("tiny.mp4", io.BytesIO(self.mp4_240p_bytes), "video/mp4")},
        )
        self.assertEqual(up_res.status_code, 200)
        video_id = up_res.json()["video_id"]

        tc_res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(tc_res.status_code, 200, tc_res.text)
        tc_data = tc_res.json()

        self.assertEqual(tc_data["status"], "skipped")
        self.assertEqual(tc_data["successful_renditions"], [])
        self.assertEqual(len(tc_data["skipped_renditions"]), 3)
        self.assertEqual({s["rendition"] for s in tc_data["skipped_renditions"]}, {"360p", "480p", "720p"})

        st_res = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(st_res.status_code, 200)
        self.assertEqual(st_res.json()["status"], "skipped")

    def test_03_invalid_video_and_traversal_rejected(self):
        """3. Verify corrupted uploads, missing UUIDs, and path traversal IDs are rejected."""
        # Corrupted upload leaves no files in uploads or processed
        corrupt_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("bad.mp4", io.BytesIO(b"corrupted data"), "video/mp4")},
        )
        self.assertEqual(corrupt_res.status_code, 422)
        self.assertEqual(list(self.uploads_path.iterdir()), [])

        # Path traversal / malformed video_id returns 400 Bad Request
        for bad_id in ["not-a-uuid", "..%5C..%5C", "C:%5CWindows"]:
            res_post = self.client.post(f"/api/videos/{bad_id}/transcode")
            self.assertEqual(res_post.status_code, 400, f"Expected 400 for {bad_id}, got {res_post.status_code}")
            res_get = self.client.get(f"/api/videos/{bad_id}/transcode/status")
            self.assertEqual(res_get.status_code, 400, f"Expected 400 for {bad_id}, got {res_get.status_code}")

    def test_04_partial_transcoding_failure_and_cleanup(self):
        """4. Verify partial failure removes incomplete files and reports 'partial' accurately."""
        if not self.mp4_720p_utf8_bytes:
            self.skipTest("FFmpeg unavailable.")

        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("partial.mp4", io.BytesIO(self.mp4_720p_utf8_bytes), "video/mp4")},
        )
        video_id = up_res.json()["video_id"]

        real_run = subprocess.run

        def fail_on_480p(cmd, *args, **kwargs):
            # Fail only when scaling to 480p (854x480)
            if any("scale=854:480" in str(arg) for arg in cmd):
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="Simulated 480p encoder failure"
                )
            return real_run(cmd, *args, **kwargs)

        with patch("backend.app.modules.video_processing.transcoder.subprocess.run", side_effect=fail_on_480p):
            tc_res = self.client.post(f"/api/videos/{video_id}/transcode")

        self.assertEqual(tc_res.status_code, 200, tc_res.text)
        tc_data = tc_res.json()

        self.assertEqual(tc_data["status"], "partial")
        self.assertEqual(tc_data["overall_status"], "partial")
        self.assertEqual({r["rendition"] for r in tc_data["successful_renditions"]}, {"360p", "720p"})
        self.assertEqual(len(tc_data["failed_renditions"]), 1)
        self.assertEqual(tc_data["failed_renditions"][0]["rendition"], "480p")
        self.assertIn("Simulated 480p encoder failure", tc_data["failed_renditions"][0]["error"])

        # Failed 480p directory must have no leftover output or temp files
        dir_480 = self.processed_path / video_id / "480p"
        self.assertFalse((dir_480 / "video.mp4").exists())
        self.assertFalse((dir_480 / "video.tmp.mp4").exists())
        self.assertFalse(dir_480.exists())

        # Status endpoint must reflect partial failure, never completed
        st_res = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(st_res.status_code, 200)
        self.assertEqual(st_res.json()["status"], "partial")
        self.assertEqual(len(st_res.json()["failed_renditions"]), 1)

    def test_05_total_failure_reports_failed_and_cleans_stale_files(self):
        """5. Verify total timeout/failure leaves no misleading outputs and marks status 'failed'."""
        if not self.mp4_720p_utf8_bytes:
            self.skipTest("FFmpeg unavailable.")

        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("timeout.mp4", io.BytesIO(self.mp4_720p_utf8_bytes), "video/mp4")},
        )
        video_id = up_res.json()["video_id"]

        # Pre-populate a prior successful transcode so we verify stale outputs are removed on forced failure
        init_res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(init_res.status_code, 200)
        self.assertTrue((self.processed_path / video_id / "360p" / "video.mp4").exists())

        real_run = subprocess.run

        def timeout_on_ffmpeg(cmd, *args, **kwargs):
            if "ffmpeg" in Path(str(cmd[0])).name.lower():
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30, stderr=b"frame=12")
            return real_run(cmd, *args, **kwargs)

        with patch(
            "backend.app.modules.video_processing.transcoder.subprocess.run",
            side_effect=timeout_on_ffmpeg,
        ):
            tc_res = self.client.post(f"/api/videos/{video_id}/transcode?force=true")

        self.assertEqual(tc_res.status_code, 200)
        tc_data = tc_res.json()
        self.assertEqual(tc_data["status"], "failed")
        self.assertEqual(tc_data["successful_renditions"], [])
        self.assertEqual(len(tc_data["failed_renditions"]), 3)
        self.assertIn("timed out", tc_data["failed_renditions"][0]["error"].lower())

        for r_name in ["360p", "480p", "720p"]:
            self.assertFalse((self.processed_path / video_id / r_name).exists())

        st_res = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(st_res.status_code, 200)
        self.assertEqual(st_res.json()["status"], "failed")

    def test_06_concurrent_transcode_conflict_and_processing_status(self):
        """6. Verify concurrent POST for same video_id returns 409 and GET /status shows 'processing'."""
        if not self.mp4_240p_bytes:
            self.skipTest("FFmpeg unavailable.")

        # Use 720p so transcoding actually invokes FFmpeg
        if not self.mp4_720p_utf8_bytes:
            self.skipTest("FFmpeg unavailable.")

        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("concurrent.mp4", io.BytesIO(self.mp4_720p_utf8_bytes), "video/mp4")},
        )
        video_id = up_res.json()["video_id"]

        entered_ffmpeg = threading.Event()
        release_ffmpeg = threading.Event()
        real_run = subprocess.run

        def slow_ffmpeg(cmd, *args, **kwargs):
            if "ffmpeg" in Path(str(cmd[0])).name.lower():
                entered_ffmpeg.set()
                release_ffmpeg.wait(timeout=10)
            return real_run(cmd, *args, **kwargs)

        with patch("backend.app.modules.video_processing.transcoder.subprocess.run", side_effect=slow_ffmpeg):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(self.client.post, f"/api/videos/{video_id}/transcode")
                self.assertTrue(entered_ffmpeg.wait(timeout=5), "First transcode did not enter FFmpeg in time")

                # While first job is active, status must show 'processing'
                mid_status = self.client.get(f"/api/videos/{video_id}/transcode/status")
                self.assertEqual(mid_status.status_code, 200)
                self.assertEqual(mid_status.json()["status"], "processing")

                # Concurrent duplicate POST must be rejected with 409 Conflict
                second_res = self.client.post(f"/api/videos/{video_id}/transcode")
                self.assertEqual(second_res.status_code, 409)
                self.assertIn("already in progress", second_res.json()["detail"].lower())

                # Allow first job to finish
                release_ffmpeg.set()
                first_res = first_future.result(timeout=30)

        self.assertEqual(first_res.status_code, 200)
        self.assertEqual(first_res.json()["status"], "completed")

        final_status = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(final_status.status_code, 200)
        self.assertEqual(final_status.json()["status"], "completed")

    def test_07_duplicate_transcode_idempotency_and_force(self):
        """7. Verify sequential duplicate POST returns existing completed result unless force=true."""
        if not self.mp4_720p_utf8_bytes:
            self.skipTest("FFmpeg unavailable.")

        up_res = self.client.post(
            "/api/videos/upload",
            files={"file": ("idempotent.mp4", io.BytesIO(self.mp4_720p_utf8_bytes), "video/mp4")},
        )
        video_id = up_res.json()["video_id"]

        res1 = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["status"], "completed")

        # Second call without force should NOT invoke FFmpeg again
        with patch(
            "backend.app.modules.video_processing.transcoder.subprocess.run",
            side_effect=AssertionError("FFmpeg should not be re-invoked on idempotent duplicate call"),
        ):
            res2 = self.client.post(f"/api/videos/{video_id}/transcode")
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json(), res1.json())

        # Call with force=true DOES re-run transcoding
        res3 = self.client.post(f"/api/videos/{video_id}/transcode?force=true")
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()["status"], "completed")

    def test_08_storage_resolution_and_explicit_missing_binaries(self):
        """8. Verify custom relative storage_dir is respected and invalid explicit binary paths return 503."""
        # Non-existent relative storage_dir should resolve under CWD, not fallback to repo storage
        prev_cwd = os.getcwd()
        try:
            os.chdir(self.run_dir)
            resolved = resolve_uploads_directory("custom_rel_storage")
            self.assertEqual(resolved, (self.run_dir / "custom_rel_storage" / "uploads").resolve())
        finally:
            os.chdir(prev_cwd)

        # Explicit invalid ffmpeg_path should return 503 instead of falling back to system PATH
        with self.assertRaises(Exception) as ctx:
            get_ffmpeg_executable("/nonexistent/path/to/ffmpeg")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 503)

        with self.assertRaises(Exception) as ctx2:
            get_ffprobe_executable("/nonexistent/path/to/ffprobe")
        self.assertEqual(getattr(ctx2.exception, "status_code", None), 503)


if __name__ == "__main__":
    unittest.main()
