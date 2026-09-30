"""Kết quả chuẩn của mọi tool. Composer chỉ được nói những gì có trong ToolResult."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import SourceRef


class ToolStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"                    # không có nguồn đã xác minh → không được suy đoán
    DENIED = "denied"                      # không đủ quyền
    INVALID = "invalid"                    # input không hợp lệ
    NEEDS_INPUT = "needs_input"            # thiếu thông tin, cần hỏi lại
    CONFIRMATION_REQUIRED = "confirmation_required"
    ERROR = "error"                        # lỗi hạ tầng (timeout, backend down, dữ liệu hỏng)


class ToolResult(BaseModel):
    tool: str
    status: ToolStatus
    data: Any = None
    sources: list[SourceRef] = Field(default_factory=list)
    message: str | None = None             # thông điệp an toàn cho người dùng (tiếng Việt)
    error_code: str | None = None
    action_id: str | None = None           # khi CONFIRMATION_REQUIRED

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.OK
