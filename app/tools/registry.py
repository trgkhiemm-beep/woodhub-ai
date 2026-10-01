"""Tool registry + permission policy (role × profile × tool)."""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.principal import Principal, Role
from app.tools.base import AgentProfile, OperationType, ToolSpec
from app.tools.advisor import ADVISOR_TOOLS
from app.tools.mutation_tools import MUTATION_TOOLS
from app.tools.read_tools import READ_TOOLS

FORBIDDEN_TOOL_NAMES = frozenset({"execute_sql", "update_anything", "run_arbitrary_command", "run_command",
                                  "http_request", "confirm_action", "delete_anything"})

# Role nào được dùng profile nào. Supplier dùng MANAGEMENT nhưng chỉ có tool của chính họ (RBAC Backend).
PROFILE_ROLES: dict[AgentProfile, frozenset[Role]] = {
    AgentProfile.CUSTOMER: frozenset(Role),
    AgentProfile.MANAGEMENT: frozenset({Role.ADMIN, Role.SUPPLIER}),
}


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str | None = None


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]):
        names = [s.name for s in specs]
        if len(names) != len(set(names)):
            raise ValueError("Tên tool bị trùng.")
        bad = FORBIDDEN_TOOL_NAMES & set(names)
        if bad:
            raise ValueError(f"Tool bị cấm: {bad}")
        self._specs = {s.name: s for s in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def check(self, spec: ToolSpec, principal: Principal, profile: AgentProfile) -> PermissionDecision:
        if principal.role not in PROFILE_ROLES[profile]:
            return PermissionDecision(False, "Tài khoản của bạn không có quyền dùng trợ lý quản trị.")
        if profile == AgentProfile.CUSTOMER and spec.operation.is_mutation:
            return PermissionDecision(False, "Trợ lý khách hàng không thực hiện thay đổi dữ liệu.")
        if spec.requires_auth and not principal.is_authenticated:
            return PermissionDecision(False, "Bạn cần đăng nhập để dùng chức năng này.")
        if principal.role not in spec.allowed_roles:
            return PermissionDecision(False, _deny_reason(spec, principal))
        return PermissionDecision(True)

    def available(self, principal: Principal, profile: AgentProfile) -> list[ToolSpec]:
        """Chỉ những tool được phép mới được đưa cho planner/LLM."""
        return [s for s in self._specs.values() if self.check(s, principal, profile).allowed]


def _deny_reason(spec: ToolSpec, principal: Principal) -> str:
    if spec.operation.is_mutation and spec.allowed_roles == frozenset({Role.SUPPLIER}):
        if principal.role == Role.ADMIN:
            return ("Giá, tồn kho và mô tả sản phẩm thuộc quyền của nhà cung cấp sở hữu sản phẩm; "
                    "quản trị viên chỉ được xem. Vui lòng liên hệ nhà cung cấp.")
        return "Chỉ nhà cung cấp sở hữu sản phẩm mới được thay đổi thông tin này."
    if spec.operation.is_mutation:
        return "Chỉ quản trị viên mới được thực hiện thay đổi này."
    return "Bạn không có quyền dùng chức năng này."


def build_registry() -> ToolRegistry:
    registry = ToolRegistry(READ_TOOLS + ADVISOR_TOOLS + MUTATION_TOOLS)
    for spec in registry.all():
        # Bất biến an toàn — fail-fast khi khởi động nếu ai đó khai báo sai.
        if spec.operation.is_mutation:
            assert spec.mutation is not None and spec.confirmation.value != "none", spec.name
            assert not ({Role.GUEST, Role.CUSTOMER} & spec.allowed_roles), spec.name
        else:
            assert spec.operation in (OperationType.READ, OperationType.SEARCH, OperationType.REALTIME), spec.name
    return registry
