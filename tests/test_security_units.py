"""Registry invariants, permission matrix, grounding guard, audit redaction, config guard rails."""
import pytest

from app.audit import redact
from app.domain.principal import Principal, Role
from app.tools.base import AgentProfile, ConfirmationLevel
from app.tools.registry import FORBIDDEN_TOOL_NAMES, build_registry
from tests.conftest import make_settings

REG = build_registry()
P = {r: Principal(user_id=None if r == Role.GUEST else f"u-{r.value}", role=r) for r in Role}


def names(role, profile):
    return {s.name for s in REG.available(P[role], profile)}


def test_no_dangerous_tools_exist():
    assert not FORBIDDEN_TOOL_NAMES & {s.name for s in REG.all()}
    assert all(not s.name.startswith(("execute", "run_", "delete")) for s in REG.all())


def test_every_mutation_requires_confirmation_and_is_not_for_customers():
    for s in REG.all():
        if s.operation.is_mutation:
            assert s.confirmation in (ConfirmationLevel.STANDARD, ConfirmationLevel.STRONG), s.name
            assert not ({Role.GUEST, Role.CUSTOMER} & s.allowed_roles), s.name
            assert s.requires_auth, s.name


@pytest.mark.parametrize("role", list(Role))
def test_customer_profile_never_exposes_mutations(role):
    mutations = {s.name for s in REG.all() if s.operation.is_mutation}
    assert not names(role, AgentProfile.CUSTOMER) & mutations


def test_permission_matrix():
    m = AgentProfile.MANAGEMENT
    assert names(Role.GUEST, m) == set() and names(Role.CUSTOMER, m) == set()
    admin, supplier = names(Role.ADMIN, m), names(Role.SUPPLIER, m)
    # DEC-4: giá / tồn kho / mô tả sản phẩm thuộc quyền supplier sở hữu (RBAC của Backend); admin chỉ đọc
    assert {"update_product_price", "adjust_inventory", "update_product_description"} <= supplier
    assert not {"update_product_price", "adjust_inventory", "update_product_description"} & admin
    assert {"create_promotion", "set_promotion_status", "update_store_info", "upsert_faq", "upsert_category",
            "upsert_material"} <= admin
    assert not {"create_promotion", "update_store_info", "upsert_category"} & supplier
    # guest không dùng được tool cần đăng nhập
    assert "find_nearby_workshops" not in names(Role.GUEST, AgentProfile.CUSTOMER)


def test_admin_denial_reason_explains_ownership():
    spec = REG.get("update_product_price")
    d = REG.check(spec, P[Role.ADMIN], AgentProfile.MANAGEMENT)
    assert not d.allowed and "nhà cung cấp" in d.reason


def test_audit_redacts_secrets():
    rec = redact({"access_token": "abc", "nested": {"Authorization": "Bearer x", "password": "p"}, "price": 1})
    assert rec["access_token"] == "[REDACTED]" and rec["nested"]["Authorization"] == "[REDACTED]"
    assert rec["nested"]["password"] == "[REDACTED]" and rec["price"] == 1


def test_principal_repr_never_leaks_token():
    assert "secret-token" not in repr(Principal(user_id="u", role=Role.ADMIN, access_token="secret-token"))


def test_backend_role_mapping_is_least_privilege():
    assert Role.from_backend("admin") == Role.ADMIN
    assert Role.from_backend("weird") == Role.CUSTOMER
    assert Role.from_backend("guest") == Role.CUSTOMER


def test_config_guard_rails():
    with pytest.raises(ValueError):
        make_settings(APP_ENV="production", CORS_ORIGINS="*")
    with pytest.raises(ValueError):
        make_settings(BACKEND_BASE_URL="http://evil.example.com")
    with pytest.raises(ValueError):
        make_settings(NLU_MODE="llm", BEDROCK_MODEL_ID=None)
