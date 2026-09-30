"""Dependency: request id, xác thực (JWT của Backend), rate limit."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.container import Container
from app.domain import errors
from app.domain.principal import Principal


def get_container(request: Request) -> Container:
    return request.app.state.container


def request_id(request: Request) -> str:
    rid = request.headers.get("X-Request-Id", "")
    return rid if 8 <= len(rid) <= 64 and rid.replace("-", "").isalnum() else str(uuid.uuid4())


async def current_principal(request: Request, container: Container = Depends(get_container)) -> Principal:
    """Không có token → guest. Token sai/hết hạn → 401 (không âm thầm hạ xuống guest)."""
    header = request.headers.get("Authorization", "")
    if not header:
        return Principal.guest()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Authorization phải là Bearer token."})
    try:
        return await container.ports.identity.resolve(token.strip())
    except errors.Unauthenticated as exc:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": exc.message}) from exc
    except errors.PortError as exc:
        raise HTTPException(status_code=503, detail={"code": "AUTH_UNAVAILABLE",
                                                     "message": "Không xác thực được do hệ thống tài khoản không phản hồi."}) from exc


class RateLimiter:
    """Sliding window theo user/IP (in-memory, một instance)."""

    def __init__(self, per_minute: int):
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self._limit:
            raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "Bạn gửi quá nhanh, vui lòng thử lại sau ít phút."})
        q.append(now)
        if len(self._hits) > 20000:
            self._hits.clear()


async def rate_limited_principal(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
    limiter: RateLimiter = request.app.state.rate_limiter
    key = principal.user_id or (request.client.host if request.client else "unknown")
    limiter.check(key)
    return principal
