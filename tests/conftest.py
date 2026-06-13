import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret-abc123")
os.environ.setdefault("GITHUB_TOKEN", "ghp_test000000000000000000000000000000")
os.environ.setdefault("GOOGLE_API_KEY", "test-gemini-key")
os.environ.setdefault("SHADOW_MODE", "true")
os.environ.setdefault("ANALYSIS_SERVICE_URL", "http://localhost:8002")
os.environ.setdefault("COMMENT_SERVICE_URL", "http://localhost:8003")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.database import Base, engine


@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
