"""Automated test suite for the Multi-Resolution Video Transcoding Engine.

Verifies the 10 requirements:
1. Successful 360p transcoding.
2. Successful 480p transcoding.
3. Successful 720p transcoding.
4. Aspect ratio preservation (e.g. 4:3 format).
5. No unnecessary upscaling (omitting renditions larger than source).
6. Portrait video handling (e.g. 9:16 format).
7. Source video preservation (source file unmodified).
8. Invalid source handling (nonexistent ID, corrupted file).
9. Missing FFmpeg handling (503 Service Unavailable / graceful failure).
10. Output metadata verification (codecs, duration, dimensions).
"""

import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app
from backend.app.modules.video_processing.metadata import extract_video_metadata
from backend.app.modules.video_processing.transcoder import (
    VideoTranscoder,
    get_ffmpeg_executable,
)
from backend.app.modules.video_processing.transcoding_config import (
    DEFAULT_RENDITION_PROFILES,
    RenditionProfile,
    TranscodingConfig,
    calculate_rendition_dimensions,
    to_even,
)


def find_system_ffmpeg() -> str | None:
    """Find FFmpeg binary on host system for generating fixtures and real transcoding."""
    custom = os.getenv("FFMPEG_PATH")
    if custom and os.path.exists(custom):
        return custom
    resolved = shutil.which("ffmpeg")
    if resolved:
        return resolved

    local_app_data = os.getenv("LOCALAPPDATA", "")
    winget_pkg_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if winget_pkg_dir.exists():
        for found in winget_pkg_dir.glob("**/ffmpeg.exe"):
            return str(found)
    return None


