"""request_id của lượt đang xử lý (contextvar) — để mọi call Agent → Backend mang X-Request-Id và mọi log lỗi
upstream ghi được request_id, giúp truy một request xuyên Backend ↔ Agent."""
from __future__ import annotations

from contextvars import ContextVar

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
