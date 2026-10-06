"""
Test harness.

Nguyên tắc (theo yêu cầu dự án): KHÔNG dùng mock data.
- Test "live" đọc DỮ LIỆU THẬT qua WoodHub Backend (https://woodhub-be.onrender.com, backed by Supabase)
  và đối chiếu với Supabase qua REST (chỉ SELECT) để chứng minh agent không bịa số liệu.
- Agent chỉ đọc: ReadOnlyTransport chặn và ghi lại mọi request không phải GET (phải luôn rỗng).
- Lỗi hạ tầng (timeout, 5xx, JSON hỏng) được mô phỏng ở tầng transport HTTP, không phải dữ liệu.

Biến môi trường: LIVE_BACKEND_URL (mặc định https://woodhub-be.onrender.com), SUPABASE_URL, SUPABASE_KEY (đọc từ .env).
Test live tự SKIP nếu không kết nối được.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest
from dotenv import dotenv_values

from app.audit import MemoryAuditSink
from app.config import Settings
from app.container import Container, build_container
from app.domain.principal import Principal

_ENV = {**dotenv_values(".env"), **os.environ}
LIVE_BACKEND_URL = _ENV.get("LIVE_BACKEND_URL") or "https://woodhub-be.onrender.com"
SUPABASE_URL = (_ENV.get("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (_ENV.get("SUPABASE_KEY") or "").strip()


def make_settings(**overrides: Any) -> Settings:
    base = dict(BACKEND_BASE_URL=LIVE_BACKEND_URL, APP_ENV="test", BACKEND_TIMEOUT_SECONDS=45, BACKEND_MAX_RETRIES=1,
                AUDIT_LOG_PATH="var/test-audit.jsonl", RATE_LIMIT_PER_MINUTE=1000, _env_file=None)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture(scope="session")
def live_backend_available() -> bool:
    try:
        r = httpx.get(f"{LIVE_BACKEND_URL}/api/categories", timeout=120)  # Render có thể cold-start ~1 phút
        return r.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
def live(live_backend_available):
    if not live_backend_available:
        pytest.skip("Backend thật không truy cập được")


class SupabaseTruth:
    """Ground truth đọc thẳng Supabase (REST, SELECT-only) để đối chiếu câu trả lời của agent."""

    def __init__(self, url: str, key: str):
        self._client = httpx.Client(base_url=f"{url}/rest/v1", timeout=30,
                                    headers={"apikey": key, "Authorization": f"Bearer {key}"})

    def get(self, table: str, **params: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/{table}", params=params)
        r.raise_for_status()
        return r.json()

    def product(self, product_id: str) -> dict[str, Any]:
        rows = self.get("products", id=f"eq.{product_id}",
                        select="id,name,status,description,product_variants(id,sku,price,color,dimensions)")
        assert rows, product_id
        return rows[0]

    def active_product_named(self, fragment: str) -> dict[str, Any]:
        rows = self.get("products", name=f"ilike.*{fragment}*", status="eq.active",
                        select="id,name,status,description,product_variants(id,sku,price,color,dimensions)")
        assert len(rows) == 1, f"cần đúng 1 sản phẩm chứa '{fragment}', có {len(rows)}"
        return rows[0]


@pytest.fixture(scope="session")
def truth() -> SupabaseTruth:
    if not (SUPABASE_URL and SUPABASE_KEY):
        pytest.skip("Thiếu SUPABASE_URL/SUPABASE_KEY để đối chiếu dữ liệu thật")
    return SupabaseTruth(SUPABASE_URL, SUPABASE_KEY)


@pytest.fixture
def audit_sink() -> MemoryAuditSink:
    return MemoryAuditSink()


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Lưới an toàn: agent chỉ đọc — mọi request không phải GET tới Backend thật đều bị chặn và ghi lại."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.blocked: list[str] = []

    @property
    def writes(self) -> list[str]:
        return self.blocked

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            self.blocked.append(f"{request.method} {request.url.path}")
            raise AssertionError(f"Agent định ghi vào Backend production: {request.method} {request.url.path}")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_live(audit_sink: MemoryAuditSink, *, llm_client: Any = None,
               **settings_overrides: Any) -> tuple[Container, ReadOnlyTransport]:
    """Container dùng adapter Backend THẬT qua transport chỉ-đọc. Mặc định NLU chạy rules (không gọi LLM);
    truyền llm_client để kiểm thử nhánh LLM. Trả (container, transport) — transport.writes phải luôn rỗng."""
    settings = make_settings(**settings_overrides)
    transport = ReadOnlyTransport()
    container = build_container(settings, audit_sinks=[audit_sink], transport=transport, llm_client=llm_client)
    return container, transport


def principal(token: str | None = None) -> Principal:
    """Ngữ cảnh người gọi cho test: Agent không xác thực/phân quyền — chỉ mang theo token (nếu có) để chuyển tiếp."""
    return Principal(access_token=token)
