import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # import modules from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent))  # and shared fakes from tests/


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Variables left in the developer's terminal or .env must not change test behaviour."""
    for name in ("DATABASE_URL", "API_KEY", "REVIEW_TOTAL_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    import db
    db.reset()
    yield
    db.reset()


@pytest.fixture
def database():
    """An in-memory SQLite database wired into the app for the duration of one test."""
    import db
    db.configure("sqlite://")
    return db
