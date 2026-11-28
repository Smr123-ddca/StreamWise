"""Generate four deterministic decision-engine scenarios."""

import json
from pathlib import Path

from backend.app.modules.decision_engine.models import DecisionRequest, NetworkState, PlayerState, PreferenceMode, Rendition, SceneType, SegmentMetadata, ViewerPreferences, as_json
from backend.app.modules.decision_engine.service import DecisionService

ROOT = Path(__file__).parent / "results"
LADDER = [Rendition("360p", 600, 360, 640), Rendition("720p", 1800, 720, 1280), Rendition("1080p", 3500, 1080, 1920)]


def make_request(throughput, buffer, scenes, mode=PreferenceMode.BALANCED):
    segments = [SegmentMetadata(f"seg-{i:02d}", i, 4, .85 if scene == SceneType.ACTION else .2, .9 if scene == SceneType.ACTION else .1, .8 if scene == SceneType.ACTION else .2, scene, list(LADDER), .2 if scene == SceneType.ACTION else 0) for i, scene in enumerate(scenes)]
    return DecisionRequest(segments, NetworkState(throughput), PlayerState(buffer), ViewerPreferences(mode))


def main():
    scenarios = {"stable_high_bandwidth": make_request(6000, 15, [SceneType.ACTION] * 4), "bandwidth_drop_mid_window": make_request(2200, 12, [SceneType.ACTION, SceneType.ACTION, SceneType.STATIC, SceneType.STATIC]), "low_buffer": make_request(1800, 5, [SceneType.ACTION] * 4), "action_vs_static": make_request(2200, 15, [SceneType.ACTION, SceneType.STATIC, SceneType.ACTION, SceneType.STATIC])}
    service = DecisionService()
    ROOT.mkdir(parents=True, exist_ok=True)
    report = ["# Decision examples", ""]
    for name, request in scenarios.items():
        response = service.decide(request)
        (ROOT / f"{name}.json").write_text(json.dumps(as_json(response), indent=2), encoding="utf-8")
        report += [f"## {name}", f"Selected: {', '.join(d.rendition_id for d in response.decisions)}", f"Budget/used: {response.window_budget_kbit:.1f}/{response.used_kbit:.1f} kbit", ""]
    (ROOT / "report.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
