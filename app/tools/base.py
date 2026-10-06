"""Định nghĩa tool: typed input, loại thao tác (CHỈ ĐỌC), quyền, mức rủi ro."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.domain.models import Freshness, SourceRef
from app.domain.principal import Principal
from app.domain.results import ToolResult
from app.ports import Ports


class OperationType(str, Enum):
    """Agent chỉ đọc dữ liệu: không có loại thao tác ghi (INSERT/UPDATE/DELETE thuộc Backend Admin API)."""
    READ = "READ"
    SEARCH = "SEARCH"
    REALTIME = "REALTIME"


class AgentProfile(str, Enum):
    CUSTOMER = "customer"      # khách/guest
    MANAGEMENT = "management"  # admin + supplier qua /v1/agent/manage/chat (giữ tương thích Backend) — cũng CHỈ ĐỌC


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


ReadHandler = Callable[[Any, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    operation: OperationType
    risk: Literal["low", "medium", "high"]
    source_of_truth: str
    read_handler: ReadHandler

    def __post_init__(self) -> None:
        if not isinstance(self.operation, OperationType) or self.read_handler is None:
            raise ValueError(f"{self.name}: tool phải là READ/SEARCH/REALTIME và có read_handler")

    def json_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()
