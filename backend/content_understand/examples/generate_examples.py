"""Generate real Content Understanding example outputs for 3 educational video categories."""

import json
from pathlib import Path
import tempfile

from ..analyzer import ContentAnalyzer
from ..config import AnalysisConfig
from ..tests._synthetic_media import create_sample_educational_segments


def generate_all_examples() -> None:
    """Create 3 synthetic educational HLS videos, analyze them, and write example JSONs."""
    examples_dir = Path(__file__).resolve().parent
    samples = [
        ("lecture_slides_001", "lecture_slides", "lecture_slides_metadata.json"),
        ("coding_tutorial_001", "coding_tutorial", "coding_tutorial_metadata.json"),
        ("animated_explainer_001", "animated_explainer", "animated_explainer_metadata.json"),
    ]

    with tempfile.TemporaryDirectory(prefix="streamwise_examples_") as tmp_dir:
        storage_root = Path(tmp_dir) / "storage"
        config = AnalysisConfig(storage_dir=storage_root, frames_per_segment=3)
        analyzer = ContentAnalyzer(config=config)

        for video_id, sample_kind, filename in samples:
            seg_dir = storage_root / "processed" / video_id / "segments"
            create_sample_educational_segments(seg_dir, sample_kind=sample_kind, duration_sec=4.0)
            output_file = examples_dir / filename
            result = analyzer.analyze_video(video_id=video_id, output_path=output_file)
            print(
                f"Generated {output_file.name}: "
                f"segments={result.total_segments}, "
                f"avg_importance={sum(s.analysis.importance_score for s in result.segments) / result.total_segments:.4f}"
            )
            # Verify generated JSON round-trips cleanly
            json.loads(output_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    generate_all_examples()
