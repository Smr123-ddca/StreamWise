"""Automated test suite for secure HLS HTTP delivery, Byte-Range support, CORS, and playback page."""

import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config.settings import Settings, get_settings
from backend.app.main import app
from backend.app.modules.video_processing.transcoder import get_ffmpeg_executable


def find_ffmpeg() -> str | None:
    try:
        return get_ffmpeg_executable()
    except Exception:
        return None


class HLSDeliveryTestCase(unittest.TestCase):
    """Verify HTTP delivery of HLS playlists and MPEG-TS segments."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.temp_dir.name)
        cls.ffmpeg_bin = find_ffmpeg()
        cls.mp4_9s_bytes = b""

        if cls.ffmpeg_bin:
            fixture = cls.temp_path / "delivery_fixture.mp4"
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

    def _prepare_hls_video(self) -> tuple[str, dict]:
        if not self.mp4_9s_bytes:
            self.skipTest("FFmpeg unavailable.")
        up = self.client.post(
            "/api/videos/upload",
            files={"file": ("delivery.mp4", io.BytesIO(self.mp4_9s_bytes), "video/mp4")},
        )
        self.assertEqual(up.status_code, 200)
        video_id = up.json()["video_id"]
        tc = self.client.post(f"/api/videos/{video_id}/transcode")
        self.assertEqual(tc.status_code, 200)
        hls = self.client.post(f"/api/videos/{video_id}/hls")
        self.assertEqual(hls.status_code, 200)
        return video_id, hls.json()

    def test_01_valid_master_and_rendition_playlist_delivery(self):
        """1. Verify GET/HEAD on master.m3u8 and rendition playlists return valid headers & URIs."""
        video_id, hls_data = self._prepare_hls_video()

        # GET master.m3u8
        res_master = self.client.get(f"/api/videos/{video_id}/hls/master.m3u8")
        self.assertEqual(res_master.status_code, 200)
        self.assertEqual(res_master.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertEqual(res_master.headers["cache-control"], "no-cache, must-revalidate")
        self.assertEqual(res_master.headers["accept-ranges"], "bytes")
        self.assertEqual(res_master.headers["x-content-type-options"], "nosniff")
        self.assertIn("etag", res_master.headers)
        self.assertIn("last-modified", res_master.headers)
        self.assertEqual(int(res_master.headers["content-length"]), len(res_master.content))

        master_text = res_master.text
        self.assertIn("360p/playlist.m3u8", master_text)
        self.assertIn("480p/playlist.m3u8", master_text)
        self.assertIn("720p/playlist.m3u8", master_text)

        # HEAD master.m3u8
        head_master = self.client.head(f"/api/videos/{video_id}/hls/master.m3u8")
        self.assertEqual(head_master.status_code, 200)
        self.assertEqual(head_master.content, b"")
        self.assertEqual(int(head_master.headers["content-length"]), len(res_master.content))

        # GET & HEAD rendition playlist
        for r in hls_data["renditions"]:
            r_name = r["rendition"]
            res_pl = self.client.get(f"/api/videos/{video_id}/hls/{r_name}/playlist.m3u8")
            self.assertEqual(res_pl.status_code, 200)
            self.assertEqual(res_pl.headers["content-type"], "application/vnd.apple.mpegurl")
            self.assertEqual(res_pl.headers["cache-control"], "no-cache, must-revalidate")
            self.assertIn("segment_000.ts", res_pl.text)

            head_pl = self.client.head(f"/api/videos/{video_id}/hls/{r_name}/playlist.m3u8")
            self.assertEqual(head_pl.status_code, 200)
            self.assertEqual(head_pl.content, b"")
            self.assertEqual(int(head_pl.headers["content-length"]), len(res_pl.content))

    def test_02_valid_segment_delivery_and_byte_ranges(self):
        """2. Verify MPEG-TS segment GET, HEAD, 206 Partial Content, and 416 Range Not Satisfiable."""
        video_id, _ = self._prepare_hls_video()
        seg_disk = (self.processed_path / video_id / "hls" / "360p" / "segment_000.ts").read_bytes()
        total_len = len(seg_disk)
        seg_url = f"/api/videos/{video_id}/hls/360p/segment_000.ts"

        # Full GET
        res_full = self.client.get(seg_url)
        self.assertEqual(res_full.status_code, 200)
        self.assertEqual(res_full.headers["content-type"], "video/mp2t")
        self.assertEqual(res_full.headers["cache-control"], "public, max-age=3600")
        self.assertEqual(res_full.headers["accept-ranges"], "bytes")
        self.assertEqual(res_full.headers["x-content-type-options"], "nosniff")
        self.assertEqual(int(res_full.headers["content-length"]), total_len)
        self.assertEqual(res_full.content, seg_disk)

        # HEAD
        res_head = self.client.head(seg_url)
        self.assertEqual(res_head.status_code, 200)
        self.assertEqual(res_head.content, b"")
        self.assertEqual(int(res_head.headers["content-length"]), total_len)

        # Explicit Range 0-1023
        res_r1 = self.client.get(seg_url, headers={"Range": "bytes=0-1023"})
        self.assertEqual(res_r1.status_code, 206)
        self.assertEqual(res_r1.headers["content-range"], f"bytes 0-1023/{total_len}")
        self.assertEqual(int(res_r1.headers["content-length"]), 1024)
        self.assertEqual(res_r1.content, seg_disk[:1024])

        # Open-ended Range 1024-
        res_r2 = self.client.get(seg_url, headers={"Range": "bytes=1024-"})
        self.assertEqual(res_r2.status_code, 206)
        self.assertEqual(res_r2.headers["content-range"], f"bytes 1024-{total_len - 1}/{total_len}")
        self.assertEqual(res_r2.content, seg_disk[1024:])

        # Suffix Range -512
        res_r3 = self.client.get(seg_url, headers={"Range": "bytes=-512"})
        self.assertEqual(res_r3.status_code, 206)
        self.assertEqual(res_r3.headers["content-range"], f"bytes {total_len - 512}-{total_len - 1}/{total_len}")
        self.assertEqual(res_r3.content, seg_disk[-512:])

        # HEAD with Range
        res_hr = self.client.head(seg_url, headers={"Range": "bytes=0-99"})
        self.assertEqual(res_hr.status_code, 206)
        self.assertEqual(int(res_hr.headers["content-length"]), 100)
        self.assertEqual(res_hr.content, b"")

        # Unsatisfiable / invalid ranges -> 416
        for bad_range in [f"bytes={total_len + 100}-", "bytes=500-100", "bytes=abc-def", "items=0-5"]:
            res_bad = self.client.get(seg_url, headers={"Range": bad_range})
            self.assertEqual(res_bad.status_code, 416, f"Expected 416 for {bad_range}")
            self.assertEqual(res_bad.headers["content-range"], f"bytes */{total_len}")

    def test_03_invalid_ids_traversal_and_blocked_file_types(self):
        """3. Verify invalid video IDs, path traversal, hidden dirs, and non-.ts files are rejected."""
        video_id, _ = self._prepare_hls_video()

        # Malformed / traversal video_id
        for bad_id in ["not-a-uuid", "..%5C..%5C"]:
            self.assertEqual(self.client.get(f"/api/videos/{bad_id}/hls/master.m3u8").status_code, 400)
            self.assertEqual(self.client.get(f"/api/videos/{bad_id}/hls/360p/playlist.m3u8").status_code, 400)
            self.assertEqual(self.client.get(f"/api/videos/{bad_id}/hls/360p/segment_000.ts").status_code, 400)

        # Non-existent UUID
        unknown = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.client.get(f"/api/videos/{unknown}/hls/master.m3u8").status_code, 404)

        # Disallowed file types, status JSON, MP4s, hidden dirs, traversal in segment/rendition
        blocked_paths = [
            f"/api/videos/{video_id}/hls/360p/video.mp4",
            f"/api/videos/{video_id}/hls/360p/hls_status.json",
            f"/api/videos/{video_id}/hls/360p/segment_000.txt",
            f"/api/videos/{video_id}/hls/.hls_staging/playlist.m3u8",
            f"/api/videos/{video_id}/hls/..%5C360p/video.mp4",
            f"/api/videos/{video_id}/hls/360p/..%5C..%5Ctranscode_status.json",
            f"/api/videos/{video_id}/hls/uploads/source.mp4",
        ]
        for url in blocked_paths:
            res = self.client.get(url)
            self.assertIn(res.status_code, (400, 404), f"Unexpected access allowed on {url}: {res.status_code}")

    def test_04_playlist_content_security_validation(self):
        """4. Verify playlists with external URLs, schemes, traversal, or symlinks are rejected."""
        video_id, _ = self._prepare_hls_video()
        hls_dir = self.processed_path / video_id / "hls"
        master_file = hls_dir / "master.m3u8"
        pl_file = hls_dir / "360p" / "playlist.m3u8"

        # Tamper master.m3u8 with unsafe URIs
        for bad_uri in [
            "http://evil.example.com/360p/playlist.m3u8",
            "file:///etc/passwd.m3u8",
            "../../uploads/secret.m3u8",
            "/absolute/playlist.m3u8",
        ]:
            master_file.write_text(
                f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\n{bad_uri}\n",
                encoding="utf-8",
            )
            res = self.client.get(f"/api/videos/{video_id}/hls/master.m3u8")
            self.assertEqual(res.status_code, 422, f"Expected 422 for master URI {bad_uri}")

        # Tamper rendition playlist.m3u8 with unsafe segment URIs
        for bad_seg in [
            "http://evil.example.com/seg.ts",
            "../480p/segment_000.ts",
            "../../video.mp4",
            "segment_000.mp4",
        ]:
            pl_file.write_text(
                f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\n{bad_seg}\n#EXT-X-ENDLIST\n",
                encoding="utf-8",
            )
            res = self.client.get(f"/api/videos/{video_id}/hls/360p/playlist.m3u8")
            self.assertEqual(res.status_code, 422, f"Expected 422 for segment URI {bad_seg}")

        # Symlink rejection
        with patch("pathlib.Path.is_symlink", return_value=True):
            res_sym = self.client.get(f"/api/videos/{video_id}/hls/360p/segment_000.ts")
            self.assertEqual(res_sym.status_code, 403)

    def test_05_missing_and_corrupted_media_files(self):
        """5. Verify 404 for missing media and 422 for 0-byte/corrupted playlists or segments."""
        video_id, _ = self._prepare_hls_video()
        hls_dir = self.processed_path / video_id / "hls"

        # Missing segment -> 404
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/hls/360p/segment_999.ts").status_code, 404)
        # Missing rendition -> 404
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/hls/1080p/playlist.m3u8").status_code, 404)

        # Corrupted 0-byte segment -> 422
        (hls_dir / "360p" / "segment_000.ts").write_bytes(b"")
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/hls/360p/segment_000.ts").status_code, 422)

        # Corrupted playlist missing #EXTM3U -> 422
        (hls_dir / "360p" / "playlist.m3u8").write_text("not an m3u8", encoding="utf-8")
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/hls/360p/playlist.m3u8").status_code, 422)

        # Missing master playlist -> 404
        (hls_dir / "master.m3u8").unlink()
        self.assertEqual(self.client.get(f"/api/videos/{video_id}/hls/master.m3u8").status_code, 404)

    def test_06_cors_preflight_and_exposed_headers(self):
        """6. Verify CORS headers allow local frontend origins and expose byte-range headers."""
        video_id, _ = self._prepare_hls_video()
        origin = "http://localhost:5173"

        pre = self.client.options(
            f"/api/videos/{video_id}/hls/master.m3u8",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Range",
            },
        )
        self.assertEqual(pre.status_code, 200)
        self.assertEqual(pre.headers.get("access-control-allow-origin"), origin)

        get_res = self.client.get(
            f"/api/videos/{video_id}/hls/360p/segment_000.ts",
            headers={"Origin": origin},
        )
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.headers.get("access-control-allow-origin"), origin)
        exposed = get_res.headers.get("access-control-expose-headers", "").lower()
        for expected_hdr in ["content-length", "content-range", "accept-ranges", "etag"]:
            self.assertIn(expected_hdr, exposed)

    def test_07_storage_relative_api_responses_and_playback_page(self):
        """7. Verify API responses hide OS paths and GET /player serves the playback verification page."""
        video_id, hls_data = self._prepare_hls_video()

        # No absolute host OS filesystem paths exposed in API responses
        run_dir_str = str(self.run_dir)
        self.assertEqual(hls_data["master_playlist_path"], f"{video_id}/hls/master.m3u8")
        self.assertEqual(hls_data["master_playlist_url"], f"/api/videos/{video_id}/hls/master.m3u8")
        self.assertNotIn(run_dir_str, hls_data["master_playlist_path"])
        for r in hls_data["renditions"]:
            self.assertEqual(r["playlist_path"], f"{video_id}/hls/{r['rendition']}/playlist.m3u8")
            self.assertEqual(r["playlist_url"], f"/api/videos/{video_id}/hls/{r['rendition']}/playlist.m3u8")
            self.assertNotIn(run_dir_str, r["playlist_path"])

        tc_status = self.client.get(f"/api/videos/{video_id}/transcode/status").json()
        for r in tc_status["successful_renditions"]:
            self.assertEqual(r["file_path"], f"{video_id}/{r['rendition']}/video.mp4")
            self.assertNotIn(run_dir_str, r["file_path"])

        # Playback verification page
        player_res = self.client.get("/player")
        self.assertEqual(player_res.status_code, 200)
        self.assertIn("text/html", player_res.headers["content-type"])
        self.assertIn("hls.min.js", player_res.text)
        self.assertIn("quality-select", player_res.text)

    def test_08_existing_hls_preserved_and_etag_changes_on_repackage(self):
        """8. Verify delivery never alters files and ETag updates when HLS is re-packaged."""
        video_id, _ = self._prepare_hls_video()
        hls_dir = self.processed_path / video_id / "hls"
        master_file = hls_dir / "master.m3u8"
        seg_file = hls_dir / "360p" / "segment_000.ts"

        master_hash = hashlib.sha256(master_file.read_bytes()).hexdigest()
        seg_hash = hashlib.sha256(seg_file.read_bytes()).hexdigest()

        r1 = self.client.get(f"/api/videos/{video_id}/hls/master.m3u8")
        self.client.get(f"/api/videos/{video_id}/hls/360p/segment_000.ts", headers={"Range": "bytes=0-255"})

        self.assertEqual(hashlib.sha256(master_file.read_bytes()).hexdigest(), master_hash)
        self.assertEqual(hashlib.sha256(seg_file.read_bytes()).hexdigest(), seg_hash)

        # Force re-package -> ETag / Last-Modified reflects updated file
        re_hls = self.client.post(f"/api/videos/{video_id}/hls?force=true")
        self.assertEqual(re_hls.status_code, 200)
        r2 = self.client.get(f"/api/videos/{video_id}/hls/master.m3u8")
        self.assertEqual(r2.status_code, 200)
        self.assertNotEqual(r1.headers["etag"], r2.headers["etag"])


if __name__ == "__main__":
    unittest.main()
