from fastapi import HTTPException

from app.db.models import DemoActor

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "sales": frozenset({"inquiry.run", "quote.read_own", "quote.update_own", "runs.read_own", "policies.read", "policy.diff"}),
    "manager": frozenset({"approvals.read", "approvals.decide", "metrics.read", "evaluation.read", "runs.read_tenant", "policies.read", "policy.diff"}),
    "admin": frozenset({"quote.read_tenant", "runs.read_tenant", "runs.read_all", "policies.read", "policy.diff", "policies.activate", "policy_drafts.manage", "demo.reset", "audit.read", "metrics.read", "evaluation.read"}),
}


def has_capability(actor: DemoActor, capability: str) -> bool:
    return capability in ROLE_CAPABILITIES.get(actor.role, frozenset())


def require_capability(actor: DemoActor, capability: str, *, code: str, message: str) -> None:
    if not has_capability(actor, capability):
        raise HTTPException(status_code=403, detail={"code": code, "message": message})
