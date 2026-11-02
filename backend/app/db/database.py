from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        raw_path = url.removeprefix("sqlite:///")
        path = Path(raw_path)
        if not path.is_absolute():
            project_root = Path(__file__).resolve().parents[3]
            path = project_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path.as_posix()}"
    return url


engine = create_engine(
    _database_url(),
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
