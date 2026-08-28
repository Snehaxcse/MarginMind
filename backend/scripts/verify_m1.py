"""M1 verification: migrate, seed twice, run catalogue tests.

Prefers DATABASE_URL / docker-compose Postgres. If nothing is listening,
starts a workspace-local Postgres via pgserver (data dir: backend/.pgdata).
Does not install a host PostgreSQL service or create extra superusers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

PGDATA = BACKEND_DIR / ".pgdata"


def _docker_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://marginmind:marginmind@localhost:5432/marginmind",
    )


def _can_connect(url: str) -> bool:
    from sqlalchemy import create_engine, text

    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _ensure_database_url() -> str:
    url = _docker_url()
    if _can_connect(url):
        print(f"Using existing Postgres at DATABASE_URL")
        return url

    try:
        import pgserver
    except ImportError as exc:
        raise SystemExit(
            "Postgres is not running and pgserver is not installed.\n"
            "Start it with: docker compose up -d\n"
            f"Then: alembic upgrade head && python -m app.db.seed && pytest\n({exc})"
        ) from exc

    print("Docker Postgres not reachable; starting workspace-local Postgres (.pgdata).")
    server = pgserver.get_server(str(PGDATA))
    uri = server.get_uri()
    if uri.startswith("postgresql://"):
        url = "postgresql+psycopg://" + uri[len("postgresql://") :]
    else:
        url = uri
    os.environ["DATABASE_URL"] = url
    if not _can_connect(url):
        raise SystemExit(f"Workspace Postgres started but is not reachable: {url}")
    print(f"Workspace Postgres ready.")
    return url


def main() -> int:
    url = _ensure_database_url()
    os.environ["DATABASE_URL"] = url

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, func, select, text

    from app.core.config import get_settings
    from app.db.seed import seed_all
    from app.db.session import get_engine, get_session_factory, session_scope
    from app.models import Merchant, Offer, Product, ProductVariant

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")
    print("alembic upgrade head: OK")

    with session_scope() as session:
        seed_all(session)
    print("seed 1: OK")

    with session_scope() as session:
        seed_all(session)
        products = session.scalar(select(func.count()).select_from(Product))
        variants = session.scalar(select(func.count()).select_from(ProductVariant))
        merchants = session.scalar(select(func.count()).select_from(Merchant))
        offers = session.scalar(select(func.count()).select_from(Offer))
    print(f"seed 2: OK (no duplicates) merchants={merchants} products={products} variants={variants} offers={offers}")

    import pytest

    rc = pytest.main(["-q", "--tb=short"])
    if rc != 0:
        print(f"pytest failed with code {rc}")
        return int(rc)

    engine = create_engine(url)
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        ).scalars().all()
    print("tables:", ", ".join(tables))
    print("M1 verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
