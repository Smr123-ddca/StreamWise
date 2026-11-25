"""Unit tests for Canny edge visual complexity analysis."""

from pathlib import Path
import tempfile
import unittest

from backend.content_understand.complexity_analyzer import (
    analyze_frames_complexity,
    calculate_visual_complexity,
)
from backend.content_understand.config import AnalysisConfig
from backend.content_understand.tests._synthetic_media import (
    make_blank_frame,
    make_code_frame,
    make_slide_frame,
)


class ComplexityAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blank_frame_has_zero_complexity(self) -> None:
        blank = make_blank_frame(self.root / "blank.png", value=255)
        score = calculate_visual_complexity(blank)
        self.assertEqual(score, 0.0)

    def test_code_screen_has_higher_complexity_than_blank(self) -> None:
        blank = make_blank_frame(self.root / "blank.png", value=255)
        code = make_code_frame(
            self.root / "code.png",
            [
                "import cv2, numpy as np",
                "def calculate_loss(y_pred, y_true):",
                "    residual = np.abs(y_pred - y_true)",
                "    return float(np.mean(residual ** 2))",
                "for idx in range(100):",
                "    print(idx, calculate_loss(idx, idx * 0.9))",
            ],
        )
        blank_score = calculate_visual_complexity(blank)
        code_score = calculate_visual_complexity(code)
        self.assertGreater(code_score, blank_score)
        self.assertGreater(code_score, 0.05)
        self.assertLessEqual(code_score, 1.0)

    def test_analyze_frames_complexity_averages_scores(self) -> None:
        blank = make_blank_frame(self.root / "blank.png")
        slide = make_slide_frame(
            self.root / "slide.png",
            "Complexity Comparison",
            ["Line one of text", "Line two of text", "Line three of text"],
        )
        avg = analyze_frames_complexity([blank, slide])
        self.assertGreater(avg, 0.0)
        self.assertLessEqual(avg, 1.0)

    def test_missing_image_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            calculate_visual_complexity(self.root / "missing.png")

    def test_invalid_canny_thresholds_and_norm_factor_raise_value_error(self) -> None:
        blank = make_blank_frame(self.root / "blank.png", value=255)
        with self.assertRaises(ValueError):
            calculate_visual_complexity(blank, canny_threshold1=200, canny_threshold2=50)
        with self.assertRaises(ValueError):
            calculate_visual_complexity(blank, normalization_factor=0.0)
        with self.assertRaises(ValueError):
            AnalysisConfig(canny_threshold1=200, canny_threshold2=50)


if __name__ == "__main__":
    unittest.main()
