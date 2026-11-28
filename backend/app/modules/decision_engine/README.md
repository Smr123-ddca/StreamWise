# Decision Intelligence

Run the API from the repository root with `uvicorn backend.app.main:app --reload`.
Use `POST /decide` with the JSON contract in `docs/API_CONTRACTS.md`; `GET
/schema` returns the machine-readable request schema.

The allocator starts every segment at the lowest permitted rendition, calculates
content priority, and greedily applies the highest positive marginal-utility
upgrade that stays within the throughput budget and simulated buffer floor.
Buffer headroom adjusts the budget, viewer caps restrict the ladder, and
switching penalties discourage abrupt resolution changes. Responses include
structured and human-readable explanations for every decision.

The direct integration point is:

```python
from backend.app.modules.decision_engine.service import DecisionService
response = DecisionService().decide(request)
```
