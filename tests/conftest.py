"""
Test harness.

Nguyên tắc (theo yêu cầu dự án): KHÔNG dùng mock data.
- Test "live" đọc DỮ LIỆU THẬT qua WoodHub Backend (https://woodhub-be.onrender.com, backed by Supabase)
  và đối chiếu với Supabase qua REST (chỉ SELECT) để chứng minh agent không bịa số liệu.
- Test mutation đọc dữ liệu thật để lập kế hoạch; bước GHI bị chặn bởi WriteIntercept (không gửi PUT/PATCH/POST
  lên production). Không có tài khoản test nên không thể — và không được — ghi vào dữ liệu thật.
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
from app.domain import errors
from app.domain.models import NamedRef, Product
from app.domain.principal import Principal, Role
from app.ports import Ports

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


# ---------------------------------------------------------------- write interception
class WriteIntercept:
    """
    Bọc adapter Backend THẬT: mọi thao tác ĐỌC đi thẳng tới Backend (dữ liệu thật),
    mọi thao tác GHI bị chặn — ghi lại lời gọi và (nếu apply=True) phản ánh vào lớp overlay
    để bước verify có thể đọc lại. Không có request ghi nào tới production.
    """

    def __init__(self, real: Any, *, apply: bool = True):
        self._real = real
        self.source_system = real.source_system
        self.apply = apply
        self.writes: list[tuple[str, tuple, dict]] = []
        self.price_overlay: dict[str, float] = {}
        self.desc_overlay: dict[str, str] = {}
        self.named_overlay: dict[str, list[NamedRef]] = {}
        self.fail_with: errors.PortError | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def _patch(self, p: Product) -> Product:
        p = p.model_copy(deep=True)
        for v in p.variants:
            if v.id in self.price_overlay:
                v.price = self.price_overlay[v.id]
        if p.id in self.desc_overlay:
            p.description = self.desc_overlay[p.id]
        return p

    async def get_product(self, product_id: str, principal: Principal) -> Product:
        return self._patch(await self._real.get_product(product_id, principal))

    async def find_product_by_sku(self, sku: str, principal: Principal) -> Product:
        return self._patch(await self._real.find_product_by_sku(sku, principal))

    async def update_variant_price(self, product: Product, variant_id: str, price: float, principal: Principal) -> Product:
        self.writes.append(("update_variant_price", (product.id, variant_id, price), {}))
        if self.fail_with:
            raise self.fail_with
        if self.apply:
            self.price_overlay[variant_id] = price
        return await self.get_product(product.id, principal)

    async def update_product_description(self, product: Product, description: str, principal: Principal) -> Product:
        self.writes.append(("update_product_description", (product.id, description), {}))
        if self.fail_with:
            raise self.fail_with
        if self.apply:
            self.desc_overlay[product.id] = description
        return await self.get_product(product.id, principal)

    async def _named(self, kind: str, principal: Principal) -> list[NamedRef]:
        real = await (self._real.list_categories(principal) if kind == "categories" else self._real.list_materials(principal))
        return real + self.named_overlay.get(kind, [])

    async def list_categories(self, principal: Principal) -> list[NamedRef]:
        return await self._named("categories", principal)

    async def list_materials(self, principal: Principal) -> list[NamedRef]:
        return await self._named("materials", principal)

    async def upsert_category(self, category_id, name, parent_id, principal) -> NamedRef:
        self.writes.append(("upsert_category", (category_id, name, parent_id), {}))
        ref = NamedRef(id=category_id or "intercepted-category", name=name, parent_id=parent_id)
        if self.apply:
            self.named_overlay.setdefault("categories", []).append(ref)
        return ref


@pytest.fixture
def audit_sink() -> MemoryAuditSink:
    return MemoryAuditSink()


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Lưới an toàn: chặn MỌI request không phải GET tới Backend thật trong test."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.blocked: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            self.blocked.append(f"{request.method} {request.url.path}")
            raise AssertionError(f"Test định ghi vào Backend production: {request.method} {request.url.path}")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def build_live(audit_sink: MemoryAuditSink, *, apply_writes: bool = True, **settings_overrides: Any
               ) -> tuple[Container, WriteIntercept]:
    """Container dùng adapter Backend THẬT (transport chỉ-đọc); catalog được bọc WriteIntercept."""
    settings = make_settings(**settings_overrides)
    base = build_container(settings, audit_sinks=[audit_sink], transport=ReadOnlyTransport())
    intercept = WriteIntercept(base.ports.catalog, apply=apply_writes)
    p = base.ports
    ports = Ports(identity=p.identity, catalog=intercept, inventory=p.inventory, store=p.store,
                  promotions=p.promotions, knowledge=p.knowledge, design=p.design)
    container = build_container(settings, audit_sinks=[audit_sink], ports=ports)
    container.backend = base.backend  # dùng chung client thật
    return container, intercept


def principal(role: Role, user_id: str = "test-user-1") -> Principal:
    """Principal dựng sẵn cho test (không có token → Backend sẽ từ chối mọi ghi nếu lỡ gửi đi)."""
    return Principal(user_id=None if role == Role.GUEST else user_id, role=role)
