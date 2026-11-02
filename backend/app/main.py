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
    with SessionLocal() as db:
        seed_demo_data(db)
    yield

app = FastAPI(title="DealGuard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Demo-Actor"],
)

app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "dealguard-api"}
