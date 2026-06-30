"""Shared pytest fixtures for the backend test suite.

All fixtures use in-memory SQLite — no real database file is touched.
"""
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

# Importing db.models registers every table on SQLModel.metadata.
import db.models  # noqa: F401


@pytest.fixture()
def db_engine():
    """Fresh in-memory SQLite engine with all tables created, per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """In-memory SQLModel Session bound to the per-test engine."""
    with Session(db_engine) as session:
        yield session
