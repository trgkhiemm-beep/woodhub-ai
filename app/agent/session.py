"""
Session store in-memory có TTL và giới hạn kích thước (thay cho dict `session_memory` tăng vô hạn cũ).

Agent không biết danh tính người dùng (Backend giữ việc đó): session_id do server tạo ngẫu nhiên (không đoán được) và
Backend lưu theo từng người dùng/phiên chat của mình. session_id lạ/hết hạn → tạo phiên mới.
Lịch sử hội thoại đầy đủ do Backend lưu (/api/ai-chat/...); ở đây chỉ giữ ngữ cảnh tối thiểu.
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.domain.principal import Principal
from app.agent.dialogue import DialogueState


@dataclass
class Session:
    id: str
    conversation: DialogueState = field(default_factory=DialogueState)
    expires_at: float = 0.0


class SessionStore:
    def __init__(self, ttl_seconds: int, max_sessions: int):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._items: OrderedDict[str, Session] = OrderedDict()

    def get_or_create(self, session_id: str | None, principal: Principal | None = None) -> Session:
        now = time.monotonic()
        s = self._items.get(session_id) if session_id else None
        if s is not None and s.expires_at < now:
            s = None
        if s is None:
            sid = session_id or f"s_{secrets.token_urlsafe(12)}"
            s = Session(id=sid)
            self._items[sid] = s
        s.expires_at = now + self._ttl
        self._items.move_to_end(s.id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return s
