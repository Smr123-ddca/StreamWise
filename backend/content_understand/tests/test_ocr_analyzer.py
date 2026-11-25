"""Unit tests for OCR and text-density analysis (Tesseract + OpenCV fallback)."""

from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from backend.content_understand.ocr_analyzer import (
    analyze_frames_text_density,
    analyze_text_density,
)
from backend.content_understand.tests._synthetic_media import make_blank_frame, make_slide_frame


class OcrAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blank_frame_has_zero_text_density(self) -> None:
        blank = make_blank_frame(self.root / "blank.png", value=255)
        result = analyze_text_density(blank)
        self.assertEqual(result["text_density"], 0.0)
        self.assertFalse(result["has_significant_text"])
        self.assertEqual(result["word_count"], 0)

    def test_slide_frame_detects_significant_text_density(self) -> None:
        slide = make_slide_frame(
            self.root / "slide.png",
            "Machine Learning Optimization",
            [
                "Gradient descent updates parameters iteratively",
                "Learning rate controls convergence stability",
                "Regularization reduces model variance and overfitting",
                "Cross-validation evaluates generalization accuracy",
            ],
        )
        result = analyze_text_density(slide)
        self.assertGreater(result["text_density"], 0.05)
        self.assertTrue(result["has_significant_text"])
        self.assertIn(result["ocr_engine"], {"tesseract", "opencv_text_region"})

    def test_strict_mode_raises_when_tesseract_missing(self) -> None:
        slide = make_slide_frame(self.root / "slide.png", "Title", ["Bullet one"])
        with patch("backend.content_understand.ocr_analyzer.find_tesseract_executable", return_value=None):
            with self.assertRaises(RuntimeError):
                analyze_text_density(slide, fallback_mode="strict")

    def test_tesseract_path_processes_bounding_boxes_and_confidence(self) -> None:
        slide = make_slide_frame(self.root / "slide.png", "Title", ["Bullet one"])

        fake_pytesseract = types.ModuleType("pytesseract")
        fake_pytesseract.pytesseract = types.SimpleNamespace(tesseract_cmd="")
        fake_pytesseract.Output = types.SimpleNamespace(DICT="dict")
        fake_pytesseract.image_to_data = lambda *args, **kwargs: {
            "text": ["def", "calculate_loss", "boundary_ok", "noise", ""],
            "conf": ["95", "92", "60", "59", "-1"],
            "left": [10, 80, 10, 200, 0],
            "top": [10, 10, 60, 100, 0],
            "width": [60, 180, 100, 50, 0],
            "height": [40, 40, 30, 20, 0],
        }

        with (
            patch("backend.content_understand.ocr_analyzer.find_tesseract_executable", return_value="/usr/bin/tesseract"),
            patch.dict(sys.modules, {"pytesseract": fake_pytesseract}),
        ):
            result = analyze_text_density(slide, confidence_threshold=60)

        self.assertEqual(result["ocr_engine"], "tesseract")
        self.assertEqual(result["detected_text"], "def calculate_loss boundary_ok")
        self.assertEqual(result["word_count"], 3)
        self.assertGreater(result["text_density"], 0.0)
        self.assertLessEqual(result["text_density"], 1.0)

    def test_analyze_frames_text_density_reports_mixed_ocr_engines(self) -> None:
        slide = make_slide_frame(self.root / "slide.png", "Title", ["Bullet one"])
        blank = make_blank_frame(self.root / "blank.png")

        mocked_returns = [
            {
                "detected_text": "Hello",
                "text_density": 0.1,
                "word_count": 1,
                "has_significant_text": True,
                "ocr_engine": "tesseract",
            },
            {
                "detected_text": "",
                "text_density": 0.0,
                "word_count": 0,
                "has_significant_text": False,
                "ocr_engine": "opencv_text_region",
            },
        ]
        with patch("backend.content_understand.ocr_analyzer.analyze_text_density", side_effect=mocked_returns):
            agg = analyze_frames_text_density([slide, blank])
        self.assertEqual(agg["ocr_engine"], "mixed")

    def test_tesseract_runtime_error_falls_back_or_raises_in_strict_mode(self) -> None:
        slide = make_slide_frame(self.root / "slide.png", "Title", ["Bullet one"])
        fake_pytesseract = types.ModuleType("pytesseract")
        fake_pytesseract.TesseractError = type("TesseractError", (RuntimeError,), {})
        fake_pytesseract.pytesseract = types.SimpleNamespace(tesseract_cmd="")
        fake_pytesseract.Output = types.SimpleNamespace(DICT="dict")

        def _fail(*args, **kwargs):
            raise fake_pytesseract.TesseractError(1, "traineddata missing")

        fake_pytesseract.image_to_data = _fail

        with (
            patch("backend.content_understand.ocr_analyzer.find_tesseract_executable", return_value="/usr/bin/tesseract"),
            patch.dict(sys.modules, {"pytesseract": fake_pytesseract}),
        ):
            fallback_res = analyze_text_density(slide, fallback_mode="opencv_text_region")
            self.assertEqual(fallback_res["ocr_engine"], "opencv_text_region")

            with self.assertRaises(RuntimeError):
                analyze_text_density(slide, fallback_mode="strict")

    def test_none_word_and_small_sample_max_chars_handled_safely(self) -> None:
        slide = make_slide_frame(self.root / "slide.png", "Title", ["Bullet one"])
        fake_pytesseract = types.ModuleType("pytesseract")
        fake_pytesseract.pytesseract = types.SimpleNamespace(tesseract_cmd="")
        fake_pytesseract.Output = types.SimpleNamespace(DICT="dict")
        fake_pytesseract.image_to_data = lambda *args, **kwargs: {
            "text": [None, "abcdef"],
            "conf": ["95", "95"],
            "left": [0, 10],
            "top": [0, 10],
            "width": [10, 80],
            "height": [10, 30],
        }

        with (
            patch("backend.content_understand.ocr_analyzer.find_tesseract_executable", return_value="/usr/bin/tesseract"),
            patch.dict(sys.modules, {"pytesseract": fake_pytesseract}),
        ):
            agg = analyze_frames_text_density([slide], text_sample_max_chars=2)
        self.assertEqual(agg["detected_text_sample"], "ab")

    def test_analyze_frames_text_density_aggregates_frames(self) -> None:
        blank = make_blank_frame(self.root / "blank.png")
        slide = make_slide_frame(
            self.root / "slide.png",
            "Summary",
            ["Point 1: Visual detail", "Point 2: Text density"],
        )
        agg = analyze_frames_text_density([blank, slide], text_sample_max_chars=50)
        self.assertGreater(agg["text_density"], 0.0)
        self.assertLessEqual(agg["text_density"], 1.0)
        self.assertLessEqual(len(agg["detected_text_sample"]), 50)


if __name__ == "__main__":
    unittest.main()
