from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.permissions import ROLE_CAPABILITIES, has_capability, require_capability


@pytest.mark.parametrize(("role", "capability", "allowed"), [
    ("sales", "inquiry.run", True),
    ("sales", "business_data.read", True),
    ("sales", "approvals.decide", False),
    ("manager", "approvals.decide", True),
    ("manager", "audit.read", False),
    ("admin", "audit.read", True),
    ("admin", "runs.read_all", True),
    ("manager", "runs.read_all", False),
    ("admin", "inquiry.run", True),
    ("unknown", "demo.reset", False),
])
def test_role_capability_matrix(role, capability, allowed):
    actor = SimpleNamespace(role=role)
    assert has_capability(actor, capability) is allowed
    if allowed:
        require_capability(actor, capability, code="DENIED", message="denied")
    else:
        with pytest.raises(HTTPException) as error:
            require_capability(actor, capability, code="DENIED", message="denied")
        assert error.value.status_code == 403
        assert error.value.detail["code"] == "DENIED"


def test_role_matrix_has_only_supported_workspace_roles():
    assert set(ROLE_CAPABILITIES) == {"sales", "manager", "admin"}