class VideoTranscodingTestCase(unittest.TestCase):
    """Test suite verifying all multi-resolution video transcoding requirements."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.uploads_path = cls.temp_path / "uploads"
        cls.processed_path = cls.temp_path / "processed"
        cls.uploads_path.mkdir(parents=True, exist_ok=True)
        cls.processed_path.mkdir(parents=True, exist_ok=True)

        cls.ffmpeg_bin = find_system_ffmpeg()

        cls.mp4_720p_bytes = b""
        cls.mp4_360p_bytes = b""
        cls.mp4_480p_4x3_bytes = b""
        cls.mp4_portrait_bytes = b""
        cls.mp4_no_audio_bytes = b""

        if cls.ffmpeg_bin:
            # 1. 720p 16:9 test fixture (1280x720, 1s duration, with audio)
            f_720p = cls.temp_path / "fix_720p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-t", "1",
                    str(f_720p), "-y",
                ],
                check=True,
                timeout=15,
            )
            cls.mp4_720p_bytes = f_720p.read_bytes()

            # 2. 360p 16:9 test fixture (640x360, 1s duration, with audio)
            f_360p = cls.temp_path / "fix_360p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-t", "1",
                    str(f_360p), "-y",
                ],
                check=True,
                timeout=15,
            )
            cls.mp4_360p_bytes = f_360p.read_bytes()

            # 3. 480p 4:3 test fixture (640x480, 1s duration, with audio)
            f_480p_4x3 = cls.temp_path / "fix_480p_4x3.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=640x480:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-t", "1",
                    str(f_480p_4x3), "-y",
                ],
                check=True,
                timeout=15,
            )
            cls.mp4_480p_4x3_bytes = f_480p_4x3.read_bytes()

            # 4. Portrait 9:16 test fixture (720x1280, 1s duration, with audio)
            f_portrait = cls.temp_path / "fix_portrait.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=720x1280:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-c:v", "libx264", "-c:a", "aac", "-t", "1",
                    str(f_portrait), "-y",
                ],
                check=True,
                timeout=15,
            )
            cls.mp4_portrait_bytes = f_portrait.read_bytes()

            # 5. Video without audio stream (640x360, 1s duration)
            f_no_audio = cls.temp_path / "fix_no_audio.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=30",
                    "-c:v", "libx264", "-an", "-t", "1",
                    str(f_no_audio), "-y",
                ],
                check=True,
                timeout=15,
            )
            cls.mp4_no_audio_bytes = f_no_audio.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        self.test_settings = Settings(
            storage_dir=str(self.temp_path),
            max_upload_size_mb=500,
            ffmpeg_path=self.ffmpeg_bin,
            transcoding_timeout_seconds=30,
        )
        app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def _upload_video_bytes(self, payload: bytes, filename: str = "input.mp4") -> str:
        """Helper to upload a video fixture and return its generated video_id."""
        response = self.client.post(
            "/api/videos/upload",
            files={"file": (filename, io.BytesIO(payload), "video/mp4")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["video_id"]

    def test_01_successful_360p_transcoding(self):
        """1. Verify successful generation of 360p rendition (640x360 max)."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "source_720p.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        # Find 360p rendition
        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        self.assertIn("360p", renditions)
        r360 = renditions["360p"]
        self.assertEqual(r360["width"], 640)
        self.assertEqual(r360["height"], 360)
        self.assertEqual(r360["resolution"], "640x360")
        self.assertIn("h264", r360["video_codec"].lower())
        self.assertIn("aac", r360["audio_codec"].lower())

        # Check physical output file
        out_file = self.processed_path / video_id / "360p" / "video.mp4"
        self.assertTrue(out_file.exists())
        self.assertGreater(out_file.stat().st_size, 0)

    def test_02_successful_480p_transcoding(self):
        """2. Verify successful generation of 480p rendition (854x480 max)."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "source_720p.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        self.assertIn("480p", renditions)
        r480 = renditions["480p"]
        self.assertEqual(r480["width"], 854)
        self.assertEqual(r480["height"], 480)
        self.assertEqual(r480["resolution"], "854x480")
        self.assertIn("h264", r480["video_codec"].lower())
        self.assertIn("aac", r480["audio_codec"].lower())

        out_file = self.processed_path / video_id / "480p" / "video.mp4"
        self.assertTrue(out_file.exists())
        self.assertGreater(out_file.stat().st_size, 0)

    def test_03_successful_720p_transcoding(self):
        """3. Verify successful generation of 720p rendition (1280x720 max)."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "source_720p.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        self.assertIn("720p", renditions)
        r720 = renditions["720p"]
        self.assertEqual(r720["width"], 1280)
        self.assertEqual(r720["height"], 720)
        self.assertEqual(r720["resolution"], "1280x720")
        self.assertIn("h264", r720["video_codec"].lower())
        self.assertIn("aac", r720["audio_codec"].lower())

        out_file = self.processed_path / video_id / "720p" / "video.mp4"
        self.assertTrue(out_file.exists())
        self.assertGreater(out_file.stat().st_size, 0)

    def test_04_aspect_ratio_preservation(self):
        """4. Verify non-16:9 aspect ratios (e.g. 4:3 640x480) are preserved without distortion."""
        if not self.mp4_480p_4x3_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_480p_4x3_bytes, "source_4x3.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        # 360p for 4:3 source should be 480x360 (ratio 4/3 = 1.333...)
        self.assertIn("360p", renditions)
        self.assertEqual(renditions["360p"]["width"], 480)
        self.assertEqual(renditions["360p"]["height"], 360)
        ratio_360 = renditions["360p"]["width"] / renditions["360p"]["height"]
        self.assertAlmostEqual(ratio_360, 4 / 3, places=2)

        # 480p for 4:3 source should be 640x480 (ratio 4/3 = 1.333...)
        self.assertIn("480p", renditions)
        self.assertEqual(renditions["480p"]["width"], 640)
        self.assertEqual(renditions["480p"]["height"], 480)
        ratio_480 = renditions["480p"]["width"] / renditions["480p"]["height"]
        self.assertAlmostEqual(ratio_480, 4 / 3, places=2)

        # 720p should be skipped since source height is 480 < 720
        skipped = {s["rendition"]: s["reason"] for s in data["skipped_renditions"]}
        self.assertIn("720p", skipped)
        self.assertIn("smaller than target", skipped["720p"].lower())

    def test_05_no_unnecessary_upscaling(self):
        """5. Verify lower-resolution sources omit renditions requiring upscaling."""
        if not self.mp4_360p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_360p_bytes, "source_360p.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        successful = [r["rendition"] for r in data["successful_renditions"]]
        self.assertIn("360p", successful)
        self.assertNotIn("480p", successful)
        self.assertNotIn("720p", successful)

        skipped = {s["rendition"]: s["reason"] for s in data["skipped_renditions"]}
        self.assertIn("480p", skipped)
        self.assertIn("720p", skipped)
        self.assertIn("prohibited", skipped["480p"].lower())
        self.assertIn("prohibited", skipped["720p"].lower())

        # Verify omitted rendition directories were not created
        self.assertFalse((self.processed_path / video_id / "480p").exists())
        self.assertFalse((self.processed_path / video_id / "720p").exists())

    def test_06_portrait_video_handling(self):
        """6. Verify portrait videos (e.g. 720x1280) scale with correct even dimensions."""
        if not self.mp4_portrait_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_portrait_bytes, "portrait.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        for r_name in ["360p", "480p", "720p"]:
            self.assertIn(r_name, renditions)
            r = renditions[r_name]
            # Height matches nominal target
            self.assertEqual(r["height"], int(r_name.replace("p", "")))
            # Width and height must be strictly even
            self.assertEqual(r["width"] % 2, 0)
            self.assertEqual(r["height"] % 2, 0)
            # Must remain portrait (width < height)
            self.assertLess(r["width"], r["height"])
            # Aspect ratio close to 9/16 (0.5625)
            self.assertAlmostEqual(r["width"] / r["height"], 9 / 16, delta=0.02)

    def test_07_source_video_preservation(self):
        """7. Verify original uploaded video file remains byte-for-byte identical."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "immutable.mp4")
        source_file = self.uploads_path / video_id / "source.mp4"
        self.assertTrue(source_file.exists())

        hash_before = hashlib.sha256(source_file.read_bytes()).hexdigest()
        size_before = source_file.stat().st_size
        mtime_before = source_file.stat().st_mtime_ns

        # Execute transcoding
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200)

        # Verify source file integrity post-transcode
        hash_after = hashlib.sha256(source_file.read_bytes()).hexdigest()
        size_after = source_file.stat().st_size
        mtime_after = source_file.stat().st_mtime_ns

        self.assertEqual(hash_before, hash_after)
        self.assertEqual(size_before, size_after)
        self.assertEqual(mtime_before, mtime_after)

    def test_08_invalid_source_handling(self):
        """8. Verify graceful handling of missing or corrupted source videos."""
        # Non-existent video UUID returns 404
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        res_missing = self.client.post(f"/api/videos/{fake_uuid}/transcode")
        self.assertEqual(res_missing.status_code, 404)
        self.assertIn("not found", res_missing.json()["detail"].lower())

        # Status endpoint also returns 404 for unknown video UUID
        res_status = self.client.get(f"/api/videos/{fake_uuid}/transcode/status")
        self.assertEqual(res_status.status_code, 404)

        # Corrupted source video returns 422
        bad_video_id = "11111111-1111-1111-1111-111111111111"
        bad_dir = self.uploads_path / bad_video_id
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_file = bad_dir / "source.mp4"
        bad_file.write_bytes(b"garbage content not video")

        res_corrupt = self.client.post(f"/api/videos/{bad_video_id}/transcode")
        self.assertEqual(res_corrupt.status_code, 422)

    @patch("backend.app.modules.video_processing.transcoder.get_ffmpeg_executable")
    def test_09_missing_ffmpeg_handling(self, mock_get_ffmpeg):
        """9. Verify 503 Service Unavailable when FFmpeg is missing from host."""
        mock_get_ffmpeg.side_effect = HTTPException(
            status_code=503,
            detail="FFmpeg is not installed or not available on the server system PATH.",
        )

        if not self.mp4_360p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_360p_bytes, "missing_ffmpeg.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 503)
        self.assertIn("ffmpeg", res.json()["detail"].lower())

    def test_10_output_metadata_verification(self):
        """10. Verify duration, codecs, bitrate, and technical metadata of outputs."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "metadata_test.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200)

        # Directly probe output with FFprobe to verify file integrity
        out_360p = self.processed_path / video_id / "360p" / "video.mp4"
        meta = extract_video_metadata(out_360p)
        self.assertEqual(meta.width, 640)
        self.assertEqual(meta.height, 360)
        self.assertIn("h264", meta.video_codec.lower())
        self.assertIn("aac", meta.audio_codec.lower())
        self.assertGreater(meta.duration, 0.5)
        self.assertLess(meta.duration, 1.5)

    def test_11_video_without_audio_stream(self):
        """11. Verify videos without audio transcode cleanly without audio stream errors."""
        if not self.mp4_no_audio_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_no_audio_bytes, "silent.mp4")
        res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["status"], "completed")
        renditions = {r["rendition"]: r for r in data["successful_renditions"]}
        self.assertIn("360p", renditions)
        self.assertIsNone(renditions["360p"]["audio_codec"])

        # Probe output with FFprobe
        out_file = self.processed_path / video_id / "360p" / "video.mp4"
        meta = extract_video_metadata(out_file)
        self.assertIsNone(meta.audio_codec)

    def test_12_transcode_status_api(self):
        """12. Verify GET /api/videos/{video_id}/transcode/status returns accurate state."""
        if not self.mp4_720p_bytes:
            self.skipTest("FFmpeg unavailable to generate fixtures.")

        video_id = self._upload_video_bytes(self.mp4_720p_bytes, "status_test.mp4")

        # Prior to transcoding, querying status returns 404
        pre_status = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(pre_status.status_code, 404)

        # Trigger transcode
        post_res = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(post_res.status_code, 200)
        post_data = post_res.json()

        # Query status post-transcode
        get_res = self.client.get(f"/api/videos/{video_id}/transcode/status")
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()

        self.assertEqual(post_data["video_id"], get_data["video_id"])
        self.assertEqual(post_data["status"], get_data["status"])
        self.assertEqual(len(post_data["successful_renditions"]), len(get_data["successful_renditions"]))

    def test_13_unit_dimension_calculation(self):
        """13. Unit test mathematical dimension calculation across edge-case aspect ratios."""
        p_360 = DEFAULT_RENDITION_PROFILES[0]
        p_480 = DEFAULT_RENDITION_PROFILES[1]
        p_720 = DEFAULT_RENDITION_PROFILES[2]

        # 16:9 1080p source -> produces all 3
        dims, err = calculate_rendition_dimensions(1920, 1080, p_360)
        self.assertEqual(dims, (640, 360))
        dims, err = calculate_rendition_dimensions(1920, 1080, p_480)
        self.assertEqual(dims, (854, 480))
        dims, err = calculate_rendition_dimensions(1920, 1080, p_720)
        self.assertEqual(dims, (1280, 720))

        # 4:3 1440x1080 source -> preserves 4:3
        dims, err = calculate_rendition_dimensions(1440, 1080, p_720)
        self.assertEqual(dims, (960, 720))

        # Ultrawide 21:9 (2560x1080) -> capped at max_width
        dims, err = calculate_rendition_dimensions(2560, 1080, p_720)
        self.assertIsNotNone(dims)
        self.assertLessEqual(dims[0], 1280)
        self.assertLessEqual(dims[1], 720)
        self.assertEqual(dims[0] % 2, 0)
        self.assertEqual(dims[1] % 2, 0)

        # 1:1 Square (1000x1000)
        dims, err = calculate_rendition_dimensions(1000, 1000, p_720)
        self.assertEqual(dims, (720, 720))

        # Sub-360p source (320x240) -> upscaling prohibited on all profiles
        dims, err = calculate_rendition_dimensions(320, 240, p_360)
        self.assertIsNone(dims)
        self.assertIn("upscaling prohibited", err.lower())


if __name__ == "__main__":
    unittest.main()
