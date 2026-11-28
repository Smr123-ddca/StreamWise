"""HTTP API for decision requests."""

from fastapi import APIRouter, FastAPI, HTTPException

from .models import ValidationError, as_json, request_from_dict, to_json_schema
from .service import DecisionService

router = APIRouter()
app = FastAPI(title="StreamWise Decision Intelligence")
service = DecisionService()


@router.post("/decide")
def decide(payload: dict):
    """Return a validated, explainable rendition allocation."""
    try:
        return as_json(service.decide(request_from_dict(payload)))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/health")
def health():
    """Return decision service status."""
    return {"status": "healthy", "service": "decision_engine"}


@router.get("/schema")
def schema():
    """Return the metadata contract schema."""
    return to_json_schema()


app.include_router(router)
