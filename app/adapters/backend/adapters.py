"""
Adapter REAL cho WoodHub Backend.

Chỉ dùng các endpoint đã kiểm chứng trong OpenAPI snapshot. Năng lực mà Backend CHƯA có
(thông tin cửa hàng, promotion, policy/FAQ, tồn kho công khai, tra SKU trực tiếp) ném
CapabilityUnavailable — Agent sẽ trả "chưa có thông tin đã xác minh" thay vì bịa.
Xem docs/BACKEND_INTEGRATION.md (Phần B) cho contract đề xuất của các GAP này.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from app.adapters.backend.client import BackendClient, require_dict, require_list
from app.domain import errors
from app.domain.models import (
    Branch, DesignTask, InventoryInfo, KnowledgeDocument, KnowledgeHit, KnowledgeKind, NamedRef, PolicyType,
    Product, ProductPage, ProductSummary, Promotion, PromotionStatus, SearchCriteria, StoreInfo, StoreStock, Variant,
)
from app.domain.principal import Principal, Role


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


class BackendIdentityAdapter:
    source_system = "backend"

    def __init__(self, client: BackendClient, cache_seconds: int = 60):
        self._client = client
        self._ttl = cache_seconds
        self._cache: dict[str, tuple[float, Principal]] = {}

    async def resolve(self, access_token: str) -> Principal:
        key = hashlib.sha256(access_token.encode()).hexdigest()
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        try:
            dto = require_dict(await self._client.request("GET", "/api/users/me", access_token=access_token), "người dùng")
        except (errors.Forbidden, errors.Unauthenticated, errors.NotFound) as exc:
            raise errors.Unauthenticated("Token không hợp lệ hoặc đã hết hạn.", detail=exc.detail) from exc
        if not dto.get("id"):
            raise errors.MalformedResponse("Thiếu id người dùng từ Backend.")
        principal = Principal(user_id=str(dto["id"]), role=Role.from_backend(dto.get("role")), email=dto.get("email"),
                              display_name=dto.get("fullName"), access_token=access_token)
        if self._ttl:
            if len(self._cache) > 5000:
                self._cache.clear()
            self._cache[key] = (time.monotonic() + self._ttl, principal)
        return principal


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

    async def update_product_description(self, product: Product, description: str, principal: Principal) -> Product:
        # UpdateProductRequest bắt buộc name + categoryId → gửi lại giá trị hiện tại.
        if not product.category_id:
            raise errors.ValidationFailed("Thiếu categoryId hiện tại của sản phẩm để cập nhật.")
        body = {"name": product.name, "description": description, "categoryId": product.category_id,
                "materialId": product.material_id}
        return _product_from_dto(await self._client.request("PUT", f"/api/products/{product.id}", principal, json=body))

    async def update_variant_price(self, product: Product, variant_id: str, price: float, principal: Principal) -> Product:
        variant = next((v for v in product.variants if v.id == variant_id), None)
        if variant is None:
            raise errors.NotFound("Không tìm thấy biến thể sản phẩm.")
        body = {"price": price, "sku": variant.sku, "color": variant.color, "dimensions": variant.dimensions}
        await self._client.request("PUT", f"/api/variants/{variant_id}", principal, json=body)
        return await self.get_product(product.id, principal)

    async def upsert_category(self, category_id: str | None, name: str, parent_id: str | None, principal: Principal) -> NamedRef:
        body = {"name": name, "parentId": parent_id}
        self._taxonomy_cache.clear()
        if category_id:
            dto = await self._client.request("PUT", f"/api/categories/{category_id}", principal, json=body)
        else:
            dto = await self._client.request("POST", "/api/categories", principal, json=body)
        dto = require_dict(dto, "danh mục")
        return NamedRef(id=str(dto["id"]), name=dto["name"], slug=dto.get("slug"), parent_id=dto.get("parentId"))

    async def upsert_material(self, material_id: str | None, name: str, principal: Principal) -> NamedRef:
        self._taxonomy_cache.clear()
        if material_id:
            dto = await self._client.request("PUT", f"/api/materials/{material_id}", principal, json={"name": name})
        else:
            dto = await self._client.request("POST", "/api/materials", principal, json={"name": name})
        dto = require_dict(dto, "vật liệu")
        return NamedRef(id=str(dto["id"]), name=dto["name"])


class BackendInventoryAdapter:
    """GET/PATCH tồn kho của Backend chỉ dành cho supplier sở hữu sản phẩm (RBAC của Backend)."""
    source_system = "backend"

    def __init__(self, client: BackendClient):
        self._client = client

    async def get_inventory(self, variant_id: str, principal: Principal) -> InventoryInfo:
        if principal.role != Role.SUPPLIER:
            raise errors.CapabilityUnavailable("Backend chưa có API tồn kho công khai (chỉ nhà cung cấp sở hữu sản phẩm xem được).")
        dto = require_dict(await self._client.request("GET", f"/api/variants/{variant_id}/inventory", principal), "tồn kho")
        stores = [StoreStock(store_id=str(s.get("storeId")), quantity=int(s.get("stockQuantity") or 0), updated_at=s.get("updatedAt"))
                  for s in (dto.get("stores") or []) if isinstance(s, dict)]
        sku = next((s.get("sku") for s in (dto.get("stores") or []) if isinstance(s, dict) and s.get("sku")), None)
        return InventoryInfo(variant_id=str(dto.get("variantId") or variant_id), sku=sku,
                             total=int(dto.get("totalStock") or 0), by_store=stores)

    async def adjust_inventory(self, store_id: str, variant_id: str, delta: int, principal: Principal) -> InventoryInfo:
        # PATCH theo delta KHÔNG idempotent → client không retry.
        await self._client.request("PATCH", f"/api/stores/{store_id}/inventory/{variant_id}", principal, json={"delta": delta})
        return await self.get_inventory(variant_id, principal)


class BackendStoreAdapter:
    source_system = "backend"

    def __init__(self, client: BackendClient):
        self._client = client

    async def get_store_info(self, principal: Principal) -> StoreInfo:
        raise errors.CapabilityUnavailable("Backend chưa có API thông tin cửa hàng (hotline, giờ mở cửa).")

    async def list_branches(self, principal: Principal, city: str | None = None) -> list[Branch]:
        """Cửa hàng của các nhà cung cấp bán lẻ (Backend chỉ công khai quận/thành phố)."""
        data = require_dict(await self._client.request("GET", "/api/suppliers/public", principal,
                                                       params={"type": "retailer", "size": 10}), "nhà cung cấp")
        suppliers = [s for s in require_list(data.get("content", []), "nhà cung cấp") if isinstance(s, dict) and s.get("id")]
        branches: list[Branch] = []
        for s in suppliers:
            stores = require_list(await self._client.request("GET", f"/api/suppliers/{s['id']}/stores", principal), "cửa hàng")
            for st in stores:
                if not isinstance(st, dict):
                    continue
                if city and city.lower() not in (st.get("city") or "").lower():
                    continue
                branches.append(Branch(id=str(st.get("id")), name=s.get("businessName"), district=st.get("district"),
                                       city=st.get("city"), phone=s.get("contactPhone"), kind="retailer"))
        return branches

    async def find_nearby_workshops(self, lat: float, lng: float, limit: int, principal: Principal) -> list[Branch]:
        data = require_list(await self._client.request("GET", "/api/stores/nearby/workshops", principal,
                                                       params={"lat": lat, "lng": lng, "limit": limit}), "xưởng")
        return [Branch(id=str(x.get("id")), name=x.get("businessName"),
                       address=", ".join(p for p in (x.get("address"), x.get("ward"), x.get("district"), x.get("city")) if p),
                       district=x.get("district"), city=x.get("city"), phone=x.get("phone"),
                       distance_km=_num(x.get("distanceKm")), kind="workshop")
                for x in data if isinstance(x, dict)]

    async def update_store_info(self, changes: dict[str, Any], expected_version: int | None, principal: Principal) -> StoreInfo:
        raise errors.CapabilityUnavailable("Backend chưa có API cập nhật thông tin cửa hàng.")


class BackendPromotionAdapter:
    source_system = "backend"
    _MSG = "Backend chưa có API khuyến mãi/voucher."

    def __init__(self, client: BackendClient):
        self._client = client

    async def list_promotions(self, principal: Principal, status: PromotionStatus | None = None,
                              category_id: str | None = None) -> list[Promotion]:
        raise errors.CapabilityUnavailable(self._MSG)

    async def get_promotion(self, promotion_id: str, principal: Principal) -> Promotion:
        raise errors.CapabilityUnavailable(self._MSG)

    async def create_promotion(self, draft: dict[str, Any], principal: Principal, idempotency_key: str) -> Promotion:
        raise errors.CapabilityUnavailable(self._MSG)

    async def set_promotion_status(self, promotion_id: str, status: PromotionStatus, expected_version: int | None,
                                   principal: Principal) -> Promotion:
        raise errors.CapabilityUnavailable(self._MSG)


class BackendKnowledgeAdapter:
    source_system = "backend"
    _MSG = "Backend chưa có API chính sách/FAQ/hướng dẫn."

    def __init__(self, client: BackendClient):
        self._client = client

    async def get_policy(self, policy_type: PolicyType, principal: Principal) -> KnowledgeDocument:
        raise errors.CapabilityUnavailable(self._MSG)

    async def search(self, query: str, kinds: list[KnowledgeKind], top_k: int, principal: Principal) -> list[KnowledgeHit]:
        raise errors.CapabilityUnavailable(self._MSG)

    async def get_document(self, document_id: str, principal: Principal) -> KnowledgeDocument:
        raise errors.CapabilityUnavailable(self._MSG)

    async def upsert_faq(self, document_id: str | None, question: str, answer: str, expected_version: int | None,
                         principal: Principal) -> KnowledgeDocument:
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
