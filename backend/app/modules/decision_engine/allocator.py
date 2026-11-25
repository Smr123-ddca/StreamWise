"""Deterministic marginal-utility greedy allocator."""

from .explain import build_explanation
from .models import DecisionRequest, DecisionResponse, SegmentDecision
from .priority import compute_priority
from .utility import estimate_utility, marginal_utility


def _allowed(segment, max_height):
    allowed = [r for r in segment.renditions if max_height is None or r.height <= max_height]
    return allowed or [min(segment.renditions, key=lambda r: r.height)]


def _simulate(request, selected, throughput):
    buffer = request.player.buffer_s
    minimum = buffer
    for segment, rendition in zip(request.segments, selected):
        download = rendition.bitrate_kbps * segment.duration_s / throughput
        buffer = buffer - download + segment.duration_s
        minimum = min(minimum, buffer)
    return minimum


def allocate(request: DecisionRequest) -> DecisionResponse:
    """Allocate renditions using budget = throughput * safety margin * duration.

    The base budget is adjusted by buffer headroom: above-minimum buffer permits
    additional spending and below-minimum buffer shrinks it. Every upgrade must
    also pass a segment-by-segment buffer simulation.
    """
    throughput = request.network.throughput_estimate_kbps * request.network.safety_margin
    duration = sum(s.duration_s for s in request.segments)
    headroom = (request.player.buffer_s - request.player.min_buffer_s) / max(1, request.player.max_buffer_s - request.player.min_buffer_s)
    budget = throughput * duration * max(.25, 1 + request.config.safety_buffer_factor * headroom)
    choices = [_allowed(s, request.viewer.max_height) for s in request.segments]
    selected = [ladder[0] for ladder in choices]
    used = sum(r.bitrate_kbps * s.duration_s for s, r in zip(request.segments, selected))
    binding: list[str] = []
    if used > budget:
        binding.extend(["bandwidth", "buffer"])
    while True:
        candidates = []
        for i, (segment, ladder, current) in enumerate(zip(request.segments, choices, selected)):
            pos = ladder.index(current)
            if pos + 1 >= len(ladder):
                continue
            higher = ladder[pos + 1]
            extra = (higher.bitrate_kbps - current.bitrate_kbps) * segment.duration_s
            gain = compute_priority(segment, request.viewer) * marginal_utility(segment, current, higher)
            previous = request.player.last_rendition_id if i == 0 else selected[i - 1].id
            previous_rendition = next((r for r in ladder if r.id == previous), current) if i == 0 else selected[i - 1]
            previous_height = previous_rendition.height
            switch_cost = request.config.switching_penalty * abs(higher.height - previous_height) / max(1, higher.height)
            score = (gain - switch_cost) / max(extra, 1e-9)
            candidates.append((-score, segment.index, i, higher, extra, switch_cost, previous))
        candidates.sort()
        upgraded = False
        for _, _, i, higher, extra, switch_cost, _ in candidates:
            segment = request.segments[i]
            gain = compute_priority(segment, request.viewer) * marginal_utility(segment, selected[i], higher)
            if gain - switch_cost <= 0 or used + extra > budget:
                continue
            trial = selected[:]
            trial[i] = higher
            if _simulate(request, trial, throughput) < request.player.min_buffer_s:
                continue
            selected = trial
            used += extra
            upgraded = True
            break
        if not upgraded:
            break
    min_buffer = _simulate(request, selected, throughput)
    if min_buffer < request.player.min_buffer_s:
        binding.append("buffer")
    if any(request.viewer.max_height is not None and r.height >= request.viewer.max_height for r in selected):
        binding.append("viewer_cap")
    decisions = []
    for segment, ladder, selected_r in zip(request.segments, choices, selected):
        pos = ladder.index(selected_r)
        lower = ladder[pos - 1] if pos else None
        higher = ladder[pos + 1] if pos + 1 < len(ladder) else None
        reason = "ladder_top" if higher is None else ("bandwidth" if used >= budget else "buffer")
        decisions.append(SegmentDecision(segment.segment_id, selected_r.id, selected_r.bitrate_kbps, compute_priority(segment, request.viewer), estimate_utility(selected_r, segment), build_explanation(segment, selected_r, request.viewer, reason, False, lower, higher)))
    return DecisionResponse(decisions, budget, used, min_buffer, sorted(set(binding)))
