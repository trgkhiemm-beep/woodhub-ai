"""Tools READ / SEARCH / REALTIME — agent CHỈ ĐỌC dữ liệu thật qua Backend."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.domain import errors
from app.domain.messages import NO_INFO
from app.domain.models import Freshness, KnowledgeKind, NamedRef, SupplierInfo
from app.domain.results import ToolResult, ToolStatus
from app.nlp.vietnamese import remove_vietnamese_diacritics
from app.tools.base import OperationType, ToolContext, ToolInput, ToolSpec
from app.tools.common import SKU_PATTERN, ProductRef, error_result, pick_variant, remember_product, resolve_product

def _norm(text: str | None) -> str:
    return remove_vietnamese_diacritics((text or "").lower()).strip()


def match_named(items: list[NamedRef], name: str) -> NamedRef | None:
    key = _norm(name)
    exact = [i for i in items if _norm(i.name) == key]
    if exact:
        return exact[0]
    partial = sorted((i for i in items if key and key in _norm(i.name)), key=lambda i: len(i.name))
    return partial[0] if partial else None


# ---------------- Supplier (nhà cung cấp) ----------------
class SupplierInfoInput(ToolInput):
    """Đúng MỘT nguồn xác định nhà cung cấp: id, tên, hoặc sản phẩm (id/mã) mà khách đang hỏi."""
    supplier_id: str | None = Field(default=None, max_length=64)
    supplier_name: str | None = Field(default=None, min_length=2, max_length=120)
    product_id: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, pattern=SKU_PATTERN)
    fields: list[Literal["hotline", "email", "address", "opening_hours"]] | None = None
    topic: Literal["shipping", "return", "warranty", "payment", "terms", "privacy"] | None = None  # hỏi chính sách của NCC

    @model_validator(mode="after")
    def _one_source(self) -> "SupplierInfoInput":
        if sum(x is not None for x in (self.supplier_id, self.supplier_name, self.product_id, self.sku)) > 1:
            raise ValueError("Chỉ cung cấp một trong supplier_id, supplier_name, product_id hoặc sku.")
        return self


def _match_supplier(items: list[SupplierInfo], name: str) -> SupplierInfo | None:
    key = _norm(name)
    exact = [s for s in items if _norm(s.name) == key]
    partial = sorted((s for s in items if key and key in _norm(s.name)), key=lambda s: len(s.name))
    return (exact or partial or [None])[0]


async def get_supplier_info(args: SupplierInfoInput, ctx: ToolContext) -> ToolResult:
    """Thông tin liên hệ của NHÀ CUNG CẤP (không dùng một 'thông tin cửa hàng chung' của WoodHub)."""
    store = ctx.ports.store
    product_name = None
    try:
        supplier_id = args.supplier_id
        if args.product_id or args.sku:
            product = await resolve_product("get_supplier_info", ProductRef(product_id=args.product_id, sku=args.sku), ctx)
            if isinstance(product, ToolResult):
                return product
            if not product.supplier_id:
                return ToolResult(tool="get_supplier_info", status=ToolStatus.NOT_FOUND, message=NO_INFO)
            supplier_id, product_name = product.supplier_id, product.name
        elif args.supplier_name:
            found = _match_supplier(await store.list_suppliers(ctx.principal), args.supplier_name)
            if found is None:
                return ToolResult(tool="get_supplier_info", status=ToolStatus.NOT_FOUND, message=NO_INFO)
            supplier_id = found.id
        if not supplier_id:
            names = [s.name for s in await store.list_suppliers(ctx.principal)]
            return ToolResult(tool="get_supplier_info", status=ToolStatus.NEEDS_INPUT,
                              message="Bạn muốn hỏi thông tin của nhà cung cấp nào?",
                              data={"candidates": [{"name": n} for n in names[:10]]})
        info = await store.get_supplier(supplier_id, ctx.principal)
    except errors.PortError as exc:
        return error_result("get_supplier_info", exc)
    data = {**info.model_dump(), "product": product_name, "fields": args.fields or [], "topic": args.topic}
    return ToolResult(tool="get_supplier_info", status=ToolStatus.OK, data=data,
                      sources=[ctx.source("suppliers", Freshness.REFERENCE, system=store.source_system, record_id=info.id)])


class BranchInput(ToolInput):
    city: str | None = Field(default=None, max_length=64)


async def list_branches(args: BranchInput, ctx: ToolContext) -> ToolResult:
    try:
        items = await ctx.ports.store.list_branches(ctx.principal, city=args.city)
    except errors.PortError as exc:
        return error_result("list_branches", exc)
    status = ToolStatus.OK if items else ToolStatus.NOT_FOUND
    return ToolResult(tool="list_branches", status=status, data=[b.model_dump() for b in items],
                      message=None if items else NO_INFO,
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
                      data=[b.model_dump() for b in items], message=None if items else NO_INFO,
                      sources=[ctx.source("workshops_nearby", Freshness.REFERENCE, system=ctx.ports.store.source_system)])


# ---------------- Orders (đơn của chính khách) ----------------
class OrderStatusInput(ToolInput):
    order_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")


async def get_order_status(args: OrderStatusInput, ctx: ToolContext) -> ToolResult:
    try:
        orders = ([await ctx.ports.orders.get_order(args.order_id, ctx.principal)] if args.order_id
                  else await ctx.ports.orders.list_my_orders(ctx.principal, limit=3))
    except errors.PortError as exc:
        return error_result("get_order_status", exc)
    if not orders:
        return ToolResult(tool="get_order_status", status=ToolStatus.NOT_FOUND, message="Chưa tìm thấy đơn hàng nào của bạn.")
    return ToolResult(tool="get_order_status", status=ToolStatus.OK, data=[o.model_dump() for o in orders],
                      sources=[ctx.source("custom_orders", Freshness.REALTIME, system=ctx.ports.orders.source_system,
                                          record_id=o.id) for o in orders])


# ---------------- Catalog ----------------
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


async def _stock_of(product, variant, ctx: ToolContext) -> int | None:
    """Tổng tồn kho THẬT của biến thể (hoặc các biến thể); Backend không công khai → None (UI: "Chưa có thông tin")."""
    variants = [variant] if variant else product.variants[:3]
    total = 0
    try:
        for v in variants:
            total += (await ctx.ports.inventory.get_inventory(v.id, ctx.principal)).total
    except errors.CapabilityUnavailable:
        return None
    return total if variants else None


class CompareInput(ToolInput):
    products: list[ProductRef] = Field(min_length=2, max_length=3)


async def compare_products(args: CompareInput, ctx: ToolContext) -> ToolResult:
    rows = []
    for ref in args.products:
        product = await resolve_product("compare_products", ref, ctx)
        if isinstance(product, ToolResult):
            return product
        v = pick_variant(product, ref.sku)
        prices = product.price_range
        stock = await _stock_of(product, v, ctx)
        rows.append({"stock": stock, "code": ref.sku, "name": product.name, "material": product.material, "category": product.category,
                     "price": v.price if v else (prices[0] if prices else None),
                     "dimensions": v.dimensions if v else next((x.dimensions for x in product.variants if x.dimensions), None),
                     "color": v.color if v else None, "product_id": product.id, "supplier": product.supplier_name})
    return ToolResult(tool="compare_products", status=ToolStatus.OK, data={"rows": rows},
                      sources=[ctx.source("products", Freshness.REALTIME, system=ctx.ports.catalog.source_system,
                                          record_id=r["product_id"]) for r in rows])


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


class KnowledgeSearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=300)
    kinds: list[KnowledgeKind] = Field(default_factory=lambda: [KnowledgeKind.FAQ, KnowledgeKind.GUIDE])
    top_k: int = Field(default=3, ge=1, le=5)


async def search_knowledge(args: KnowledgeSearchInput, ctx: ToolContext) -> ToolResult:
    try:
        hits = await ctx.ports.knowledge.search(args.query, args.kinds, args.top_k, ctx.principal)
    except errors.PortError as exc:
        return error_result("search_knowledge", exc)
    return ToolResult(tool="search_knowledge", status=ToolStatus.OK if hits else ToolStatus.NOT_FOUND,
                      data=[h.model_dump(mode="json") for h in hits], message=None if hits else NO_INFO,
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
    ToolSpec("get_supplier_info", "Liên hệ/khu vực của NHÀ CUNG CẤP theo sản phẩm, tên hoặc id (không có giờ mở cửa).",
             SupplierInfoInput, OperationType.READ, "low", "Backend /api/suppliers/{id}/public + /stores",
             read_handler=get_supplier_info),
    ToolSpec("list_branches", "Cửa hàng của các nhà cung cấp bán lẻ, lọc theo thành phố.", BranchInput,
             OperationType.READ, "low", "Backend /api/suppliers/public + /stores", read_handler=list_branches),
    ToolSpec("find_nearby_workshops", "Tìm xưởng gia công gần vị trí khách (vị trí lấy từ thiết bị).",
             WorkshopInput, OperationType.READ, "low", "Backend /api/stores/nearby/workshops",
             read_handler=find_nearby_workshops),
    ToolSpec("get_product", "Chi tiết một sản phẩm (giá theo biến thể, kích thước, màu, chất liệu, nhà cung cấp) theo SKU, id hoặc tên.",
             GetProductInput, OperationType.REALTIME, "low", "Backend /api/products/{id}",
             read_handler=get_product),
    ToolSpec("compare_products", "So sánh 2-3 sản phẩm (mã hoặc id), kể cả khác nhà cung cấp.", CompareInput,
             OperationType.READ, "low", "Backend /api/products/{id}", read_handler=compare_products),
    ToolSpec("get_inventory", "Tình trạng còn/hết hàng realtime của sản phẩm/biến thể.", InventoryInput,
             OperationType.REALTIME, "low", "Backend /api/variants/{id}/inventory (supplier) · chưa có API công khai",
             read_handler=get_inventory),
    ToolSpec("get_order_status", "Trạng thái đơn đặt làm của khách (Backend quyết định quyền xem).", OrderStatusInput,
             OperationType.REALTIME, "low", "Backend /api/custom-orders/my|{id}",
             read_handler=get_order_status),
    ToolSpec("search_knowledge", "FAQ / hướng dẫn sử dụng website, app WoodHub.", KnowledgeSearchInput,
             OperationType.SEARCH, "low", "Backend knowledge (chưa có nguồn)", read_handler=search_knowledge),
    ToolSpec("list_taxonomy", "Liệt kê danh mục, chất liệu, loại phòng hoặc phong cách.", TaxonomyInput,
             OperationType.READ, "low", "Backend /api/categories|materials|rooms|styles",
             read_handler=list_taxonomy),
    ToolSpec("get_design_task_status", "Trạng thái task tạo mẫu 3D của chính người dùng.", DesignTaskInput,
             OperationType.REALTIME, "low", "Backend /api/custom/ai/tasks/{id}",
             read_handler=get_design_task_status),
]
