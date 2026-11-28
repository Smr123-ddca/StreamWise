"""Unit tests for contextual importance scoring and content-type hints."""

import unittest

from backend.content_understand.importance_scorer import (
    calculate_importance_score,
    infer_content_type_hint,
)


class ImportanceScorerTests(unittest.TestCase):
    def test_importance_score_always_bounded_zero_to_one(self) -> None:
        low = calculate_importance_score(0.0, 0.0, False, False)
        high = calculate_importance_score(1.5, 1.5, True, True)
        self.assertEqual(low, 0.0)
        self.assertEqual(high, 1.0)

    def test_custom_weights_applied_accurately(self) -> None:
        score = calculate_importance_score(
            visual_complexity=0.5,
            text_density=0.5,
            scene_change=True,
            has_significant_text=False,
            weights={
                "visual_complexity": 0.4,
                "text_density": 0.4,
                "scene_change_bonus": 0.2,
                "text_flag_bonus": 0.0,
            },
        )
        self.assertEqual(score, 0.6)

    def test_content_type_hints_reflect_signals(self) -> None:
        self.assertEqual(
            infer_content_type_hint(
                visual_complexity=0.6,
                text_density=0.3,
                has_significant_text=True,
                scene_change=False,
                detected_text_sample="def calculate_loss(y_pred, y_true):",
            ),
            "code_screen",
        )
        self.assertEqual(
            infer_content_type_hint(
                visual_complexity=0.25,
                text_density=0.18,
                has_significant_text=True,
                scene_change=False,
            ),
            "presentation_slide",
        )
        self.assertEqual(
            infer_content_type_hint(
                visual_complexity=0.05,
                text_density=0.0,
                has_significant_text=False,
                scene_change=False,
            ),
            "talking_head_or_simple",
        )

    def test_curly_braces_in_math_slide_do_not_trigger_code_screen(self) -> None:
        hint = infer_content_type_hint(
            visual_complexity=0.20,
            text_density=0.18,
            has_significant_text=True,
            scene_change=False,
            detected_text_sample="Set notation: {x in R | x > 0}",
        )
        self.assertEqual(hint, "presentation_slide")


if __name__ == "__main__":
    unittest.main()
