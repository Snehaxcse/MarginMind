"""HTTP dependencies. Thin adapters only."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.layers.payments import PaymentProvider, get_payment_provider


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def payment_provider() -> PaymentProvider:
    return get_payment_provider()
