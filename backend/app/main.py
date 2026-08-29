"""FastAPI application entrypoint.

Thin HTTP adapters over deterministic services. M11 adds read-only Agent Trace.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.api.v1.routes import health
from app.layers.approval import ApprovalServiceError
from app.layers.checkout import CheckoutServiceError
from app.layers.session import SessionServiceError

app = FastAPI(
    title="MarginMind API",
    version="0.11.0",
    description="Policy-controlled AI merchant-growth decision engine.",
)
app.include_router(health.router)
app.include_router(api_router)

_HTTP = {
    "unknown_session": 404,
    "unknown_approval": 404,
    "unknown_checkout": 404,
    "unknown_basket": 404,
    "unknown_merchant": 404,
    "unknown_customer": 404,
    "client_amount_not_authoritative": 400,
    "session_mismatch": 400,
    "approval_not_granted": 409,
    "revalidation_failed": 409,
    "amount_mismatch": 409,
    "checkout_not_retryable": 409,
    "checkout_not_reportable": 409,
    "provider_failed": 502,
    "not_configured": 503,
}


def _error_payload(code: str, message: str, **extra) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    for key, value in extra.items():
        if value:
            body["error"][key] = value
    return body


@app.exception_handler(CheckoutServiceError)
async def checkout_error(_request: Request, exc: CheckoutServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=_HTTP.get(exc.code, 400),
        content=_error_payload(
            exc.code,
            exc.message,
            failure_reasons=exc.failure_reasons,
            revalidation_ref_id=exc.revalidation_ref_id,
            checkout_attempt_ref=exc.checkout_attempt_ref,
        ),
    )


@app.exception_handler(SessionServiceError)
async def session_error(_request: Request, exc: SessionServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=_HTTP.get(exc.code, 400),
        content=_error_payload(exc.code, exc.message),
    )


@app.exception_handler(ApprovalServiceError)
async def approval_error(_request: Request, exc: ApprovalServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=_HTTP.get(exc.code, 400),
        content=_error_payload(exc.code, exc.message),
    )
