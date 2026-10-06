"""
Model dữ liệu nội bộ của Agent (độc lập với DTO của Backend).

Adapter chịu trách nhiệm chuyển DTO của nguồn dữ liệu → các model này. Agent core, tools
và composer chỉ làm việc với các model ở đây.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Freshness(str, Enum):
    REALTIME = "realtime"    # đọc trực tiếp từ source of truth trong lượt này
    REFERENCE = "reference"  # dữ liệu cấu trúc ít thay đổi (danh mục, cửa hàng)
    SEMANTIC = "semantic"    # knowledge dạng văn bản (policy, FAQ, hướng dẫn)


class SourceRef(BaseModel):
    """Truy vết nguồn cho mỗi thông tin trả lời."""
    system: Literal["backend"]
    resource: str
    freshness: Freshness
    fetched_at: datetime
    record_id: str | None = None
    version: str | None = None
    verified: bool = True


# ---------------- Catalog ----------------
class Variant(BaseModel):
    id: str
    sku: str | None = None
    color: str | None = None
    dimensions: str | None = None
    price: float | None = None
    updated_at: str | None = None


class ProductSummary(BaseModel):
    id: str
    name: str
    category: str | None = None
    material: str | None = None
    supplier_name: str | None = None
    price_from: float | None = None
    image_url: str | None = None
    status: str | None = None


class Product(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str | None = None
    category_id: str | None = None
    category: str | None = None
    material_id: str | None = None
    material: str | None = None
    supplier_id: str | None = None
    supplier_name: str | None = None
    variants: list[Variant] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    updated_at: str | None = None

    def variant_by_sku(self, sku: str) -> Variant | None:
        key = sku.strip().lower()
        return next((v for v in self.variants if (v.sku or "").lower() == key), None)

    @property
    def price_range(self) -> tuple[float, float] | None:
        prices = [v.price for v in self.variants if v.price is not None]
        return (min(prices), max(prices)) if prices else None


class ProductPage(BaseModel):
    items: list[ProductSummary]
    total: int
    page: int
    size: int


class SearchCriteria(BaseModel):
    keyword: str | None = Field(default=None, max_length=200)
    category_id: str | None = None
    material_id: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    room: str | None = None
    style: str | None = None
    available_only: bool = False
    page: int = Field(default=0, ge=0)
    size: int = Field(default=10, ge=1, le=50)


class NamedRef(BaseModel):
    id: str
    name: str
    slug: str | None = None
    parent_id: str | None = None
    updated_at: str | None = None


# ---------------- Inventory ----------------
class StoreStock(BaseModel):
    store_id: str
    quantity: int
    updated_at: str | None = None


class InventoryInfo(BaseModel):
    variant_id: str
    sku: str | None = None
    total: int
    by_store: list[StoreStock] = Field(default_factory=list)


# ---------------- Suppliers / stores ----------------
class Branch(BaseModel):
    id: str
    name: str | None = None
    address: str | None = None
    district: str | None = None
    city: str | None = None
    phone: str | None = None
    distance_km: float | None = None
    kind: Literal["showroom", "retailer", "workshop"] | None = None
    supplier_id: str | None = None


class SupplierInfo(BaseModel):
    """Hồ sơ CÔNG KHAI của nhà cung cấp (Backend /api/suppliers/{id}/public + /stores). Không có giờ mở cửa."""
    id: str
    name: str
    type: str | None = None
    description: str | None = None
    phone: str | None = None
    email: str | None = None
    stores: list[Branch] = Field(default_factory=list)   # Backend công khai: quận/thành phố


# ---------------- Orders (đơn đặt làm của chính khách) ----------------
class OrderStatusChange(BaseModel):
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    created_at: str | None = None


class CustomOrder(BaseModel):
    id: str
    order_number: str | None = None
    status: str
    workshop_name: str | None = None
    total_amount: float | None = None
    lead_time_days: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    history: list[OrderStatusChange] = Field(default_factory=list)


# ---------------- Knowledge (FAQ/hướng dẫn Web/App) ----------------
class KnowledgeKind(str, Enum):
    FAQ = "faq"
    GUIDE = "guide"


class KnowledgeHit(BaseModel):
    document_id: str
    kind: KnowledgeKind
    title: str
    snippet: str
    score: float
    version: int | None = None


# ---------------- Workshops / 3D ----------------
class DesignTask(BaseModel):
    task_id: str
    status: str
    progress: int | None = None
    model_url: str | None = None
    poster_url: str | None = None
    error_message: str | None = None
