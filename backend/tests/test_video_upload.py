"""Automated test suite for video upload, validation, metadata extraction, and storage."""

import io
import json
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
from backend.app.modules.video_processing.metadata import (
    extract_video_metadata,
    get_ffprobe_executable,
)


def find_ffmpeg_executable() -> str | None:
    """Find ffmpeg binary for generating synthetic test media."""
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


class VideoUploadTestCase(unittest.TestCase):
    """Test suite covering the 10 requirements of the upload and validation pipeline."""

    @classmethod
    def setUpClass(cls):
        # Prepare temporary directory for test storage and generated fixtures
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.uploads_path = cls.temp_path / "uploads"
        cls.uploads_path.mkdir(parents=True, exist_ok=True)

        cls.mp4_bytes = b""
        cls.webm_bytes = b""

        ffmpeg_cmd = find_ffmpeg_executable()
        if ffmpeg_cmd:
            # Generate 1-second 320x240 synthetic MP4 fixture
            mp4_file = cls.temp_path / "synthetic_fixture.mp4"
            subprocess.run(
                [
                    ffmpeg_cmd,
                    "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-t", "1",
                    str(mp4_file),
                    "-y",
                ],
                check=True,
                timeout=15,
            )
            with open(mp4_file, "rb") as f:
                cls.mp4_bytes = f.read()

            # Generate 1-second 320x240 synthetic WebM fixture
            webm_file = cls.temp_path / "synthetic_fixture.webm"
            subprocess.run(
                [
                    ffmpeg_cmd,
                    "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
                    "-c:v", "libvpx-vp9",
                    "-t", "1",
                    str(webm_file),
                    "-y",
                ],
                check=True,
                timeout=20,
            )
            with open(webm_file, "rb") as f:
                cls.webm_bytes = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def setUp(self):
        # Override settings to isolate test storage
        self.test_settings = Settings(
            storage_dir=str(self.temp_path),
            max_upload_size_mb=500,
        )
        app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_01_successful_mp4_upload(self):
        """1. Verify successful upload, storage, and metadata of an MP4 file."""
        if not self.mp4_bytes:
            self.skipTest("FFmpeg unavailable to generate synthetic MP4.")

        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("sample_clip.mp4", io.BytesIO(self.mp4_bytes), "video/mp4")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()

        # Validate response schema
        self.assertIn("video_id", data)
        self.assertEqual(data["original_filename"], "sample_clip.mp4")
        self.assertEqual(data["status"], "uploaded")
        self.assertEqual(data["upload_size"], len(self.mp4_bytes))
        self.assertIn("created_at", data)

        # Validate metadata fields
        meta = data["metadata"]
        self.assertEqual(meta["width"], 320)
        self.assertEqual(meta["height"], 240)
        self.assertEqual(meta["resolution"], "320x240")
        self.assertEqual(meta["video_codec"], "h264")
        self.assertEqual(meta["audio_codec"], "aac")
        self.assertGreater(meta["duration"], 0.0)

        # Verify physical file existence
        saved_file = self.uploads_path / data["video_id"] / "source.mp4"
        self.assertTrue(saved_file.exists())
        self.assertEqual(saved_file.stat().st_size, len(self.mp4_bytes))

    def test_02_successful_webm_upload(self):
        """2. Verify successful upload of another supported format (WebM)."""
        if not self.webm_bytes:
            self.skipTest("FFmpeg unavailable to generate synthetic WebM.")

        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("clip.webm", io.BytesIO(self.webm_bytes), "video/webm")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()

        self.assertEqual(data["original_filename"], "clip.webm")
        self.assertIn("vp9", data["metadata"]["video_codec"].lower())
        saved_file = self.uploads_path / data["video_id"] / "source.webm"
        self.assertTrue(saved_file.exists())

    def test_03_empty_file_rejection(self):
        """3. Verify 0-byte file upload returns 400 Bad Request."""
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("empty.mp4", io.BytesIO(b""), "video/mp4")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"].lower())

    def test_04_unsupported_extension_rejection(self):
        """4. Verify unsupported extensions (e.g. .avi, .txt) return 415."""
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("test.avi", io.BytesIO(b"fake avi"), "video/x-msvideo")},
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn("unsupported", response.json()["detail"].lower())

    def test_05_invalid_video_content_rejection(self):
        """5. Verify corrupted media bytes return 422 Unprocessable Entity."""
        corrupted_content = b"Not a valid MP4 header or stream content at all"
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("corrupt.mp4", io.BytesIO(corrupted_content), "video/mp4")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("corrupted", response.json()["detail"].lower())

    def test_06_upload_size_limit_enforcement(self):
        """6. Verify file exceeding MAX_UPLOAD_SIZE_MB returns 413."""
        # Set artificially small limit of 1 MB
        custom_settings = Settings(
            storage_dir=str(self.temp_path),
            max_upload_size_mb=1,
        )
        app.dependency_overrides[get_settings] = lambda: custom_settings
        client = TestClient(app)

        oversized_data = b"x" * (2 * 1024 * 1024)  # 2 MB payload
        response = client.post(
            "/api/videos/upload",
            files={"file": ("too_large.mp4", io.BytesIO(oversized_data), "video/mp4")},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds", response.json()["detail"].lower())

    def test_07_metadata_extraction(self):
        """7. Directly verify FFprobe metadata extractor unit functionality."""
        if not self.mp4_bytes:
            self.skipTest("FFmpeg unavailable to generate synthetic MP4.")

        sample_path = self.temp_path / "metadata_unit_test.mp4"
        with open(sample_path, "wb") as f:
            f.write(self.mp4_bytes)

        metadata = extract_video_metadata(sample_path)
        self.assertEqual(metadata.width, 320)
        self.assertEqual(metadata.height, 240)
        self.assertEqual(metadata.resolution, "320x240")
        self.assertEqual(metadata.video_codec, "h264")
        self.assertEqual(metadata.audio_codec, "aac")
        self.assertGreater(metadata.duration, 0.0)
        self.assertEqual(metadata.file_size, len(self.mp4_bytes))

    @patch("backend.app.modules.video_processing.metadata.get_ffprobe_executable")
    def test_08_missing_ffprobe_handling(self, mock_ffprobe):
        """8. Verify 503 Service Unavailable when FFprobe is missing."""
        mock_ffprobe.side_effect = HTTPException(
            status_code=503,
            detail="FFprobe is not installed or not available on the server system PATH.",
        )

        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("sample.mp4", io.BytesIO(self.mp4_bytes or b"dummy"), "video/mp4")},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("ffprobe", response.json()["detail"].lower())

    def test_09_cleanup_of_incomplete_uploads(self):
        """9. Verify no partial directories or files linger after upload failure."""
        existing_items = set(self.uploads_path.iterdir())

        # Attempt uploading corrupted payload
        corrupted_content = b"Corrupted video payload for cleanup verification"
        response = self.client.post(
            "/api/videos/upload",
            files={"file": ("fail_clean.mp4", io.BytesIO(corrupted_content), "video/mp4")},
        )
        self.assertEqual(response.status_code, 422)

        current_items = set(self.uploads_path.iterdir())
        # Assert directory structure is identical before and after failure
        self.assertEqual(existing_items, current_items)

    def test_10_duplicate_filenames_separate_ids(self):
        """10. Verify identical filenames receive distinct UUIDs and separate storage."""
        if not self.mp4_bytes:
            self.skipTest("FFmpeg unavailable to generate synthetic MP4.")

        res1 = self.client.post(
            "/api/videos/upload",
            files={"file": ("duplicate.mp4", io.BytesIO(self.mp4_bytes), "video/mp4")},
        )
        res2 = self.client.post(
            "/api/videos/upload",
            files={"file": ("duplicate.mp4", io.BytesIO(self.mp4_bytes), "video/mp4")},
        )

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)

        data1 = res1.json()
        data2 = res2.json()

        self.assertNotEqual(data1["video_id"], data2["video_id"])

        file1 = self.uploads_path / data1["video_id"] / "source.mp4"
        file2 = self.uploads_path / data2["video_id"] / "source.mp4"

        self.assertTrue(file1.exists())
        self.assertTrue(file2.exists())
        self.assertNotEqual(str(file1), str(file2))

    def test_11_empty_storage_dir_and_non_numeric_dimensions_rejected(self):
        """11. Verify empty storage_dir raises ValueError and invalid dimensions return HTTP 422."""
        from backend.app.config.settings import resolve_storage_directory

        with self.assertRaises(ValueError):
            resolve_storage_directory("   ")

        sample_path = self.temp_path / "bad_dims.mp4"
        sample_path.write_bytes(b"placeholder")
        fake_ffprobe_json = json.dumps({
            "format": {"format_name": "mp4", "duration": "1.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": "bad", "height": "240"}],
        })
        fake_proc = subprocess.CompletedProcess(args=["ffprobe"], returncode=0, stdout=fake_ffprobe_json, stderr="")
        with (
            patch("backend.app.modules.video_processing.metadata.get_ffprobe_executable", return_value="ffprobe"),
            patch("backend.app.modules.video_processing.metadata.subprocess.run", return_value=fake_proc),
        ):
            with self.assertRaises(HTTPException) as ctx:
                extract_video_metadata(sample_path)
            self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
