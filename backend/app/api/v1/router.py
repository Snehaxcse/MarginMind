"""API v1 router. Keep routes thin; services own commercial truth."""

from fastapi import APIRouter

from app.api.v1.routes import checkout

api_router = APIRouter()
api_router.include_router(checkout.router, prefix="/api/v1", tags=["checkout"])
