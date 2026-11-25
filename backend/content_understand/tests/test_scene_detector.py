"""Unit tests for OpenCV histogram scene detection and PySceneDetect wrapper."""

from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np

from backend.content_understand.scene_detector import (
    compute_histogram_distance,
    detect_scene_change,
    detect_segment_scene_change,
    get_scene_changes,
)
from backend.content_understand.tests._synthetic_media import (
    make_blank_frame,
    make_code_frame,
    make_slide_frame,
    render_ts_segment,
)


class SceneDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_identical_frames_no_scene_change(self) -> None:
        f1 = make_slide_frame(self.root / "f1.png", "Title", ["Point A", "Point B"])
        f2 = make_slide_frame(self.root / "f2.png", "Title", ["Point A", "Point B"])
        distance = compute_histogram_distance(f1, f2)
        self.assertLess(distance, 0.05)
        self.assertFalse(detect_scene_change(f1, f2, threshold=0.40))

    def test_different_scenes_trigger_scene_change(self) -> None:
        light_slide = make_blank_frame(self.root / "light.png", value=250)
        dark_code = make_code_frame(
            self.root / "dark.png",
            ["def foo():", "    return 42"],
        )
        distance = compute_histogram_distance(light_slide, dark_code)
        self.assertGreater(distance, 0.40)
        self.assertTrue(detect_scene_change(light_slide, dark_code, threshold=0.40))

    def test_inter_segment_scene_change_detected(self) -> None:
        prev_last = make_blank_frame(self.root / "prev.png", value=250)
        curr_1 = make_code_frame(self.root / "c1.png", ["x = 1", "y = 2"])
        curr_2 = make_code_frame(self.root / "c2.png", ["x = 1", "y = 2"])
        self.assertTrue(
            detect_segment_scene_change([curr_1, curr_2], prev_segment_last_frame=prev_last)
        )

    def test_pyscenedetect_detects_cut_in_video(self) -> None:
        light = np.full((360, 640, 3), 245, dtype=np.uint8)
        dark = np.full((360, 640, 3), 15, dtype=np.uint8)
        ts_path = render_ts_segment(
            self.root / "cut.ts",
            lambda i: light if i < 16 else dark,
            duration_sec=2.0,
            fps=16,
        )
        cuts = get_scene_changes(ts_path, threshold=20.0, min_scene_len=5)
        self.assertGreaterEqual(len(cuts), 1)


if __name__ == "__main__":
    unittest.main()
