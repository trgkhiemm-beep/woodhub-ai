"""
Integration ports (Hexagonal architecture).

Agent core và tools CHỈ phụ thuộc vào các Protocol dưới đây. Adapter production:
app/adapters/backend/* → WoodHub Backend (source of truth, đọc/ghi Supabase phía Backend).
Thay Backend (hoặc thêm nguồn mới) chỉ cần viết adapter mới cho các Protocol này.

Quy ước: mọi method nhận `principal` để adapter gọi Backend bằng quyền của chính người dùng;
lỗi phải được chuẩn hóa thành app.domain.errors.PortError.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from app.domain.models import (
    Branch, DesignTask, InventoryInfo, KnowledgeDocument, KnowledgeHit, KnowledgeKind, NamedRef,
    PolicyType, Product, ProductPage, Promotion, PromotionStatus, SearchCriteria, StoreInfo,
)
from app.domain.principal import Principal

SourceSystem = Literal["backend"]


@runtime_checkable
class IdentityPort(Protocol):
    source_system: SourceSystem

    async def resolve(self, access_token: str) -> Principal: ...


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
    # --- mutations (quyền do nguồn dữ liệu enforce lần 2) ---
    async def update_product_description(self, product: Product, description: str, principal: Principal) -> Product: ...
    async def update_variant_price(self, product: Product, variant_id: str, price: float, principal: Principal) -> Product: ...
    async def upsert_category(self, category_id: str | None, name: str, parent_id: str | None, principal: Principal) -> NamedRef: ...
    async def upsert_material(self, material_id: str | None, name: str, principal: Principal) -> NamedRef: ...


@runtime_checkable
class InventoryPort(Protocol):
    source_system: SourceSystem

    async def get_inventory(self, variant_id: str, principal: Principal) -> InventoryInfo: ...
    async def adjust_inventory(self, store_id: str, variant_id: str, delta: int, principal: Principal) -> InventoryInfo: ...


@runtime_checkable
class StorePort(Protocol):
    source_system: SourceSystem

    async def get_store_info(self, principal: Principal) -> StoreInfo: ...
    async def list_branches(self, principal: Principal, city: str | None = None) -> list[Branch]: ...
    async def find_nearby_workshops(self, lat: float, lng: float, limit: int, principal: Principal) -> list[Branch]: ...
    async def update_store_info(self, changes: dict[str, Any], expected_version: int | None, principal: Principal) -> StoreInfo: ...


@runtime_checkable
class PromotionPort(Protocol):
    source_system: SourceSystem

    async def list_promotions(self, principal: Principal, status: PromotionStatus | None = None,
                              category_id: str | None = None) -> list[Promotion]: ...
    async def get_promotion(self, promotion_id: str, principal: Principal) -> Promotion: ...
    async def create_promotion(self, draft: dict[str, Any], principal: Principal, idempotency_key: str) -> Promotion: ...
    async def set_promotion_status(self, promotion_id: str, status: PromotionStatus, expected_version: int | None,
                                   principal: Principal) -> Promotion: ...


@runtime_checkable
class KnowledgePort(Protocol):
    source_system: SourceSystem

    async def get_policy(self, policy_type: PolicyType, principal: Principal) -> KnowledgeDocument: ...
    async def search(self, query: str, kinds: list[KnowledgeKind], top_k: int, principal: Principal) -> list[KnowledgeHit]: ...
    async def get_document(self, document_id: str, principal: Principal) -> KnowledgeDocument: ...
    async def upsert_faq(self, document_id: str | None, question: str, answer: str, expected_version: int | None,
                         principal: Principal) -> KnowledgeDocument: ...


@runtime_checkable
class DesignPort(Protocol):
    source_system: SourceSystem

    async def get_design_task(self, task_id: str, principal: Principal) -> DesignTask: ...


class Ports:
    """Tập hợp adapter được wiring khi khởi động (xem app/container.py)."""

    def __init__(self, *, identity: IdentityPort, catalog: CatalogPort, inventory: InventoryPort,
                 store: StorePort, promotions: PromotionPort, knowledge: KnowledgePort, design: DesignPort):
        self.identity = identity
        self.catalog = catalog
        self.inventory = inventory
        self.store = store
        self.promotions = promotions
        self.knowledge = knowledge
        self.design = design
