"""
Session store in-memory có TTL và giới hạn kích thước (thay cho dict `session_memory` tăng vô hạn cũ).

Mỗi session gắn với chủ sở hữu (user_id hoặc guest). Session_id của người khác không bị dùng lại:
server tạo session mới thay vì trả ngữ cảnh của người khác.
Lịch sử hội thoại đầy đủ do Backend lưu (/api/ai-chat/...); ở đây chỉ giữ ngữ cảnh tối thiểu.
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.domain.principal import Principal
from app.tools.base import ConversationContext


@dataclass
class Session:
    id: str
    owner: str
    conversation: ConversationContext = field(default_factory=ConversationContext)
    expires_at: float = 0.0


class SessionStore:
    def __init__(self, ttl_seconds: int, max_sessions: int):
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._items: OrderedDict[str, Session] = OrderedDict()

    @staticmethod
    def _owner(principal: Principal) -> str:
        return principal.user_id if principal.is_authenticated and principal.user_id else "guest"

    def get_or_create(self, session_id: str | None, principal: Principal) -> Session:
        now = time.monotonic()
        owner = self._owner(principal)
        s = self._items.get(session_id) if session_id else None
        if s is not None and (s.expires_at < now or s.owner != owner):
            s = None
            if session_id and self._items.get(session_id) and self._items[session_id].owner != owner:
                session_id = None  # không chiếm dụng session của người khác
        if s is None:
            sid = session_id or f"s_{secrets.token_urlsafe(12)}"
            s = Session(id=sid, owner=owner)
            self._items[sid] = s
        s.expires_at = now + self._ttl
        self._items.move_to_end(s.id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return s
