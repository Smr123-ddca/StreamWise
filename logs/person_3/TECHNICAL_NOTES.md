# Technical Notes

Status: AI-generated, pending review by Bagchi.

## Definitions

Marginal utility is the quality gain from a one-step ladder upgrade. Greedy allocation
repeatedly takes the best score available now. A rolling window is the upcoming ordered
segment list. Safety margin discounts throughput. Switching penalty discourages adjacent
level changes. VMAF is a 0–100 perceptual score. Saturating utility grows logarithmically,
so each additional bitrate step contributes less than the previous one.

## Algorithm

1. Validate the metadata and keep renditions sorted.
2. Compute a throughput-and-buffer-adjusted window budget.
3. Start each segment at its lowest rendition allowed by the viewer cap.
4. Score single-step upgrades by priority-weighted marginal gain per extra kbit.
5. Reject upgrades that exceed budget or make simulated buffer fall below the floor.
6. Repeat until no positive feasible upgrade remains, then explain each result.

## Design decisions and assumptions

- Dataclasses were chosen for a small pure core; Pydantic was not introduced for internal models.
- The existing backend module path was preferred over creating a parallel package.
- Rendition bitrate times duration estimates segment size; audio overhead is not modeled.
- Buffer simulation assumes the safety-adjusted throughput remains constant across the window.
- A missing VMAF uses a normalized logarithmic bitrate model with resolution fit.

## Viva questions

1. Why is marginal utility better than maximizing bitrate?
2. How does the safety margin affect both budget and buffer simulation?
3. Why must the ladder be sorted?
4. What makes the allocator deterministic?
5. How does content complexity affect utility?
6. What does a binding constraint tell the playback controller?
7. Why is switching penalty applied to neighbors?
8. What are the limitations of constant-throughput simulation?
9. How does data-saver change allocation?
10. What contract does Person 2 need to emit?

Reviewed by Bagchi: [ ]
