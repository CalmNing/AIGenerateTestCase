"""Smoke test: confirms the in-memory DB fixture and imports work."""
from db.models import ApiProject


def test_db_session_fixture_creates_tables(db_session):
    project = ApiProject(name="smoke-project")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    assert project.id is not None
    assert project.name == "smoke-project"
