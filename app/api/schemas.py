"""
PUBLIC CONTRACT giữa Frontend/Backend-proxy và AI Agent (version: agent-api v1).

Frontend chỉ phụ thuộc vào các DTO ở file này (xuất ra contracts/agent-api.openapi.json).
Không bao giờ trả class nội bộ (PendingAction, ToolResult...) trực tiếp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CONTRACT_VERSION = "1.0"

ResponseType = Literal["answer", "clarification", "confirmation_required", "action_result", "error"]
BlockKind = Literal[
    "product_list", "product_detail", "product_comparison", "inventory", "store_info", "branch_list",
    "workshop_list", "promotion_list", "policy", "knowledge", "taxonomy", "design_task", "candidates",
]


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000, description="Tin nhắn người dùng (giới hạn thực tế theo MAX_MESSAGE_CHARS).")
    session_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
                                   description="Bỏ trống để server tạo phiên mới.")
    client_message_id: str | None = Field(default=None, max_length=100, description="Id phía client để chống gửi trùng.")
    location: Location | None = Field(default=None, description="Vị trí thiết bị (chỉ dùng cho tìm xưởng gần).")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Tin nhắn không được để trống.")
        return v


class ConfirmRequest(BaseModel):
    confirmation_code: str = Field(min_length=4, max_length=12)
    session_id: str | None = Field(default=None, max_length=100)


class SourceOut(BaseModel):
    system: Literal["backend"]
    resource: str
    freshness: Literal["realtime", "reference", "semantic"]
    fetched_at: datetime
    record_id: str | None = None
    version: str | None = None


class Block(BaseModel):
    kind: BlockKind
    data: Any


class FieldChangeOut(BaseModel):
    field: str
    label: str
    before: Any = None
    after: Any = None


class ConfirmationOut(BaseModel):
    required: bool
    level: Literal["standard", "strong"]
    code: str | None = Field(default=None, description="Mã người dùng phải gửi lại để xác nhận (chỉ trả cho chủ action).")
    expires_at: datetime
    confirm_endpoint: str
    cancel_endpoint: str
    chat_phrase: str = Field(description="Câu người dùng có thể gõ trong chat để xác nhận, vd 'xác nhận ABC123'.")


class ActionOut(BaseModel):
    id: str
    tool: str
    operation: Literal["UPDATE", "SENSITIVE_UPDATE", "ACTION"]
    state: Literal["pending_confirmation", "confirmed", "executing", "verified", "completed", "unverified",
                   "failed", "cancelled", "expired"]
    summary: str
    target: dict[str, str]
    changes: list[FieldChangeOut]
    warnings: list[str] = Field(default_factory=list)
    confirmation: ConfirmationOut | None = None
    verified: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ErrorOut(BaseModel):
    code: str
    message: str


class MetaOut(BaseModel):
    contract_version: str = CONTRACT_VERSION
    profile: Literal["customer", "management"]
    role: Literal["guest", "customer", "supplier", "admin"]
    planner: Literal["rules", "llm"]
    tools_used: list[str] = Field(default_factory=list)
    data_source: Literal["backend"] = "backend"


class AgentResponse(BaseModel):
    type: ResponseType
    message: str
    session_id: str
    request_id: str
    blocks: list[Block] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    action: ActionOut | None = None
    error: ErrorOut | None = None
    meta: MetaOut


class ErrorResponse(BaseModel):
    """Envelope lỗi HTTP (4xx/5xx) — khác với AgentResponse(type='error') là lỗi trong hội thoại."""
    status: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None
