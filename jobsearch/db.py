"""Database engine and session helpers for Job Search OS."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = "sqlite:///./jobsearch.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
    expire_on_commit=False,
)


def build_database_url(path: str = "jobsearch.db") -> str:
    """Build the SQLite database URL for the application."""

    return f"sqlite:///./{path}"


def get_engine(database_url: str | None = None):
    """Create the SQLAlchemy engine for the application database."""

    if database_url in (None, DATABASE_URL):
        return engine

    return create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )


def get_session_factory(engine_override=None):
    """Create the SQLAlchemy session factory."""

    if engine_override is None or engine_override is engine:
        return SessionLocal

    return sessionmaker(
        bind=engine_override,
        autocommit=False,
        autoflush=False,
        class_=Session,
        expire_on_commit=False,
    )


def get_session() -> Session:
    """Return a database session from the configured session factory."""

    return SessionLocal()


@contextmanager
def get_db() -> Iterator[Session]:
    """Yield a database session and close it when the context exits."""

    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
