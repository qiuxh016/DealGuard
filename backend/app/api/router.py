from fastapi import APIRouter

from app.api.routes.demo import router as demo_router
from app.api.routes.approvals import router as approvals_router
from app.api.routes.policy_tools import router as policy_tools_router
from app.api.routes.policies import router as policies_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.runs import router as runs_router
from app.api.routes.auth import router as auth_router

api_router = APIRouter()
api_router.include_router(runs_router, prefix="/api/v1")
api_router.include_router(auth_router, prefix="/api/v1")
api_router.include_router(policies_router, prefix="/api/v1")
api_router.include_router(demo_router, prefix="/api/v1")
api_router.include_router(quotes_router, prefix="/api/v1")
api_router.include_router(approvals_router, prefix="/api/v1")
api_router.include_router(policy_tools_router, prefix="/api/v1")
