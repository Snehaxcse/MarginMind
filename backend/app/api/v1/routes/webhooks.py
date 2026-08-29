"""Razorpay webhook adapter. Reads raw bytes before any JSON parse."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, payment_provider
from app.layers.checkout import process_webhook
from app.layers.payments import PaymentProvider

router = APIRouter()


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    provider: PaymentProvider = Depends(payment_provider),
) -> JSONResponse:
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    event_id = request.headers.get("x-razorpay-event-id")
    result = process_webhook(
        db,
        provider,
        body=body,
        signature=signature,
        event_id=event_id,
    )
    payload = {
        "ok": result.http_status < 400,
        "status": result.processing_status.value,
        "webhook_ref": result.webhook_ref,
        "reason": result.failure_reason,
        "duplicate": result.duplicate,
        "payment_verified": result.payment_verified,
    }
    return JSONResponse(status_code=result.http_status, content=payload)
