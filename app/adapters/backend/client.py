"""
HTTP client tới WoodHub Backend (Spring Boot).

- Chỉ gọi các endpoint trong ALLOWED_ENDPOINTS (đã kiểm chứng từ OpenAPI snapshot
  contracts/backend/woodhub-be.openapi.snapshot-2026-10-06.json). Không có đường gọi tùy ý.
- Chuyển tiếp NGUYÊN TRẠNG token mà Backend gửi kèm request (không giải mã) → Backend tự enforce quyền/sở hữu.
- Chỉ GET (agent chỉ đọc). Timeout, retry có backoff.
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
from app.request_context import current_request_id

logger = logging.getLogger("woodhub.backend")

# (method, path pattern) — mọi call phải khớp 1 dòng ở đây. Agent CHỈ ĐỌC: không có PUT/PATCH/POST/DELETE.
ALLOWED_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GET", r"/api/products"),
    ("GET", r"/api/products/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/variants/[0-9a-fA-F-]{36}/inventory"),
    ("GET", r"/api/categories"),
    ("GET", r"/api/materials"),
    ("GET", r"/api/rooms"),
    ("GET", r"/api/styles"),
    ("GET", r"/api/suppliers/public"),
    ("GET", r"/api/suppliers/[0-9a-fA-F-]{36}/public"),
    ("GET", r"/api/suppliers/[0-9a-fA-F-]{36}/stores"),
    ("GET", r"/api/stores/nearby/workshops"),
    ("GET", r"/api/custom/ai/tasks/[0-9a-fA-F-]{36}"),
    ("GET", r"/api/custom-orders/my"),
    ("GET", r"/api/custom-orders/[0-9a-fA-F-]{36}"),
)
_ALLOWED = tuple((m, re.compile(p + r"$")) for m, p in ALLOWED_ENDPOINTS)
_RETRY_SAFE = frozenset({"GET"})


class BackendClient:
    def __init__(self, base_url: str, *, timeout: float = 8.0, max_retries: int = 2,
                 transport: httpx.AsyncBaseTransport | None = None, backoff_base: float = 0.3):
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self.stats = {"requests": 0}  # metadata cho observability/evaluation
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, transport=transport,
                                         headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _check_allowed(method: str, path: str) -> None:
        if not any(m == method and rx.match(path) for m, rx in _ALLOWED):
            raise errors.Forbidden("Endpoint không nằm trong allowlist của AI service.", detail=f"{method} {path}")

    async def request(self, method: str, path: str, principal: Principal | None = None, *,
                      params: dict[str, Any] | None = None, json: Any = None, request_id: str | None = None) -> Any:
        method = method.upper()
        self._check_allowed(method, path)
        headers: dict[str, str] = {}
        token = principal.access_token if principal else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        rid = request_id or current_request_id.get()
        if rid:
            headers["X-Request-Id"] = rid  # Backend log được request này theo request_id của Agent
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        attempts = 1 + (self._max_retries if method in _RETRY_SAFE else 0)
        last_exc: errors.PortError | None = None
        for attempt in range(attempts):
            try:
                self.stats["requests"] += 1
                resp = await self._client.request(method, path, params=clean_params, json=json, headers=headers)
            except httpx.TimeoutException as exc:
                last_exc = errors.UpstreamTimeout("Backend phản hồi quá thời gian.", detail=f"{method} {path}: {exc!r}")
            except httpx.HTTPError as exc:
                last_exc = errors.UpstreamUnavailable("Không kết nối được Backend.", detail=f"{method} {path}: {exc!r}")
            else:
                if resp.status_code < 400:
                    return self._parse_json(resp, method, path)
                last_exc = self._map_status(resp, method, path)
                if not last_exc.retryable or isinstance(last_exc, errors.RateLimited):
                    if isinstance(last_exc, errors.RateLimited):
                        logger.warning("upstream_429 request_id=%s %s %s attempt=%d layer=%s retry_after=%s cf_ray=%s rndr_id=%s body=%r",
                                       rid, method, path, attempt + 1, upstream_layer(resp), resp.headers.get("retry-after"),
                                       resp.headers.get("cf-ray"), resp.headers.get("rndr-id"), resp.text[:200])
                    raise last_exc
            if attempt + 1 < attempts:
                await asyncio.sleep(self._backoff_base * (2 ** attempt))
        logger.warning("Backend call failed after %d attempt(s): request_id=%s %s %s (%s)", attempts, rid, method, path,
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


def upstream_layer(resp: httpx.Response) -> str:
    """Tầng nào sinh ra response lỗi: Spring Boot trả JSON {timestamp,status,error,path}; Cloudflare trả trang lỗi
    (thường kèm 'error code: 10xx'); Render edge trả text/HTML không phải JSON của ứng dụng."""
    ctype = resp.headers.get("content-type", "")
    body = resp.text[:500]
    if "json" in ctype and '"timestamp"' in body and '"path"' in body:
        return "backend-app"
    if "cloudflare" in body.lower() or "error code: 10" in body.lower():
        return "cloudflare"
    if resp.headers.get("x-render-origin-server") or resp.headers.get("rndr-id"):
        return "render-edge-or-app"
    return "unknown"


def require_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise errors.MalformedResponse(f"Dữ liệu {what} từ Backend không đúng định dạng.", detail=repr(value)[:200])
    return value


def require_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise errors.MalformedResponse(f"Dữ liệu {what} từ Backend không đúng định dạng.", detail=repr(value)[:200])
    return value
