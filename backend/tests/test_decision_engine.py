import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.modules.decision_engine.allocator import allocate
from backend.app.modules.decision_engine.models import DecisionRequest, NetworkState, PlayerState, PreferenceMode, Rendition, SceneType, SegmentMetadata, ValidationError, ViewerPreferences, as_json
from backend.app.modules.decision_engine.priority import compute_priority
from backend.app.modules.decision_engine.utility import estimate_utility


LADDER = [Rendition("360p", 600, 360, 640), Rendition("720p", 1800, 720, 1280), Rendition("1080p", 3500, 1080, 1920)]


def request(throughput=3000, buffer=15, scenes=None, mode=PreferenceMode.BALANCED, last=None):
    scenes = scenes or [SceneType.ACTION, SceneType.STATIC, SceneType.ACTION]
    segments = [SegmentMetadata(f"s{i}", i, 4, .9 if s == SceneType.ACTION else .1, .9 if s == SceneType.ACTION else .1, .8 if s == SceneType.ACTION else .1, s, list(LADDER)) for i, s in enumerate(scenes)]
    return DecisionRequest(segments, NetworkState(throughput), PlayerState(buffer, last_rendition_id=last), ViewerPreferences(mode))


def test_utility_monotonic_diminishing_and_vmaf():
    segment = request().segments[0]
    values = [estimate_utility(r, segment) for r in LADDER]
    assert values[0] < values[1] < values[2]
    assert values[1] - values[0] > values[2] - values[1]
    assert estimate_utility(Rendition("v", 100, 100, 100, 80), segment) == .8


def test_bandwidth_and_determinism():
    req = request(throughput=2200)
    one, two = allocate(req), allocate(req)
    assert as_json(one) == as_json(two)
    assert one.used_kbit <= one.window_budget_kbit


def test_priority_and_data_saver():
    high, low = request().segments[0], request().segments[1]
    assert compute_priority(high, ViewerPreferences()) > compute_priority(low, ViewerPreferences())
    assert allocate(request(mode=PreferenceMode.DATA_SAVER)).used_kbit <= allocate(request()).used_kbit


def test_low_buffer_and_validation():
    result = allocate(request(throughput=1800, buffer=5))
    assert result.predicted_min_buffer_s >= 5
    with pytest.raises(ValidationError):
        Rendition("bad", 0, 360, 640)


def test_api_endpoints():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/schema").status_code == 200
    assert client.post("/decide", json={}).status_code == 422
    payload = as_json(request())
    assert client.post("/decide", json=payload).status_code == 200
