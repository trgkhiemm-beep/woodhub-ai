"""Định nghĩa tool: typed input, loại thao tác, quyền, mức rủi ro, mức xác nhận."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.domain.actions import ConfirmationLevel, FieldChange, PendingAction
from app.domain.models import Freshness, SourceRef
from app.domain.principal import Principal, Role
from app.domain.results import ToolResult
from app.ports import Ports


class OperationType(str, Enum):
    READ = "READ"
    SEARCH = "SEARCH"
    REALTIME = "REALTIME"
    UPDATE = "UPDATE"
    SENSITIVE_UPDATE = "SENSITIVE_UPDATE"
    ACTION = "ACTION"

    @property
    def is_mutation(self) -> bool:
        return self in (OperationType.UPDATE, OperationType.SENSITIVE_UPDATE, OperationType.ACTION)


class AgentProfile(str, Enum):
    CUSTOMER = "customer"      # khách/guest: chỉ READ/SEARCH/REALTIME
    MANAGEMENT = "management"  # admin + supplier: thêm mutation theo quyền


class ToolInput(BaseModel):
    """Base cho mọi input tool: cấm field lạ để LLM/người dùng không chèn tham số ngoài schema."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class ConversationContext:
    """Ngữ cảnh hội thoại tối thiểu (không chứa dữ liệu realtime cũ để trả lời lại)."""
    last_product_id: str | None = None
    last_product_sku: str | None = None


@dataclass
class ToolContext:
    principal: Principal
    ports: Ports
    settings: Settings
    request_id: str
    session_id: str
    profile: AgentProfile
    conversation: ConversationContext = field(default_factory=ConversationContext)
    location: tuple[float, float] | None = None  # do client gửi trong request, không do LLM tạo

    def source(self, resource: str, freshness: Freshness, *, system: str, record_id: str | None = None,
               version: Any = None) -> SourceRef:
        return SourceRef(system=system, resource=resource, freshness=freshness, fetched_at=datetime.now(timezone.utc),
                         record_id=record_id, version=None if version is None else str(version))


@dataclass
class Proposal:
    """Kế hoạch mutation đã chuẩn bị (đọc trạng thái hiện tại), chờ xác nhận."""
    target_type: str
    target_id: str
    target_label: str
    summary: str
    changes: list[FieldChange]
    params: dict[str, Any]
    snapshot: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    escalate_to_strong: bool = False


class MutationHandler(Protocol):
    async def prepare(self, args: Any, ctx: ToolContext) -> Proposal | ToolResult: ...
    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None: ...
    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]: ...
    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool: ...


ReadHandler = Callable[[Any, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    operation: OperationType
    allowed_roles: frozenset[Role]
    risk: Literal["low", "medium", "high"]
    source_of_truth: str
    read_handler: ReadHandler | None = None
    mutation: MutationHandler | None = None
    confirmation: ConfirmationLevel = ConfirmationLevel.NONE
    requires_auth: bool = False

    def __post_init__(self) -> None:
        if self.operation.is_mutation:
            if self.mutation is None or self.read_handler is not None:
                raise ValueError(f"{self.name}: mutation tool phải có MutationHandler và không có read_handler")
            if self.confirmation == ConfirmationLevel.NONE:
                raise ValueError(f"{self.name}: mutation tool bắt buộc có confirmation")
            if Role.GUEST in self.allowed_roles or Role.CUSTOMER in self.allowed_roles:
                raise ValueError(f"{self.name}: guest/customer không được có mutation quản trị")
        elif self.read_handler is None:
            raise ValueError(f"{self.name}: read tool thiếu read_handler")

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()
