"""
Integration ports (Hexagonal architecture) — CHỈ ĐỌC.

Agent core và tools chỉ phụ thuộc vào các Protocol dưới đây. Adapter production:
app/adapters/backend/* → WoodHub Backend (source of truth, Backend đọc Supabase).
Không có method ghi dữ liệu: Admin/Supplier cập nhật qua Backend Admin API, không qua agent.

Quy ước: mọi method nhận `principal` (ngữ cảnh người gọi) để adapter chuyển tiếp nguyên trạng token Backend gửi kèm;
Backend tự quyết định quyền truy cập;
lỗi phải được chuẩn hóa thành app.domain.errors.PortError.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from app.domain.models import (
    Branch, CustomOrder, DesignTask, InventoryInfo, KnowledgeHit, KnowledgeKind, NamedRef, Product, ProductPage,
    SearchCriteria, SupplierInfo,
)
from app.domain.principal import Principal

SourceSystem = Literal["backend"]


@runtime_checkable
class CatalogPort(Protocol):
    source_system: SourceSystem

    async def search_products(self, criteria: SearchCriteria, principal: Principal) -> ProductPage: ...
    async def get_product(self, product_id: str, principal: Principal) -> Product: ...
    async def find_product_by_sku(self, sku: str, principal: Principal) -> Product: ...
    async def list_categories(self, principal: Principal) -> list[NamedRef]: ...
    async def list_materials(self, principal: Principal) -> list[NamedRef]: ...
    async def list_rooms(self, principal: Principal) -> list[NamedRef]: ...
    async def list_styles(self, principal: Principal) -> list[NamedRef]: ...


@runtime_checkable
class InventoryPort(Protocol):
    source_system: SourceSystem

    async def get_inventory(self, variant_id: str, principal: Principal) -> InventoryInfo: ...


@runtime_checkable
class StorePort(Protocol):
    """Nhà cung cấp và cửa hàng/chi nhánh của họ."""
    source_system: SourceSystem

    async def list_suppliers(self, principal: Principal) -> list[SupplierInfo]: ...
    async def get_supplier(self, supplier_id: str, principal: Principal) -> SupplierInfo: ...
    async def list_branches(self, principal: Principal, city: str | None = None) -> list[Branch]: ...
    async def find_nearby_workshops(self, lat: float, lng: float, limit: int, principal: Principal) -> list[Branch]: ...


@runtime_checkable
class OrderPort(Protocol):
    """Đơn của CHÍNH người dùng đăng nhập (Backend kiểm tra quyền sở hữu)."""
    source_system: SourceSystem

    async def list_my_orders(self, principal: Principal, limit: int = 5) -> list[CustomOrder]: ...
    async def get_order(self, order_id: str, principal: Principal) -> CustomOrder: ...


@runtime_checkable
class KnowledgePort(Protocol):
    """FAQ / hướng dẫn sử dụng Web/App (semantic knowledge, phải có nguồn thật)."""
    source_system: SourceSystem

    async def search(self, query: str, kinds: list[KnowledgeKind], top_k: int, principal: Principal) -> list[KnowledgeHit]: ...


@runtime_checkable
class DesignPort(Protocol):
    source_system: SourceSystem

    async def get_design_task(self, task_id: str, principal: Principal) -> DesignTask: ...


class Ports:
    """Tập hợp adapter được wiring khi khởi động (xem app/container.py)."""

    def __init__(self, *, catalog: CatalogPort, inventory: InventoryPort, store: StorePort,
                 knowledge: KnowledgePort, design: DesignPort, orders: OrderPort):
        self.catalog = catalog
        self.inventory = inventory
        self.store = store
        self.knowledge = knowledge
        self.design = design
        self.orders = orders
