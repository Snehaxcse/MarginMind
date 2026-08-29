"""API v1 router. Keep routes thin; services own commercial truth."""

from fastapi import APIRouter

from app.api.v1.routes import checkout, trace, webhooks

api_router = APIRouter()
api_router.include_router(checkout.router, prefix="/api/v1", tags=["checkout"])
api_router.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
api_router.include_router(trace.router, prefix="/api/v1", tags=["trace"])
