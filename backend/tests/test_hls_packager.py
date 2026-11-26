"""Automated test suite for the StreamWise HLS Packaging Engine.

Covers:
1. Successful HLS generation (master.m3u8, media playlists, MPEG-TS segments).
2. Fractional frame rate handling and FFprobe keyframe/timestamp alignment across renditions.
3. Missing/skipped rendition handling (only successful MP4 renditions included).
4. Partial HLS packaging failure (valid renditions still packaged; failed ones excluded).
5. Total HLS failure preserving previously valid HLS outputs and cleaning staging dirs.
6. Interrupted directory promotion rollback and abandoned staging directory cleanup.
7. Byte-for-byte preservation of original uploads and MP4 renditions.
8. Invalid video IDs, missing transcodes, missing binaries, and silent videos.
9. Repeated idempotent requests, forced Windows directory replacement, and concurrent 409 conflict.
10. Direct validation rejection of non-keyframe segments and misaligned renditions.
"""

import concurrent.futures
import hashlib
import io
import json
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
from backend.app.modules.video_processing.hls_config import (
    HLSConfig,
    calculate_gop_frames,
    parse_frame_rate,
)
from backend.app.modules.video_processing.hls_packager import (
    HLSPackager,
    _promote_staging_to_hls,
    parse_media_playlist,
)
from backend.app.modules.video_processing.hls_schemas import HLSRenditionInfo, HLSSegmentInfo
from backend.app.modules.video_processing.transcoder import get_ffmpeg_executable


def find_ffmpeg() -> str | None:
    try:
        return get_ffmpeg_executable()
    except Exception:
        return None


