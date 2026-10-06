"""
Adapter REAL cho WoodHub Backend — CHỈ ĐỌC (mọi request là GET).

Chỉ dùng các endpoint đã kiểm chứng trong OpenAPI của Backend. Năng lực Backend CHƯA có (FAQ/hướng dẫn,
tồn kho công khai, tra SKU trực tiếp) ném CapabilityUnavailable — Agent trả "chưa có thông tin đã xác minh"
thay vì bịa. Xem docs/BACKEND_INTEGRATION.md.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.adapters.backend.client import BackendClient, require_dict, require_list
from app.domain import errors
from app.domain.models import (
    Branch, CustomOrder, DesignTask, InventoryInfo, KnowledgeHit, KnowledgeKind, NamedRef, OrderStatusChange, Product,
    ProductPage, ProductSummary, SearchCriteria, StoreStock, SupplierInfo, Variant,
)
from app.domain.principal import Principal


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise errors.MalformedResponse("Giá trị số từ Backend không hợp lệ.", detail=repr(value)) from exc


def _product_from_dto(dto: dict[str, Any]) -> Product:
    dto = require_dict(dto, "sản phẩm")
    if not dto.get("id") or not dto.get("name"):
        raise errors.MalformedResponse("Sản phẩm từ Backend thiếu id/name.", detail=repr(dto)[:200])
    variants = [
        Variant(id=str(v.get("id")), sku=v.get("sku"), color=v.get("color"), dimensions=v.get("dimensions"),
                price=_num(v.get("price")), updated_at=v.get("updatedAt"))
        for v in (dto.get("variants") or []) if isinstance(v, dict) and v.get("id")
    ]
    images = sorted((i for i in (dto.get("images") or []) if isinstance(i, dict) and i.get("url")),
                    key=lambda i: (not i.get("primary"), i.get("sortOrder") or 0))
    return Product(
        id=str(dto["id"]), name=dto["name"], description=dto.get("description"), status=dto.get("status"),
        category_id=dto.get("categoryId"), category=dto.get("categoryName"),
        material_id=dto.get("materialId"), material=dto.get("materialName"),
        supplier_id=dto.get("supplierId"), supplier_name=dto.get("supplierName"),
        variants=variants, image_urls=[i["url"] for i in images], updated_at=dto.get("updatedAt"),
    )


class BackendCatalogAdapter:
    source_system = "backend"

    def __init__(self, client: BackendClient, sku_scan_max_products: int = 40, taxonomy_ttl_seconds: int = 600):
        self._client = client
        self._sku_scan_max = sku_scan_max_products
        self._taxonomy_ttl = taxonomy_ttl_seconds
        self._taxonomy_cache: dict[str, tuple[float, list[NamedRef]]] = {}
        self.cache_hits = 0

    async def search_products(self, criteria: SearchCriteria, principal: Principal) -> ProductPage:
        params = {
            "keyword": criteria.keyword, "categoryId": criteria.category_id, "materialId": criteria.material_id,
            "minPrice": criteria.min_price, "maxPrice": criteria.max_price, "room": criteria.room,
            "style": criteria.style, "available": "true" if criteria.available_only else None,
            "page": criteria.page, "size": criteria.size,
        }
        data = require_dict(await self._client.request("GET", "/api/products", principal, params=params), "danh sách sản phẩm")
        content = require_list(data.get("content", []), "danh sách sản phẩm")
        page = data.get("page") or {}
        items = [
            ProductSummary(id=str(p["id"]), name=p["name"], category=p.get("categoryName"), material=p.get("materialName"),
                           supplier_name=p.get("supplierName"), price_from=_num(p.get("priceFrom")),
                           image_url=p.get("primaryImageUrl"), status=p.get("status"))
            for p in content if isinstance(p, dict) and p.get("id") and p.get("name")
        ]
        return ProductPage(items=items, total=int(page.get("totalElements", len(items))),
                           page=int(page.get("number", criteria.page)), size=int(page.get("size", criteria.size)))

    async def get_product(self, product_id: str, principal: Principal) -> Product:
        return _product_from_dto(await self._client.request("GET", f"/api/products/{product_id}", principal))

    async def find_product_by_sku(self, sku: str, principal: Principal) -> Product:
        """Backend chưa có tra cứu theo SKU (GAP B.2) → tìm theo keyword rồi quét có giới hạn."""
        seen: set[str] = set()
        candidates: list[str] = []
        first = await self.search_products(SearchCriteria(keyword=sku, size=5), principal)
        candidates += [p.id for p in first.items]
        page = 0
        while len(candidates) < self._sku_scan_max:
            batch = await self.search_products(SearchCriteria(page=page, size=20), principal)
            candidates += [p.id for p in batch.items]
            page += 1
            if (page * 20) >= batch.total or not batch.items:
                break
        sem = asyncio.Semaphore(5)

        async def load(pid: str) -> Product | None:
            async with sem:
                try:
                    return await self.get_product(pid, principal)
                except errors.NotFound:
                    return None

        ordered = [pid for pid in candidates if not (pid in seen or seen.add(pid))][: self._sku_scan_max]
        for product in await asyncio.gather(*(load(pid) for pid in ordered)):
            if product and product.variant_by_sku(sku):
                return product
        raise errors.NotFound(f"Không tìm thấy sản phẩm có mã {sku}.")

    async def _named(self, path: str, principal: Principal) -> list[NamedRef]:
        # Danh mục/chất liệu/phòng/phong cách là dữ liệu tĩnh, công khai → cache TTL ngắn (giá/tồn kho KHÔNG cache).
        hit = self._taxonomy_cache.get(path)
        if hit and hit[0] > time.monotonic():
            self.cache_hits += 1
            return hit[1]
        data = require_list(await self._client.request("GET", path, principal), path)
        items = [NamedRef(id=str(x["id"]), name=x["name"], slug=x.get("slug"), parent_id=x.get("parentId"),
                          updated_at=x.get("createdAt")) for x in data if isinstance(x, dict) and x.get("id")]
        if self._taxonomy_ttl:
            self._taxonomy_cache[path] = (time.monotonic() + self._taxonomy_ttl, items)
        return items

    async def list_categories(self, principal: Principal) -> list[NamedRef]:
        return await self._named("/api/categories", principal)

    async def list_materials(self, principal: Principal) -> list[NamedRef]:
        return await self._named("/api/materials", principal)

    async def list_rooms(self, principal: Principal) -> list[NamedRef]:
        return await self._named("/api/rooms", principal)

    async def list_styles(self, principal: Principal) -> list[NamedRef]:
        return await self._named("/api/styles", principal)


class BackendInventoryAdapter:
    """Tồn kho: Backend hiện chỉ trả cho nhà cung cấp sở hữu sản phẩm. Agent KHÔNG tự kiểm tra role — gọi Backend với
    token được chuyển tiếp; Backend từ chối → coi là "chưa có dữ liệu tồn kho công khai" (không đoán)."""
    source_system = "backend"

    def __init__(self, client: BackendClient):
        self._client = client

    async def get_inventory(self, variant_id: str, principal: Principal) -> InventoryInfo:
        try:
            dto = require_dict(await self._client.request("GET", f"/api/variants/{variant_id}/inventory", principal), "tồn kho")
        except (errors.Forbidden, errors.Unauthenticated) as exc:
            raise errors.CapabilityUnavailable("Backend chưa công khai tồn kho của sản phẩm này.", detail=exc.detail) from exc
        stores = [StoreStock(store_id=str(s.get("storeId")), quantity=int(s.get("stockQuantity") or 0), updated_at=s.get("updatedAt"))
                  for s in (dto.get("stores") or []) if isinstance(s, dict)]
        sku = next((s.get("sku") for s in (dto.get("stores") or []) if isinstance(s, dict) and s.get("sku")), None)
        return InventoryInfo(variant_id=str(dto.get("variantId") or variant_id), sku=sku,
                             total=int(dto.get("totalStock") or 0), by_store=stores)


class BackendStoreAdapter:
    """Nhà cung cấp (hồ sơ công khai) + cửa hàng/chi nhánh. Backend công khai: tên, mô tả, email, điện thoại; chi nhánh chỉ
    có quận/thành phố. Backend KHÔNG có giờ mở cửa → agent nói chưa có thông tin."""
    source_system = "backend"

    def __init__(self, client: BackendClient, cache_seconds: int = 300):
        self._client = client
        self._ttl = cache_seconds
        self._suppliers: tuple[float, list[SupplierInfo]] | None = None

    @staticmethod
    def _supplier(dto: dict[str, Any]) -> SupplierInfo:
        if not dto.get("id") or not dto.get("businessName"):
            raise errors.MalformedResponse("Nhà cung cấp từ Backend thiếu id/businessName.", detail=repr(dto)[:200])
        return SupplierInfo(id=str(dto["id"]), name=dto["businessName"], type=dto.get("type"),
                            description=dto.get("description"), phone=dto.get("contactPhone"), email=dto.get("contactEmail"))

    async def list_suppliers(self, principal: Principal) -> list[SupplierInfo]:
        """Danh sách nhà cung cấp công khai (cache ngắn: dữ liệu hồ sơ, không phải giá/tồn kho)."""
        if self._suppliers and self._suppliers[0] > time.monotonic():
            return self._suppliers[1]
        data = require_dict(await self._client.request("GET", "/api/suppliers/public", principal,
                                                       params={"size": 50}), "nhà cung cấp")
        items = [self._supplier(s) for s in require_list(data.get("content", []), "nhà cung cấp") if isinstance(s, dict)]
        self._suppliers = (time.monotonic() + self._ttl, items)
        return items

    async def get_supplier(self, supplier_id: str, principal: Principal) -> SupplierInfo:
        dto = require_dict(await self._client.request("GET", f"/api/suppliers/{supplier_id}/public", principal), "nhà cung cấp")
        info = self._supplier(dto)
        stores = require_list(await self._client.request("GET", f"/api/suppliers/{supplier_id}/stores", principal), "cửa hàng")
        info.stores = [Branch(id=str(st.get("id")), name=info.name, district=st.get("district"), city=st.get("city"),
                              kind=st.get("supplierType"), supplier_id=info.id)
                       for st in stores if isinstance(st, dict) and st.get("id")]
        return info

    async def list_branches(self, principal: Principal, city: str | None = None) -> list[Branch]:
        """Cửa hàng của các nhà cung cấp bán lẻ (Backend chỉ công khai quận/thành phố)."""
        branches: list[Branch] = []
        for s in [x for x in await self.list_suppliers(principal) if x.type == "retailer"][:10]:
            stores = require_list(await self._client.request("GET", f"/api/suppliers/{s.id}/stores", principal), "cửa hàng")
            for st in stores:
                if not isinstance(st, dict):
                    continue
                if city and city.lower() not in (st.get("city") or "").lower():
                    continue
                branches.append(Branch(id=str(st.get("id")), name=s.name, district=st.get("district"),
                                       city=st.get("city"), phone=s.phone, kind="retailer", supplier_id=s.id))
        return branches

    async def find_nearby_workshops(self, lat: float, lng: float, limit: int, principal: Principal) -> list[Branch]:
        data = require_list(await self._client.request("GET", "/api/stores/nearby/workshops", principal,
                                                       params={"lat": lat, "lng": lng, "limit": limit}), "xưởng")
        return [Branch(id=str(x.get("id")), name=x.get("businessName"),
                       address=", ".join(p for p in (x.get("address"), x.get("ward"), x.get("district"), x.get("city")) if p),
                       district=x.get("district"), city=x.get("city"), phone=x.get("phone"),
                       distance_km=_num(x.get("distanceKm")), kind="workshop", supplier_id=x.get("supplierId"))
                for x in data if isinstance(x, dict)]


class BackendOrderAdapter:
    """Đơn đặt làm (custom order) của chính người dùng — Backend chỉ trả đơn thuộc về token gửi kèm."""
    source_system = "backend"

    def __init__(self, client: BackendClient):
        self._client = client

    @staticmethod
    def _order(dto: dict[str, Any]) -> CustomOrder:
        if not dto.get("id") or not dto.get("status"):
            raise errors.MalformedResponse("Đơn hàng từ Backend thiếu id/status.", detail=repr(dto)[:200])
        hist = [OrderStatusChange(from_status=h.get("fromStatus"), to_status=h.get("toStatus"), note=h.get("note"),
                                  created_at=h.get("createdAt")) for h in (dto.get("history") or []) if isinstance(h, dict)]
        return CustomOrder(id=str(dto["id"]), order_number=dto.get("orderNumber"), status=str(dto["status"]),
                           workshop_name=dto.get("workshopName"), total_amount=_num(dto.get("totalAmount")),
                           lead_time_days=dto.get("leadTimeDays"), created_at=dto.get("createdAt"),
                           updated_at=dto.get("updatedAt"), history=hist)

    async def list_my_orders(self, principal: Principal, limit: int = 5) -> list[CustomOrder]:
        data = require_dict(await self._client.request("GET", "/api/custom-orders/my", principal,
                                                       params={"size": limit, "sort": "updatedAt,DESC"}), "đơn hàng")
        return [self._order(o) for o in require_list(data.get("content", []), "đơn hàng") if isinstance(o, dict)]

    async def get_order(self, order_id: str, principal: Principal) -> CustomOrder:
        return self._order(require_dict(await self._client.request("GET", f"/api/custom-orders/{order_id}", principal), "đơn hàng"))


class BackendKnowledgeAdapter:
    """FAQ/hướng dẫn Web/App. Backend/Supabase hiện CHƯA có nguồn (không có bảng FAQ) → CapabilityUnavailable,
    agent trả "chưa có thông tin đã xác minh" thay vì dùng kiến thức của model."""
    source_system = "backend"
    _MSG = "Backend chưa có nguồn FAQ/hướng dẫn sử dụng."

    def __init__(self, client: BackendClient):
        self._client = client

    async def search(self, query: str, kinds: list[KnowledgeKind], top_k: int, principal: Principal) -> list[KnowledgeHit]:
        raise errors.CapabilityUnavailable(self._MSG)


class BackendDesignAdapter:
    source_system = "backend"

    def __init__(self, client: BackendClient):
        self._client = client

    async def get_design_task(self, task_id: str, principal: Principal) -> DesignTask:
        dto = require_dict(await self._client.request("GET", f"/api/custom/ai/tasks/{task_id}", principal), "task 3D")
        return DesignTask(task_id=str(dto.get("taskId") or task_id), status=str(dto.get("status")),
                          progress=dto.get("progress"), model_url=dto.get("modelUrl"),
                          poster_url=dto.get("posterUrl"), error_message=dto.get("errorMessage"))
