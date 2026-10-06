"""Registry chỉ đọc, permission matrix, allowlist chỉ GET, audit redaction, config guard rails."""
import pytest

from app.audit import redact
from app.domain.principal import Principal
from app.adapters.backend.client import ALLOWED_ENDPOINTS
from app.tools.base import AgentProfile, OperationType
from app.tools.registry import FORBIDDEN_TOOL_NAMES, READ_ONLY_OPERATIONS, ToolRegistry, build_registry
from tests.conftest import make_settings

REG = build_registry()


def test_no_dangerous_tools_exist():
    assert not FORBIDDEN_TOOL_NAMES & {s.name for s in REG.all()}
    assert all(not s.name.startswith(("execute", "run_", "delete")) for s in REG.all())


def test_agent_is_read_only():
    assert all(s.operation in READ_ONLY_OPERATIONS for s in REG.all())
    assert {op.value for op in OperationType} == {"READ", "SEARCH", "REALTIME"}  # không còn loại thao tác ghi
    assert {m for m, _ in ALLOWED_ENDPOINTS} == {"GET"}                        # Backend chỉ được gọi bằng GET


def test_mutation_tools_cannot_be_registered():
    import dataclasses
    spec = dataclasses.replace(REG.get("get_product"), name="update_product_price")
    with pytest.raises(ValueError):
        ToolRegistry([spec])


def test_agent_has_no_user_authorization():
    # Agent không phân quyền người dùng: mọi tool chỉ đọc, dùng chung; không còn khái niệm role/requires_auth
    from app.tools.base import ToolSpec
    fields = set(ToolSpec.__dataclass_fields__)
    assert not {"allowed_roles", "requires_auth"} & fields
    assert {"get_product", "recommend_products", "get_supplier_info", "find_nearby_workshops", "get_order_status"} <= \
        {s.name for s in REG.all()}


def test_audit_redacts_secrets():
    rec = redact({"access_token": "abc", "nested": {"Authorization": "Bearer x", "password": "p"}, "price": 1})
    assert rec["access_token"] == "[REDACTED]" and rec["nested"]["Authorization"] == "[REDACTED]"
    assert rec["nested"]["password"] == "[REDACTED]" and rec["price"] == 1


def test_principal_repr_never_leaks_token():
    assert "secret-token" not in repr(Principal(access_token="secret-token"))


def test_config_guard_rails():
    with pytest.raises(ValueError):
        make_settings(APP_ENV="production", CORS_ORIGINS="*")
    with pytest.raises(ValueError):
        make_settings(BACKEND_BASE_URL="http://evil.example.com")
    with pytest.raises(ValueError):
        make_settings(NLU_MODE="llm", BEDROCK_MODEL_ID=None)
