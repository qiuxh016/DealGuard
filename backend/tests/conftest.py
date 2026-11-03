import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.db import models  # noqa: F401
from app.db.seed import seed_demo_data
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")
    engine = create_engine(URL.create("sqlite", database=str(tmp_path / "test.sqlite")), connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with TestingSession() as db:
        seed_demo_data(db)

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, headers={"X-Demo-Actor": "sales_a"}) as test_client:
        test_client.app.state.session_factory = TestingSession
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
