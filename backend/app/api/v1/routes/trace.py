"""Read-only Agent Trace HTTP adapters. Reconstruction lives in layers.trace."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.layers.trace import build_agent_trace, project_customer_progress
from app.schemas.trace import AgentTrace, CustomerProgress

router = APIRouter()


@router.get("/sessions/{session_ref}/trace", response_model=AgentTrace)
def get_session_trace(session_ref: str, db: Session = Depends(get_db)) -> AgentTrace:
    return build_agent_trace(db, session_ref)


@router.get("/sessions/{session_ref}/progress", response_model=CustomerProgress)
def get_session_progress(session_ref: str, db: Session = Depends(get_db)) -> CustomerProgress:
    return project_customer_progress(build_agent_trace(db, session_ref))
