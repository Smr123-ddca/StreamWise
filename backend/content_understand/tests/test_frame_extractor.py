"""Unit tests for FFmpeg frame extraction and FFprobe duration probing."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.content_understand.frame_extractor import (
    MediaExtractionError,
    extract_frames,
    probe_media_duration,
)
from backend.content_understand.tests._synthetic_media import make_slide_frame, render_ts_segment


class FrameExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extract_frames_from_real_ts_segment(self) -> None:
        import cv2

        slide = make_slide_frame(
            self.root / "slide.png",
            "Frame Extraction Test",
            ["Bullet One", "Bullet Two"],
        )
        rgb = cv2.cvtColor(cv2.imread(str(slide)), cv2.COLOR_BGR2RGB)
        seg_path = render_ts_segment(
            self.root / "seg_000.ts",
            lambda _: rgb,
            duration_sec=2.0,
        )

        duration = probe_media_duration(seg_path)
        self.assertIsNotNone(duration)
        self.assertGreater(duration, 0.5)

        frames_dir = self.root / "frames"
        frames = extract_frames(seg_path, frames_dir, num_frames=3)
        self.assertEqual(len(frames), 3)
        for frame in frames:
            self.assertTrue(frame.is_file())
            self.assertGreater(frame.stat().st_size, 0)

    def test_missing_segment_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            extract_frames(self.root / "missing.ts", self.root / "out", num_frames=2)

    def test_invalid_num_frames_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            extract_frames(self.root / "seg.ts", self.root / "out", num_frames=0)
        with self.assertRaises(ValueError):
            extract_frames(self.root / "seg.ts", self.root / "out", num_frames=1, timeout_sec=0)
        with self.assertRaises(ValueError):
            probe_media_duration(self.root / "seg.ts", timeout_sec=0)

    def test_timeouts_and_corrupt_media_handling(self) -> None:
        dummy_seg = self.root / "corrupt.ts"
        dummy_seg.write_bytes(b"not a valid transport stream")

        with patch(
            "backend.content_understand.frame_extractor.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=5),
        ):
            self.assertIsNone(probe_media_duration(dummy_seg))

        with (
            patch("backend.content_understand.frame_extractor.probe_media_duration", return_value=2.0),
            patch(
                "backend.content_understand.frame_extractor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5),
            ),
        ):
            with self.assertRaises(TimeoutError):
                extract_frames(dummy_seg, self.root / "out_timeout", num_frames=2)

        with self.assertRaises(MediaExtractionError):
            extract_frames(dummy_seg, self.root / "out_corrupt", num_frames=2)

    def test_long_segment_does_not_clamp_fps_to_zero_point_one(self) -> None:
        dummy_seg = self.root / "long.ts"
        dummy_seg.write_bytes(b"placeholder")
        out_dir = self.root / "out_long"
        captured_cmds = []

        def _fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            (out_dir / "frame_001.jpg").write_bytes(b"jpg")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch("backend.content_understand.frame_extractor.probe_media_duration", return_value=60.0),
            patch("backend.content_understand.frame_extractor.get_ffmpeg_executable", return_value="ffmpeg"),
            patch("backend.content_understand.frame_extractor.subprocess.run", side_effect=_fake_run),
        ):
            extract_frames(dummy_seg, out_dir, num_frames=3)

        self.assertIn("fps=0.050000", captured_cmds[0])


if __name__ == "__main__":
    unittest.main()
