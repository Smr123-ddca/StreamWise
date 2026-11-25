# Testing Strategy

Existing backend tests use pytest-compatible unittest classes and FastAPI's
`TestClient`. New decision tests cover pure model validation, utility
monotonicity/diminishing returns, deterministic allocation, bandwidth and
buffer constraints, priority/data-saver behavior, switching, and all decision
HTTP endpoints. Examples are run as a separate smoke check.
