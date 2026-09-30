"""
Mutation có kiểm soát: mỗi thay đổi là một PendingAction đi qua state machine.

REQUESTED → PENDING_CONFIRMATION → CONFIRMED → EXECUTING → VERIFIED → COMPLETED
                         │                          │           └→ UNVERIFIED (đã gửi, chưa xác minh được)
                         ├→ CANCELLED               └→ FAILED
                         └→ EXPIRED
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActionState(str, Enum):
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    VERIFIED = "verified"
    COMPLETED = "completed"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATES = frozenset({
    ActionState.COMPLETED, ActionState.UNVERIFIED, ActionState.FAILED,
    ActionState.CANCELLED, ActionState.EXPIRED,
})

ALLOWED_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PENDING_CONFIRMATION: frozenset({ActionState.CONFIRMED, ActionState.CANCELLED, ActionState.EXPIRED}),
    ActionState.CONFIRMED: frozenset({ActionState.EXECUTING, ActionState.FAILED}),
    ActionState.EXECUTING: frozenset({ActionState.VERIFIED, ActionState.UNVERIFIED, ActionState.FAILED}),
    ActionState.VERIFIED: frozenset({ActionState.COMPLETED}),
}


class ConfirmationLevel(str, Enum):
    NONE = "none"
    STANDARD = "standard"  # xác nhận gắn với action_id + mã
    STRONG = "strong"      # như standard, TTL ngắn hơn, cảnh báo phạm vi ảnh hưởng


class FieldChange(BaseModel):
    field: str
    label: str
    before: Any = None
    after: Any = None


class PendingAction(BaseModel):
    id: str
    tool: str
    operation: str
    state: ActionState
    confirmation_level: ConfirmationLevel
    confirmation_code: str = Field(repr=False)
    owner_user_id: str
    owner_role: str
    session_id: str
    request_id: str
    target_type: str
    target_id: str
    target_label: str
    summary: str
    changes: list[FieldChange]
    warnings: list[str] = Field(default_factory=list)
    params: dict[str, Any]                 # tham số đã validate để execute
    snapshot: dict[str, Any] = Field(default_factory=dict)  # trạng thái 'before' để phát hiện dữ liệu cũ
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
