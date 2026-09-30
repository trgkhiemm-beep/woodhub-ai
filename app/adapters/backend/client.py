"""
HTTP client tới WoodHub Backend (Spring Boot).

- Chỉ gọi các endpoint trong ALLOWED_ENDPOINTS (đã kiểm chứng từ OpenAPI snapshot
  contracts/backend/woodhub-be.openapi.snapshot-2026-09-30.json). Không có đường gọi tùy ý.
- Forward JWT của người dùng → Backend enforce RBAC/ownership lần 2.
- Timeout, retry có backoff CHỈ cho request an toàn để lặp lại (GET, PUT). PATCH/POST không retry.
- Chuẩn hóa lỗi HTTP → app.domain.errors.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.domain import errors
from app.domain.principal import Principal

logger = logging.getLogger("woodhub.backend")

# (method, path pattern) — mọi call phải khớp 1 dòng ở đây.
ALLOWED_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", r"/api/users/me"),
    ("GET", r"/api/products"),
    ("GET", r"/api/products/mine"),
    ("GET", r"/api/products/[0-9a-fA-F-]{36}"),
    ("PUT", r"/api/products/[0-9a-fA-F-]{36}"),
    ("PUT", r"/api/variants/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/variants/[0-9a-fA-F-]{36}/inventory"),
    ("PATCH", r"/api/stores/[0-9a-fA-F-]{36}/inventory/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/categories"),
    ("POST", r"/api/categories"),
    ("PUT", r"/api/categories/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/materials"),
    ("POST", r"/api/materials"),
    ("PUT", r"/api/materials/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/rooms"),
    ("GET", r"/api/styles"),
    ("GET", r"/api/suppliers/public"),
    ("GET", r"/api/suppliers/[0-9a-fA-F-]{36}/stores"),
    ("GET", r"/api/stores/nearby/workshops"),
    ("GET", r"/api/custom/ai/tasks/[0-9a-fA-F-]{36}"),
)
_ALLOWED = tuple((m, re.compile(p + r"$")) for m, p in ALLOWED_ENDPOINTS)
_RETRY_SAFE = frozenset({"GET", "PUT"})


class BackendClient:
    def __init__(self, base_url: str, *, timeout: float = 8.0, max_retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None, backoff_base: float = 0.3):
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, transport=transport,
                                         headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check_allowed(method: str, path: str) -> None:
        if not any(m == method and rx.match(path) for m, rx in _ALLOWED):
            raise errors.Forbidden("Endpoint không nằm trong allowlist của AI service.", detail=f"{method} {path}")

    async def request(self, method: str, path: str, principal: Principal | None = None, *,
                      params: dict[str, Any] | None = None, json: Any = None, request_id: str | None = None,
                      access_token: str | None = None) -> Any:
        method = method.upper()
        self._check_allowed(method, path)
        headers: dict[str, str] = {}
        token = access_token or (principal.access_token if principal else None)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if request_id:
            headers["X-Request-Id"] = request_id
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        attempts = 1 + (self._max_retries if method in _RETRY_SAFE else 0)
        last_exc: errors.PortError | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.request(method, path, params=clean_params, json=json, headers=headers)
            except httpx.TimeoutException as exc:
                last_exc = errors.UpstreamTimeout("Backend phản hồi quá thời gian.", detail=f"{method} {path}: {exc!r}")
            except httpx.HTTPError as exc:
                last_exc = errors.UpstreamUnavailable("Không kết nối được Backend.", detail=f"{method} {path}: {exc!r}")
            else:
                if resp.status_code < 400:
                    return self._parse_json(resp, method, path)
                last_exc = self._map_status(resp, method, path)
                if not last_exc.retryable:
                    raise last_exc
            if attempt + 1 < attempts:
                await asyncio.sleep(self._backoff_base * (2 ** attempt))
        logger.warning("Backend call failed after %d attempt(s): %s %s (%s)", attempts, method, path,
                       last_exc.code if last_exc else "?")
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _parse_json(resp: httpx.Response, method: str, path: str) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise errors.MalformedResponse("Backend trả dữ liệu không hợp lệ.", detail=f"{method} {path}: {exc!r}") from exc

    @staticmethod
    def _map_status(resp: httpx.Response, method: str, path: str) -> errors.PortError:
        status = resp.status_code
        detail = f"{method} {path} -> {status}: {resp.text[:300]}"
        if status == 400 or status == 422:
            return errors.ValidationFailed("Backend từ chối dữ liệu không hợp lệ.", detail=detail)
        if status == 401:
            return errors.Unauthenticated("Phiên đăng nhập không hợp lệ hoặc đã hết hạn.", detail=detail)
        if status == 403:
            # Backend (Spring) trả 403 cho cả "chưa đăng nhập" và "không đủ quyền".
            return errors.Forbidden("Backend từ chối quyền truy cập.", detail=detail)
        if status == 404:
            return errors.NotFound("Không tìm thấy dữ liệu.", detail=detail)
        if status == 409:
            return errors.Conflict("Dữ liệu đã bị thay đổi hoặc xung đột.", detail=detail)
        if status == 429:
            return errors.RateLimited("Đã vượt giới hạn sử dụng.", detail=detail)
        return errors.UpstreamUnavailable("Backend đang gặp sự cố.", detail=detail)


def require_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.MalformedResponse(f"Dữ liệu {what} từ Backend không đúng định dạng.", detail=repr(value)[:200])
    return value


def require_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise errors.MalformedResponse(f"Dữ liệu {what} từ Backend không đúng định dạng.", detail=repr(value)[:200])
    return value
