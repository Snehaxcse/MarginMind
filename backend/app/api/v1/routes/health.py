"""Health check. No commercial logic."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/health")
def health_v1() -> dict[str, str]:
    return {"status": "ok"}
