"""Tools READ / SEARCH / REALTIME — không thay đổi dữ liệu."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.domain import errors
from app.domain.models import Freshness, KnowledgeKind, NamedRef, PolicyType, PromotionStatus, SearchCriteria
from app.domain.principal import Role
from app.domain.results import ToolResult, ToolStatus
from app.nlp.vietnamese import remove_vietnamese_diacritics, restore_diacritics_for_search
from app.tools.base import OperationType, ToolContext, ToolInput, ToolSpec
from app.tools.common import ProductRef, error_result, pick_variant, remember_product, resolve_product

ALL_ROLES = frozenset(Role)
AUTHENTICATED = frozenset({Role.CUSTOMER, Role.SUPPLIER, Role.ADMIN})


def _norm(text: str | None) -> str:
    return remove_vietnamese_diacritics((text or "").lower()).strip()


def match_named(items: list[NamedRef], name: str) -> NamedRef | None:
    key = _norm(name)
    exact = [i for i in items if _norm(i.name) == key]
    if exact:
        return exact[0]
    partial = sorted((i for i in items if key and key in _norm(i.name)), key=lambda i: len(i.name))
    return partial[0] if partial else None


# ---------------- Store ----------------
class StoreInfoInput(ToolInput):
    fields: list[Literal["hotline", "email", "address", "opening_hours", "social_links"]] | None = None


async def get_store_info(args: StoreInfoInput, ctx: ToolContext) -> ToolResult:
    try:
        info = await ctx.ports.store.get_store_info(ctx.principal)
    except errors.PortError as exc:
        return error_result("get_store_info", exc)
    data = info.model_dump(mode="json")
    if args.fields:
        data = {k: v for k, v in data.items() if k in set(args.fields) | {"name", "version", "updated_at"}}
    return ToolResult(tool="get_store_info", status=ToolStatus.OK, data=data,
                      sources=[ctx.source("store_info", Freshness.REFERENCE, system=ctx.ports.store.source_system,
                                          version=info.version)])


class BranchInput(ToolInput):
    city: str | None = Field(default=None, max_length=64)


async def list_branches(args: BranchInput, ctx: ToolContext) -> ToolResult:
    try:
        items = await ctx.ports.store.list_branches(ctx.principal, city=args.city)
    except errors.PortError as exc:
        return error_result("list_branches", exc)
    status = ToolStatus.OK if items else ToolStatus.NOT_FOUND
    return ToolResult(tool="list_branches", status=status, data=[b.model_dump() for b in items],
                      message=None if items else "Chưa có thông tin chi nhánh phù hợp.",
                      sources=[ctx.source("branches", Freshness.REFERENCE, system=ctx.ports.store.source_system)])


class WorkshopInput(ToolInput):
    limit: int = Field(default=5, ge=1, le=10)


async def find_nearby_workshops(args: WorkshopInput, ctx: ToolContext) -> ToolResult:
    if ctx.location is None:
        return ToolResult(tool="find_nearby_workshops", status=ToolStatus.NEEDS_INPUT,
                          message="Bạn hãy bật chia sẻ vị trí để mình tìm xưởng gần bạn nhé.")
    lat, lng = ctx.location
    try:
        items = await ctx.ports.store.find_nearby_workshops(lat, lng, args.limit, ctx.principal)
    except errors.PortError as exc:
        return error_result("find_nearby_workshops", exc)
    return ToolResult(tool="find_nearby_workshops", status=ToolStatus.OK if items else ToolStatus.NOT_FOUND,
                      data=[b.model_dump() for b in items],
                      sources=[ctx.source("workshops_nearby", Freshness.REFERENCE, system=ctx.ports.store.source_system)])


# ---------------- Catalog ----------------
class SearchProductsInput(ToolInput):
    keyword: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=80)
    material: str | None = Field(default=None, max_length=80)
    min_price: float | None = Field(default=None, ge=0, le=10_000_000_000)
    max_price: float | None = Field(default=None, ge=0, le=10_000_000_000)
    room: str | None = Field(default=None, max_length=60)
    style: str | None = Field(default=None, max_length=60)
    available_only: bool = False
    page: int = Field(default=0, ge=0, le=50)


async def search_products(args: SearchProductsInput, ctx: ToolContext) -> ToolResult:
    if args.min_price is not None and args.max_price is not None and args.min_price > args.max_price:
        return ToolResult(tool="search_products", status=ToolStatus.INVALID, message="Khoảng giá không hợp lệ.")
    catalog = ctx.ports.catalog
    try:
        category_id = material_id = None
        keyword = args.keyword
        if args.category:
            cat = match_named(await catalog.list_categories(ctx.principal), args.category)
            if cat:
                category_id = cat.id
            else:
                keyword = " ".join(x for x in (args.category, keyword) if x)
        if args.material:
            mat = match_named(await catalog.list_materials(ctx.principal), args.material)
            if mat:
                material_id = mat.id
            else:
                keyword = " ".join(x for x in (keyword, args.material) if x)
        criteria = SearchCriteria(
            keyword=restore_diacritics_for_search(keyword) if keyword else None, category_id=category_id,
            material_id=material_id, min_price=args.min_price, max_price=args.max_price, room=args.room,
            style=args.style, available_only=args.available_only, page=args.page, size=10,
        )
        page = await catalog.search_products(criteria, ctx.principal)
        relaxed_from = None
        words = (criteria.keyword or "").split()
        if not page.items and len(words) > 1:
            # Backend khớp keyword như một cụm liền nhau → nới lỏng: danh mục hoặc danh từ chính.
            head = words[0]
            cat = None if criteria.category_id else match_named(await catalog.list_categories(ctx.principal), head)
            relaxed = criteria.model_copy(update={"keyword": None if cat else head,
                                                  "category_id": cat.id if cat else criteria.category_id})
            page = await catalog.search_products(relaxed, ctx.principal)
            if page.items:
                relaxed_from, criteria = criteria.keyword, relaxed
    except errors.PortError as exc:
        return error_result("search_products", exc)
    data = {"items": [p.model_dump() for p in page.items], "total": page.total, "page": page.page,
            "criteria": criteria.model_dump(exclude_none=True), "relaxed_from": relaxed_from}
    return ToolResult(tool="search_products", status=ToolStatus.OK if page.items else ToolStatus.NOT_FOUND, data=data,
                      message=None if page.items else "Không tìm thấy sản phẩm phù hợp tiêu chí.",
                      sources=[ctx.source("products.search", Freshness.REALTIME, system=catalog.source_system)])


class GetProductInput(ProductRef):
    pass


async def get_product(args: GetProductInput, ctx: ToolContext) -> ToolResult:
    product = await resolve_product("get_product", args, ctx)
    if isinstance(product, ToolResult):
        return product
    remember_product(ctx, product, args.sku)
    data = product.model_dump()
    data["price_range"] = product.price_range
    if args.sku:
        variant = pick_variant(product, args.sku)
        data["requested_variant"] = variant.model_dump() if variant else None
    return ToolResult(tool="get_product", status=ToolStatus.OK, data=data,
                      sources=[ctx.source("products", Freshness.REALTIME, system=ctx.ports.catalog.source_system,
                                          record_id=product.id, version=product.updated_at)])


class CompareInput(ToolInput):
    skus: list[str] = Field(min_length=2, max_length=3)


async def compare_products(args: CompareInput, ctx: ToolContext) -> ToolResult:
    rows = []
    for sku in args.skus:
        product = await resolve_product("compare_products", ProductRef(sku=sku), ctx)
        if isinstance(product, ToolResult):
            return product
        v = pick_variant(product, sku)
        rows.append({"sku": sku, "name": product.name, "material": product.material, "category": product.category,
                     "price": v.price if v else None, "dimensions": v.dimensions if v else None,
                     "color": v.color if v else None, "product_id": product.id})
    return ToolResult(tool="compare_products", status=ToolStatus.OK, data={"rows": rows},
                      sources=[ctx.source("products", Freshness.REALTIME, system=ctx.ports.catalog.source_system)])


class InventoryInput(ProductRef):
    pass


async def get_inventory(args: InventoryInput, ctx: ToolContext) -> ToolResult:
    product = await resolve_product("get_inventory", args, ctx)
    if isinstance(product, ToolResult):
        return product
    remember_product(ctx, product, args.sku)
    chosen = pick_variant(product, args.sku) if args.sku else None
    variants = [chosen] if chosen else product.variants
    rows = []
    for v in [x for x in variants if x is not None]:
        try:
            inv = await ctx.ports.inventory.get_inventory(v.id, ctx.principal)
            rows.append({"sku": v.sku, "variant_id": v.id, "status": "known", "total": inv.total,
                         "by_store": [s.model_dump() for s in inv.by_store]})
        except errors.CapabilityUnavailable as exc:
            rows.append({"sku": v.sku, "variant_id": v.id, "status": "unknown", "reason": exc.message})
        except errors.PortError as exc:
            return error_result("get_inventory", exc)
    known = any(r["status"] == "known" for r in rows)
    return ToolResult(tool="get_inventory", status=ToolStatus.OK if known else ToolStatus.UNKNOWN,
                      data={"product": product.name, "product_id": product.id, "variants": rows},
                      message=None if known else "Chưa có dữ liệu tồn kho đã xác minh cho sản phẩm này.",
                      sources=[ctx.source("inventory", Freshness.REALTIME, system=ctx.ports.inventory.source_system,
                                          record_id=product.id)] if known else [])


class PromotionsInput(ToolInput):
    category: str | None = Field(default=None, max_length=80)
    code: str | None = Field(default=None, max_length=40)
    status: PromotionStatus | None = None


async def get_promotions(args: PromotionsInput, ctx: ToolContext) -> ToolResult:
    try:
        if args.code:
            promos = [await ctx.ports.promotions.get_promotion(args.code, ctx.principal)]
        else:
            category_id = None
            if args.category:
                cat = match_named(await ctx.ports.catalog.list_categories(ctx.principal), args.category)
                category_id = cat.id if cat else None
            status = args.status if ctx.principal.role == Role.ADMIN else PromotionStatus.ACTIVE
            promos = await ctx.ports.promotions.list_promotions(ctx.principal, status=status, category_id=category_id)
    except errors.PortError as exc:
        return error_result("get_promotions", exc)
    return ToolResult(tool="get_promotions", status=ToolStatus.OK if promos else ToolStatus.NOT_FOUND,
                      data=[p.model_dump(mode="json") for p in promos],
                      message=None if promos else "Hiện không có khuyến mãi đang áp dụng phù hợp.",
                      sources=[ctx.source("promotions", Freshness.REALTIME, system=ctx.ports.promotions.source_system)])


class PolicyInput(ToolInput):
    policy_type: PolicyType


async def get_policy(args: PolicyInput, ctx: ToolContext) -> ToolResult:
    try:
        doc = await ctx.ports.knowledge.get_policy(args.policy_type, ctx.principal)
    except errors.PortError as exc:
        return error_result("get_policy", exc)
    return ToolResult(tool="get_policy", status=ToolStatus.OK, data=doc.model_dump(mode="json"),
                      sources=[ctx.source("policy", Freshness.SEMANTIC, system=ctx.ports.knowledge.source_system,
                                          record_id=doc.id, version=doc.version)])


class KnowledgeSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=300)
    kinds: list[KnowledgeKind] = Field(default_factory=lambda: [KnowledgeKind.FAQ, KnowledgeKind.GUIDE, KnowledgeKind.POLICY])
    top_k: int = Field(default=3, ge=1, le=5)


async def search_knowledge(args: KnowledgeSearchInput, ctx: ToolContext) -> ToolResult:
    try:
        hits = await ctx.ports.knowledge.search(args.query, args.kinds, args.top_k, ctx.principal)
    except errors.PortError as exc:
        return error_result("search_knowledge", exc)
    return ToolResult(tool="search_knowledge", status=ToolStatus.OK if hits else ToolStatus.NOT_FOUND,
                      data=[h.model_dump(mode="json") for h in hits],
                      message=None if hits else "Chưa tìm thấy thông tin đã xác minh cho câu hỏi này.",
                      sources=[ctx.source("knowledge", Freshness.SEMANTIC, system=ctx.ports.knowledge.source_system,
                                          record_id=h.document_id, version=h.version) for h in hits])


class TaxonomyInput(ToolInput):
    kind: Literal["categories", "materials", "rooms", "styles"]


async def list_taxonomy(args: TaxonomyInput, ctx: ToolContext) -> ToolResult:
    fn = {"categories": ctx.ports.catalog.list_categories, "materials": ctx.ports.catalog.list_materials,
          "rooms": ctx.ports.catalog.list_rooms, "styles": ctx.ports.catalog.list_styles}[args.kind]
    try:
        items = await fn(ctx.principal)
    except errors.PortError as exc:
        return error_result("list_taxonomy", exc)
    return ToolResult(tool="list_taxonomy", status=ToolStatus.OK, data={"kind": args.kind, "items": [i.model_dump() for i in items]},
                      sources=[ctx.source(args.kind, Freshness.REFERENCE, system=ctx.ports.catalog.source_system)])


class DesignTaskInput(ToolInput):
    task_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


async def get_design_task_status(args: DesignTaskInput, ctx: ToolContext) -> ToolResult:
    try:
        task = await ctx.ports.design.get_design_task(args.task_id, ctx.principal)
    except errors.PortError as exc:
        return error_result("get_design_task_status", exc)
    return ToolResult(tool="get_design_task_status", status=ToolStatus.OK, data=task.model_dump(),
                      sources=[ctx.source("design_task", Freshness.REALTIME, system=ctx.ports.design.source_system,
                                          record_id=task.task_id)])


READ_TOOLS: list[ToolSpec] = [
    ToolSpec("get_store_info", "Thông tin cửa hàng WoodHub: hotline, email, địa chỉ, giờ mở cửa.", StoreInfoInput,
             OperationType.READ, ALL_ROLES, "low", "Backend (GAP B.1)", read_handler=get_store_info),
    ToolSpec("list_branches", "Danh sách chi nhánh/cửa hàng, lọc theo thành phố.", BranchInput,
             OperationType.READ, ALL_ROLES, "low", "Backend /api/suppliers/public + /stores", read_handler=list_branches),
    ToolSpec("find_nearby_workshops", "Tìm xưởng gia công gần vị trí khách (vị trí lấy từ thiết bị, cần đăng nhập).",
             WorkshopInput, OperationType.READ, AUTHENTICATED, "low", "Backend /api/stores/nearby/workshops",
             read_handler=find_nearby_workshops, requires_auth=True),
    ToolSpec("search_products", "Tìm sản phẩm theo từ khóa, danh mục, chất liệu, khoảng giá (VND), phòng, phong cách.",
             SearchProductsInput, OperationType.SEARCH, ALL_ROLES, "low", "Backend /api/products",
             read_handler=search_products),
    ToolSpec("get_product", "Chi tiết một sản phẩm (giá theo biến thể, kích thước, màu, chất liệu, ảnh) theo SKU, id hoặc tên.",
             GetProductInput, OperationType.REALTIME, ALL_ROLES, "low", "Backend /api/products/{id}",
             read_handler=get_product),
    ToolSpec("compare_products", "So sánh 2-3 sản phẩm theo SKU.", CompareInput, OperationType.READ, ALL_ROLES, "low",
             "Backend /api/products/{id}", read_handler=compare_products),
    ToolSpec("get_inventory", "Tồn kho realtime của sản phẩm/biến thể.", InventoryInput, OperationType.REALTIME,
             ALL_ROLES, "low", "Backend /api/variants/{id}/inventory (supplier) · GAP B.3 (công khai)",
             read_handler=get_inventory),
    ToolSpec("get_promotions", "Khuyến mãi/voucher đang áp dụng, theo danh mục hoặc mã.", PromotionsInput,
             OperationType.REALTIME, ALL_ROLES, "low", "Backend (GAP B.6)", read_handler=get_promotions),
    ToolSpec("get_policy", "Chính sách chính thức: shipping, return, warranty, payment, terms, privacy.", PolicyInput,
             OperationType.READ, ALL_ROLES, "low", "Backend (GAP B.7)", read_handler=get_policy),
    ToolSpec("search_knowledge", "Tìm FAQ, hướng dẫn sử dụng website/app, chính sách theo nội dung câu hỏi.",
             KnowledgeSearchInput, OperationType.SEARCH, ALL_ROLES, "low", "Backend knowledge search (GAP B.7)",
             read_handler=search_knowledge),
    ToolSpec("list_taxonomy", "Liệt kê danh mục, chất liệu, loại phòng hoặc phong cách.", TaxonomyInput,
             OperationType.READ, ALL_ROLES, "low", "Backend /api/categories|materials|rooms|styles",
             read_handler=list_taxonomy),
    ToolSpec("get_design_task_status", "Trạng thái task tạo mẫu 3D của chính người dùng.", DesignTaskInput,
             OperationType.REALTIME, AUTHENTICATED, "low", "Backend /api/custom/ai/tasks/{id}",
             read_handler=get_design_task_status, requires_auth=True),
]
