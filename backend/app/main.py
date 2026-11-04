from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.seed import seed_demo_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.db.migrations import upgrade_database

    upgrade_database()
    if settings.app_env != "production":
        with SessionLocal() as db:
            seed_demo_data(db)
    yield

app = FastAPI(title="DealGuard API", version="0.1.0", lifespan=lifespan)


def frontend_allowed_origins() -> list[str]:
    origins = {settings.frontend_origin}
    if settings.app_env != "production":
        origins.update({
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        })
    return sorted(origins)


app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Demo-Actor"],
)

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "dealguard-api"}
