"""
Dependency: request id, ngữ cảnh người gọi, bảo vệ server-to-server (tùy chọn), rate limit.

Agent KHÔNG xác thực/phân quyền người dùng (Backend làm việc đó). `Authorization: Bearer …` do Backend gửi kèm chỉ được
mang theo nguyên trạng để gọi lại Backend — không giải mã, không kiểm tra, không bao giờ là lý do để Agent từ chối request.
"""
from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.container import Container
from app.domain.principal import Principal

logger = logging.getLogger("woodhub.api")  # không log token/secret/header
SERVICE_KEY_HEADER = "X-Agent-Api-Key"


def get_container(request: Request) -> Container:
    return request.app.state.container


def request_id(request: Request) -> str:
    rid = request.headers.get("X-Request-Id", "")
    return rid if 8 <= len(rid) <= 64 and rid.replace("-", "").isalnum() else str(uuid.uuid4())


def verify_service_key(request: Request, container: Container = Depends(get_container)) -> None:
    """Bảo vệ SERVER-TO-SERVER (Backend → Agent), không phải xác thực người dùng.
    Chỉ bật khi cấu hình AGENT_SERVICE_API_KEY; khi bật, request phải mang header X-Agent-Api-Key đúng giá trị."""
    expected = container.settings.AGENT_SERVICE_API_KEY
    if expected is None or not expected.get_secret_value():
        return
    given = request.headers.get(SERVICE_KEY_HEADER, "")
    if not hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
        logger.info("service_key=rejected path=%s", request.url.path)
        raise HTTPException(status_code=401, detail={"code": "SERVICE_UNAUTHORIZED",
                                                     "message": "Request không đến từ dịch vụ được phép."})


def caller(request: Request, _: None = Depends(verify_service_key)) -> Principal:
    """Ngữ cảnh người gọi: chỉ mang theo token Backend gửi kèm (nếu có), KHÔNG kiểm tra."""
    scheme, _sep, token = request.headers.get("Authorization", "").partition(" ")
    return Principal(access_token=token.strip() or None) if scheme.lower() == "bearer" else Principal.guest()


class RateLimiter:
    """Sliding window theo client (in-memory, một instance) — chống lạm dụng dịch vụ, KHÔNG phải quota người dùng
    (quota ai_chat do Backend quản lý). Khi Agent nằm sau Backend, mọi request có thể cùng một IP → đặt ngưỡng phù hợp."""

    def __init__(self, per_minute: int):
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self._limit:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "Hệ thống đang quá tải, vui lòng thử lại sau ít phút."})
        q.append(now)
        if len(self._hits) > 20000:
            self._hits.clear()


async def rate_limited_caller(request: Request, principal: Principal = Depends(caller)) -> Principal:
    limiter: RateLimiter = request.app.state.rate_limiter
    limiter.check(request.client.host if request.client else "unknown")
    return principal
