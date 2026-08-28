"""Shared fixtures: tests run against the local Postgres seed."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.seed import seed_all
from app.db.session import get_session_factory


@pytest.fixture
def db() -> Session:
    session = get_session_factory()()
    try:
        seed_all(session)
        session.commit()
        yield session
    finally:
        session.close()
