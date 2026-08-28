"""FastAPI application entrypoint.

M1: app object only. No product routes, payments, or LLM wiring.
"""

from fastapi import FastAPI

app = FastAPI(
    title="MarginMind API",
    version="0.1.0",
    description="Policy-controlled AI merchant-growth decision engine.",
)