class HLSPackagingTestCase(unittest.TestCase):
    """Comprehensive unit and integration tests for HLS packaging."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()

        cls.mp4_9s_720p_bytes = b""
        cls.mp4_9s_23976_bytes = b""
        cls.mp4_5s_360p_bytes = b""
        cls.mp4_5s_silent_bytes = b""

        if cls.ffmpeg_bin:
            # 1. 9s 1280x720 @ 30fps with audio (yields 3 segments: 4s, 4s, 1s)
            f_720 = cls.temp_path / "fix_9s_720p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=9:size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=9",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "9",
                    str(f_720), "-y",
                ],
                check=True,
                timeout=30,
            )
            cls.mp4_9s_720p_bytes = f_720.read_bytes()

            # 2. 9s 1280x720 @ 24000/1001 (23.976fps) fractional frame rate with audio
            f_frac = cls.temp_path / "fix_9s_23976.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=9:size=1280x720:rate=24000/1001",
                    "-f", "lavfi", "-i", "sine=frequency=660:duration=9",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "9",
                    str(f_frac), "-y",
                ],
                check=True,
                timeout=30,
            )
            cls.mp4_9s_23976_bytes = f_frac.read_bytes()

            # 3. 5s 640x360 @ 30fps with audio (skips 480p and 720p during transcoding)
            f_360 = cls.temp_path / "fix_5s_360p.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-t", "5",
                    str(f_360), "-y",
                ],
                check=True,
                timeout=20,
            )
            cls.mp4_5s_360p_bytes = f_360.read_bytes()

            # 4. 5s 640x360 silent video (no audio stream)
            f_silent = cls.temp_path / "fix_5s_silent.mp4"
            subprocess.run(
                [
                    cls.ffmpeg_bin, "-nostdin", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=30",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-an", "-t", "5",
                    str(f_silent), "-y",
                ],
                check=True,
                timeout=20,
            )
            cls.mp4_5s_silent_bytes = f_silent.read_bytes()

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
            transcoding_timeout_seconds=60,
            hls_segment_duration_seconds=4,
            hls_timeout_seconds=60,
        )
        app.dependency_overrides[get_settings] = lambda: self.test_settings
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _upload_and_transcode(self, payload: bytes, filename: str = "clip.mp4") -> str:
        up = self.client.post(
            "/api/videos/upload",
            files={"file": (filename, io.BytesIO(payload), "video/mp4")},
        )
        self.assertEqual(up.status_code, 200, up.text)
        video_id = up.json()["video_id"]
        tc = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(tc.status_code, 200, tc.text)
        return video_id

    def test_01_successful_hls_packaging_and_playlists(self):
        """1. Verify master.m3u8, rendition playlists, segments, and /hls/status for 720p video."""
        if not self.mp4_9s_720p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_9s_720p_bytes, "hls_9s.mp4")
        res = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["video_id"], video_id)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["overall_status"], "completed")
        self.assertEqual(data["segment_duration"], 4)
        self.assertEqual(len(data["renditions"]), 3)
        self.assertEqual(data["failed_renditions"], [])

        hls_dir = self.processed_path / video_id / "hls"
        master_path = hls_dir / "master.m3u8"
        self.assertTrue(master_path.exists())
        master_text = master_path.read_text(encoding="utf-8")
        self.assertIn("#EXTM3U", master_text)
        self.assertIn("#EXT-X-VERSION:6", master_text)
        self.assertIn("#EXT-X-INDEPENDENT-SEGMENTS", master_text)

        for r_info in data["renditions"]:
            r_name = r_info["rendition"]
            self.assertIn(f"{r_name}/playlist.m3u8", master_text)
            self.assertIn(f'RESOLUTION={r_info["resolution"]}', master_text)
            self.assertIn("avc1.", r_info["codecs"])
            self.assertIn("mp4a.40.2", r_info["codecs"])
            self.assertEqual(r_info["segment_count"], 3)

            r_dir = hls_dir / r_name
            entries = parse_media_playlist(r_dir / "playlist.m3u8")
            self.assertEqual(len(entries), 3)
            for seg in r_info["segments"]:
                self.assertTrue(seg["starts_with_keyframe"])
                self.assertTrue((r_dir / seg["filename"]).exists())
                self.assertGreater((r_dir / seg["filename"]).stat().st_size, 0)

        # Status endpoint must match packaging response
        st = self.client.get(f"/api/videos/{video_id}/hls/status")
        self.assertEqual(st.status_code, 200, st.text)
        self.assertEqual(st.json(), data)

    def test_02_fractional_fps_and_keyframe_alignment(self):
        """2. Verify keyframe & timestamp alignment across renditions at fractional 23.976fps."""
        if not self.mp4_9s_23976_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_9s_23976_bytes, "frac_23976.mp4")
        res = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["status"], "completed")
        self.assertEqual(len(data["renditions"]), 3)

        # Verify every rendition has identical segment counts and aligned PTS start timestamps
        ref_segments = data["renditions"][0]["segments"]
        tolerance = 2.0 / (24000 / 1001)  # ~0.0834s (2 frames)
        for r in data["renditions"][1:]:
            self.assertEqual(len(r["segments"]), len(ref_segments))
            for seg_ref, seg_cur in zip(ref_segments, r["segments"]):
                self.assertTrue(seg_cur["starts_with_keyframe"])
                self.assertAlmostEqual(seg_cur["duration"], seg_ref["duration"], delta=tolerance)
                self.assertAlmostEqual(seg_cur["start_time"], seg_ref["start_time"], delta=tolerance)

    def test_03_skipped_renditions_excluded_from_master(self):
        """3. Verify 360p-only video skips 480p/720p and omits them from master.m3u8."""
        if not self.mp4_5s_360p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_5s_360p_bytes, "only_360p.mp4")
        res = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["status"], "completed")
        self.assertEqual([r["rendition"] for r in data["renditions"]], ["360p"])
        self.assertEqual({s["rendition"] for s in data["skipped_renditions"]}, {"480p", "720p"})

        master_text = (self.processed_path / video_id / "hls" / "master.m3u8").read_text(encoding="utf-8")
        self.assertIn("360p/playlist.m3u8", master_text)
        self.assertNotIn("480p/playlist.m3u8", master_text)
        self.assertNotIn("720p/playlist.m3u8", master_text)

    def test_04_partial_hls_failure_excludes_failed_rendition(self):
        """4. Verify corrupted 480p MP4 fails independently while 360p & 720p still package."""
        if not self.mp4_9s_720p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_9s_720p_bytes, "partial_hls.mp4")
        # Corrupt 480p MP4 rendition on disk
        (self.processed_path / video_id / "480p" / "video.mp4").write_bytes(b"corrupted mp4 bytes")

        res = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()

        self.assertEqual(data["status"], "partial")
        self.assertEqual({r["rendition"] for r in data["renditions"]}, {"360p", "720p"})
        self.assertEqual(len(data["failed_renditions"]), 1)
        self.assertEqual(data["failed_renditions"][0]["rendition"], "480p")

        hls_dir = self.processed_path / video_id / "hls"
        self.assertFalse((hls_dir / "480p").exists())
        master_text = (hls_dir / "master.m3u8").read_text(encoding="utf-8")
        self.assertIn("360p/playlist.m3u8", master_text)
        self.assertIn("720p/playlist.m3u8", master_text)
        self.assertNotIn("480p/playlist.m3u8", master_text)

        st = self.client.get(f"/api/videos/{video_id}/hls/status")
        self.assertEqual(st.status_code, 200)
        self.assertEqual(st.json()["status"], "partial")

    def test_05_total_failure_preserves_previous_valid_hls_and_cleans_staging(self):
        """5. Verify total HLS failure preserves existing valid HLS outputs and removes staging."""
        if not self.mp4_5s_360p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_5s_360p_bytes, "preserve_hls.mp4")
        res_ok = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res_ok.status_code, 200)

        hls_dir = self.processed_path / video_id / "hls"
        master_hash = hashlib.sha256((hls_dir / "master.m3u8").read_bytes()).hexdigest()
        seg0_hash = hashlib.sha256((hls_dir / "360p" / "segment_000.ts").read_bytes()).hexdigest()

        # Force re-run that fails completely during FFmpeg execution
        real_run = subprocess.run

        def fail_hls_ffmpeg(cmd, *args, **kwargs):
            if "ffmpeg" in Path(str(cmd[0])).name.lower():
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="Simulated crash")
            return real_run(cmd, *args, **kwargs)

        with patch("backend.app.modules.video_processing.hls_packager.subprocess.run", side_effect=fail_hls_ffmpeg):
            res_fail = self.client.post(f"/api/videos/{video_id}/hls?force=true")

        self.assertEqual(res_fail.status_code, 200)
        self.assertEqual(res_fail.json()["status"], "failed")
        self.assertIsNone(res_fail.json()["master_playlist_path"])

        # Previous valid HLS files & status MUST remain completely intact
        self.assertEqual(hashlib.sha256((hls_dir / "master.m3u8").read_bytes()).hexdigest(), master_hash)
        self.assertEqual(hashlib.sha256((hls_dir / "360p" / "segment_000.ts").read_bytes()).hexdigest(), seg0_hash)
        st = self.client.get(f"/api/videos/{video_id}/hls/status")
        self.assertEqual(st.status_code, 200)
        self.assertEqual(st.json()["status"], "completed")

        # No staging or backup directories left behind
        leftovers = [d for d in (self.processed_path / video_id).iterdir() if d.name.startswith(".hls_")]
        self.assertEqual(leftovers, [])

    def test_06_interrupted_promotion_rollback_and_abandoned_cleanup(self):
        """6. Verify interrupted directory swap rolls back to previous HLS and cleans abandoned staging."""
        if not self.mp4_5s_360p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_5s_360p_bytes, "rollback.mp4")
        self.assertEqual(self.client.post(f"/api/videos/{video_id}/hls").status_code, 200)

        video_proc_dir = self.processed_path / video_id
        hls_dir = video_proc_dir / "hls"
        master_bytes_before = (hls_dir / "master.m3u8").read_bytes()

        # Simulate an abandoned staging directory from a prior crashed process
        abandoned = video_proc_dir / ".hls_staging_abandoned"
        abandoned.mkdir()
        (abandoned / "junk.ts").write_bytes(b"stale")

        real_replace = os.replace
        call_count = {"n": 0}

        def fail_second_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("Simulated Windows file lock during promotion")
            return real_replace(src, dst)

        with patch("backend.app.modules.video_processing.hls_packager.os.replace", side_effect=fail_second_replace):
            res_err = self.client.post(f"/api/videos/{video_id}/hls?force=true")
            self.assertEqual(res_err.status_code, 500)

        # Abandoned staging cleaned up, original hls_dir rolled back intact
        self.assertFalse(abandoned.exists())
        self.assertTrue(hls_dir.exists())
        self.assertEqual((hls_dir / "master.m3u8").read_bytes(), master_bytes_before)
        leftovers = [d for d in video_proc_dir.iterdir() if d.name.startswith(".hls_")]
        self.assertEqual(leftovers, [])

    def test_07_uploads_and_mp4_renditions_preserved(self):
        """7. Verify original uploaded video and existing MP4 renditions remain byte-for-byte unchanged."""
        if not self.mp4_9s_720p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_9s_720p_bytes, "immutable_mp4s.mp4")

        files_to_check = [
            self.uploads_path / video_id / "source.mp4",
            self.processed_path / video_id / "360p" / "video.mp4",
            self.processed_path / video_id / "480p" / "video.mp4",
            self.processed_path / video_id / "720p" / "video.mp4",
        ]
        before = {
            f: (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns)
            for f in files_to_check
        }

        res = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(res.status_code, 200)

        for f, (h_before, m_before) in before.items():
            self.assertEqual(hashlib.sha256(f.read_bytes()).hexdigest(), h_before)
            self.assertEqual(f.stat().st_mtime_ns, m_before)

    def test_08_invalid_ids_missing_transcodes_and_silent_video(self):
        """8. Verify 400 on malformed IDs, 404 on untranscoded/missing IDs, and silent video HLS."""
        for bad_id in ["invalid-uuid", "..%5C..%5C"]:
            self.assertEqual(self.client.post(f"/api/videos/{bad_id}/hls").status_code, 400)
            self.assertEqual(self.client.get(f"/api/videos/{bad_id}/hls/status").status_code, 400)

        unknown_id = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.client.post(f"/api/videos/{unknown_id}/hls").status_code, 404)
        self.assertEqual(self.client.get(f"/api/videos/{unknown_id}/hls/status").status_code, 404)

        if not self.mp4_5s_silent_bytes:
            self.skipTest("FFmpeg unavailable.")

        # Uploaded but not transcoded yet -> 404
        up = self.client.post(
            "/api/videos/upload",
            files={"file": ("silent.mp4", io.BytesIO(self.mp4_5s_silent_bytes), "video/mp4")},
        )
        vid = up.json()["video_id"]
        self.assertEqual(self.client.post(f"/api/videos/{vid}/hls").status_code, 404)

        # Transcode then package silent video
        self.assertEqual(self.client.post(f"/api/videos/{vid}/transcode").status_code, 200)
        hls_res = self.client.post(f"/api/videos/{vid}/hls")
        self.assertEqual(hls_res.status_code, 200, hls_res.text)
        data = hls_res.json()
        self.assertEqual(data["status"], "completed")
        self.assertNotIn("mp4a", data["renditions"][0]["codecs"])

    def test_09_repeated_idempotent_force_and_concurrent_requests(self):
        """9. Verify repeated idempotent POST, Windows ?force=true overwrite, and 409 concurrency."""
        if not self.mp4_5s_360p_bytes:
            self.skipTest("FFmpeg unavailable.")

        video_id = self._upload_and_transcode(self.mp4_5s_360p_bytes, "concurrent_hls.mp4")

        entered_hls = threading.Event()
        release_hls = threading.Event()
        real_run = subprocess.run

        def slow_hls_ffmpeg(cmd, *args, **kwargs):
            if "ffmpeg" in Path(str(cmd[0])).name.lower():
                entered_hls.set()
                release_hls.wait(timeout=15)
            return real_run(cmd, *args, **kwargs)

        with patch("backend.app.modules.video_processing.hls_packager.subprocess.run", side_effect=slow_hls_ffmpeg):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                f1 = executor.submit(self.client.post, f"/api/videos/{video_id}/hls")
                self.assertTrue(entered_hls.wait(timeout=5))

                # Status returns 'processing' while active
                st_mid = self.client.get(f"/api/videos/{video_id}/hls/status")
                self.assertEqual(st_mid.status_code, 200)
                self.assertEqual(st_mid.json()["status"], "processing")

                # Concurrent HLS or transcode request returns 409 Conflict
                res_conflict_hls = self.client.post(f"/api/videos/{video_id}/hls")
                self.assertEqual(res_conflict_hls.status_code, 409)
                res_conflict_tc = self.client.post(f"/api/videos/{video_id}/transcode?force=true")
                self.assertEqual(res_conflict_tc.status_code, 409)

                release_hls.set()
                res1 = f1.result(timeout=30)

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["status"], "completed")

        # Repeated POST without force returns existing result without invoking FFmpeg
        with patch(
            "backend.app.modules.video_processing.hls_packager.subprocess.run",
            side_effect=AssertionError("FFmpeg must not run for idempotent duplicate HLS call"),
        ):
            res2 = self.client.post(f"/api/videos/{video_id}/hls")
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json(), res1.json())

        # ?force=true replaces existing non-empty hls/ directory cleanly on Windows
        res3 = self.client.post(f"/api/videos/{video_id}/hls?force=true")
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()["status"], "completed")

    def test_10_unit_validation_and_math_helpers(self):
        """10. Unit test fractional FPS math, missing keyframe rejection, and alignment check."""
        self.assertAlmostEqual(parse_frame_rate("24000/1001"), 23.976023976, places=5)
        self.assertEqual(calculate_gop_frames(24000 / 1001, 4), 96)
        self.assertEqual(calculate_gop_frames(30.0, 4), 120)

        packager = HLSPackager(HLSConfig())

        # Alignment validation should reject mismatched start_time beyond tolerance
        r360 = HLSRenditionInfo(
            rendition="360p",
            playlist_path="360p/playlist.m3u8",
            relative_playlist_uri="360p/playlist.m3u8",
            width=640,
            height=360,
            resolution="640x360",
            bandwidth=500000,
            average_bandwidth=450000,
            codecs="avc1.64001e,mp4a.40.2",
            segment_count=2,
            total_duration=8.0,
            segments=[
                HLSSegmentInfo(filename="segment_000.ts", duration=4.0, file_size=1000, start_time=1.4, starts_with_keyframe=True),
                HLSSegmentInfo(filename="segment_001.ts", duration=4.0, file_size=1000, start_time=5.4, starts_with_keyframe=True),
            ],
        )
        r720_drifted = HLSRenditionInfo(
            rendition="720p",
            playlist_path="720p/playlist.m3u8",
            relative_playlist_uri="720p/playlist.m3u8",
            width=1280,
            height=720,
            resolution="1280x720",
            bandwidth=1500000,
            average_bandwidth=1400000,
            codecs="avc1.64001f,mp4a.40.2",
            segment_count=2,
            total_duration=8.0,
            segments=[
                HLSSegmentInfo(filename="segment_000.ts", duration=4.0, file_size=2000, start_time=1.4, starts_with_keyframe=True),
                HLSSegmentInfo(filename="segment_001.ts", duration=4.0, file_size=2000, start_time=5.9, starts_with_keyframe=True),
            ],
        )
        staging_tmp = self.run_dir / "align_test"
        (staging_tmp / "720p").mkdir(parents=True)
        aligned, failures = packager._validate_cross_rendition_alignment(
            [r360, r720_drifted],
            {"360p": 30.0, "720p": 30.0},
            staging_tmp,
        )
        self.assertEqual([r.rendition for r in aligned], ["360p"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].rendition, "720p")
        self.assertIn("start pts", failures[0].error.lower())

        # Segment validation should reject a segment whose first frame is not a keyframe
        from backend.app.modules.video_processing.schemas import VideoMetadata

        bad_seg_dir = self.run_dir / "bad_kf_seg"
        bad_seg_dir.mkdir(parents=True)
        (bad_seg_dir / "segment_000.ts").write_bytes(b"fake ts bytes")
        (bad_seg_dir / "playlist.m3u8").write_text(
            "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:4\n"
            "#EXT-X-INDEPENDENT-SEGMENTS\n#EXTINF:4.000000,\nsegment_000.ts\n#EXT-X-ENDLIST\n",
            encoding="utf-8",
        )
        fake_meta = VideoMetadata(
            duration=4.0, width=640, height=360, resolution="640x360",
            frame_rate="30/1", video_codec="h264", audio_codec=None,
            container="mp4", bitrate=500000, file_size=1000,
        )
        fake_probe = {
            "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360}],
            "frames": [{"stream_index": 0, "key_frame": 0, "pict_type": "P", "pts_time": "1.400000"}],
        }
        with patch("backend.app.modules.video_processing.hls_packager._probe_segment", return_value=fake_probe):
            with self.assertRaises(ValueError) as ctx:
                packager._validate_rendition_hls("360p", bad_seg_dir, bad_seg_dir, fake_meta, "ffprobe")
            self.assertIn("keyframe", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
