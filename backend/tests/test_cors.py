from app.core.config import settings
from app.main import frontend_allowed_origins


def test_development_cors_allows_common_local_frontend_origins():
    origins = frontend_allowed_origins()

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "http://localhost:3001" in origins
    assert "http://127.0.0.1:3001" in origins
    assert settings.frontend_origin in origins


def test_production_cors_only_allows_configured_frontend_origin(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "frontend_origin", "https://dealguard.example")

    assert frontend_allowed_origins() == ["https://dealguard.example"]
