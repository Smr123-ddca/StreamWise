# API Contracts

## Segment Metadata -> Decision Service

`POST /decide` accepts a rolling window of ordered segments. Bitrates are in
Kbit/s, segment sizes are calculated as `bitrate_kbps * duration_s`, and all
normalized content fields are in `[0, 1]`. Renditions must be sorted in strictly
increasing bitrate order.

```json
{
  "segments": [{
    "segment_id": "seg-0001", "index": 0, "duration_s": 4,
    "complexity": 0.8, "motion": 0.9, "saliency": 0.7,
    "scene_type": "action", "face_presence": 0.2,
    "renditions": [
      {"id": "360p", "bitrate_kbps": 600, "height": 360, "width": 640},
      {"id": "720p", "bitrate_kbps": 1800, "height": 720, "width": 1280},
      {"id": "1080p", "bitrate_kbps": 3500, "height": 1080, "width": 1920}
    ]
  }],
  "network": {"throughput_estimate_kbps": 3000, "throughput_variance": 200, "safety_margin": 0.8},
  "player": {"buffer_s": 12, "min_buffer_s": 5, "max_buffer_s": 30, "last_rendition_id": "360p"},
  "viewer": {"mode": "balanced", "scene_weights": {"action": 1.2}, "max_height": 1080},
  "config": {"switching_penalty": 0.04, "safety_buffer_factor": 0.5}
}
```

The response contains one decision per segment, the computed window budget and
usage, the minimum simulated buffer, and any binding constraints. Validation
errors are returned as HTTP 422 responses.
