"""
Product Advisor: nhu cầu → catalog THẬT → ràng buộc CỨNG → xếp hạng.

- Không nới ngân sách, không gợi ý sản phẩm "gần đúng": sai loại/chất liệu/màu/ngân sách hoặc không xác minh
  được (thiếu giá; thiếu kích thước/số chỗ khi tiêu chí cần) → loại. Không còn sản phẩm → NOT_FOUND (NO_INFO).
- Tiết kiệm truy vấn: 1 lần đọc danh sách (danh mục được cache); chỉ đọc chi tiết khi tiêu chí cần dữ liệu biến thể.
- LLM không tham gia chọn sản phẩm.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from app.domain import errors
from app.domain.messages import NO_INFO
from app.domain.models import Freshness, Product, ProductSummary, SearchCriteria
from app.domain.results import ToolResult, ToolStatus
from app.nlu.lexicon import fold
from app.tools.base import OperationType, ToolContext, ToolInput, ToolSpec
from app.tools.common import error_result
from app.tools.read_tools import match_named

MAX_SCAN = 50      # một trang danh sách (catalog thật hiện có 27 sản phẩm)
MAX_DETAILS = 8    # số chi tiết tối đa khi tiêu chí cần dữ liệu biến thể
# Loại sản phẩm thường dùng cho từng phòng (Backend chưa gắn sản phẩm ↔ phòng: product_rooms rỗng).
ROOM_AFFINITY = {
    "phong lam viec": ("bàn làm việc", "bàn máy tính", "bàn liền hộc", "bàn chân sắt", "kệ sách", "ghế văn phòng"),
    "phong an": ("bàn ăn", "ghế ăn"), "phong khach": ("sofa", "kệ tivi", "bàn trà", "bàn sofa"),
    "phong ngu": ("giường", "tủ", "bàn trang điểm"), "phong tre em": ("bàn học", "giường", "kệ sách"),
}


class RecommendInput(ToolInput):
    category: str | None = Field(default=None, max_length=80)
    material: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=40)
    style: str | None = Field(default=None, max_length=40)
    room: str | None = Field(default=None, max_length=60)
    use_case: str | None = Field(default=None, max_length=80)
    budget_min: float | None = Field(default=None, ge=0, le=10_000_000_000)
    budget_max: float | None = Field(default=None, ge=0, le=10_000_000_000)
    seats: int | None = Field(default=None, ge=1, le=30)
    size: Literal["compact", "large"] | None = None
    price_pref: Literal["low", "high"] | None = None
    max_area_cm2: float | None = Field(default=None, gt=0)
    min_area_cm2: float | None = Field(default=None, gt=0)
    exclude_ids: list[str] = Field(default_factory=list, max_length=20)
    distinct_suppliers: bool = False   # "từ các nhà cung cấp khác nhau" → mỗi nhà cung cấp tối đa 1 mẫu
    supplier: str | None = Field(default=None, max_length=120)  # chỉ sản phẩm của nhà cung cấp này (tên THẬT)
    in_stock: bool = False             # khách yêu cầu "còn hàng": kiểm tra tồn kho thật, hết hàng → loại, chưa rõ → ghi rõ
    limit: int = Field(default=3, ge=1, le=5)
    mode: Literal["recommend", "search"] = "recommend"


@dataclass
class Dims:
    width: float | None = None   # cm
    depth: float | None = None
    height: float | None = None

    @property
    def area(self) -> float | None:
        return self.width * self.depth if self.width and self.depth else None


def parse_dimensions(text: str | None) -> Dims:
    """'W1800 x D900 x H750 mm' | '160 x 90 x 75' | 'W1400 x H750 mm' | '120x60' → cm."""
    if not text:
        return Dims()
    t = text.upper()
    tokens = re.findall(r"([WDHLR]?)\s*(\d+(?:[.,]\d+)?)(?:\s*[-/]\s*\d+)?", t)
    if not tokens:
        return Dims()
    values = [(p, float(n.replace(",", "."))) for p, n in tokens[:4]]
    in_mm = "MM" in t or any(v > 400 for _, v in values)
    scale = 0.1 if in_mm else 1.0
    d = Dims()
    labeled = any(p for p, _ in values)
    if labeled:
        for p, v in values:
            if p in ("W", "L") and d.width is None:
                d.width = v * scale
            elif p in ("W", "L") and d.depth is None:  # 'W1234xW2034' → giường: dài x rộng
                d.depth = v * scale
            elif p in ("D", "R") and d.depth is None:
                d.depth = v * scale
            elif p == "H" and d.height is None:
                d.height = v * scale
    else:
        nums = [v * scale for _, v in values]
        d.width = nums[0]
        d.depth = nums[1] if len(nums) > 1 else None
        d.height = nums[2] if len(nums) > 2 else None
    return d


def seat_capacity(product: Product, dims: Dims) -> tuple[int | None, bool]:
    """(số chỗ, là_ước_tính). Ưu tiên số ghi rõ trong tên/mô tả, sau đó ước tính theo chiều dài bàn ăn."""
    text = fold(f"{product.name} {product.description or ''}")
    m = re.search(r"(\d{1,2})\s*(ghe|cho ngoi|cho|nguoi)\b", text)
    if m:
        return int(m.group(1)), False
    if "ban an" in text and dims.width:
        length = max(dims.width, dims.depth or 0)
        return (8 if length >= 200 else 6 if length >= 150 else 4 if length >= 110 else 2), True
    return None, False


@dataclass
class Candidate:
    summary: ProductSummary
    price: float | None
    product: Product | None = None      # chi tiết — chỉ tải khi tiêu chí cần (số chỗ, kích thước, màu)
    dims: Dims = field(default_factory=Dims)
    dims_text: str | None = None
    seats: int | None = None
    seats_estimated: bool = False
    colors: list[str] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    stock: str | None = None            # in_stock | low_stock | unknown (chỉ khi khách hỏi "còn hàng")


def vnd(v: float | None) -> str:
    return "chưa có giá" if v is None else f"{v:,.0f}đ".replace(",", ".")


def _contains(hay: str, needle: str) -> bool:
    words = [w for w in fold(needle).split() if len(w) > 1 and w not in ("go",)]
    return bool(words) and all(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", fold(hay)) for w in words)


def _needs_detail(a: RecommendInput) -> bool:
    return bool(a.seats or a.size or a.max_area_cm2 or a.min_area_cm2 or a.color)


async def _collect(args: RecommendInput, ctx: ToolContext) -> list[ProductSummary]:
    """MỘT truy vấn danh sách (lọc giá phía Backend); danh mục lấy từ cache. Lọc chính xác ở _matches."""
    catalog = ctx.ports.catalog
    criteria = SearchCriteria(min_price=args.budget_min, max_price=args.budget_max, size=MAX_SCAN)
    if args.category:
        cats = await catalog.list_categories(ctx.principal)
        cat_ref = match_named(cats, args.category) or match_named(cats, args.category.split()[0])
        # Danh mục lá → lọc phía Backend. Danh mục cha/không khớp → quét trang theo giá rồi lọc tên tại chỗ
        # (keyword của Backend cần đúng dấu và đúng cụm liền nhau nên dễ bỏ sót).
        if cat_ref and not any(c.parent_id == cat_ref.id for c in cats):
            criteria = criteria.model_copy(update={"category_id": cat_ref.id})
    page = await catalog.search_products(criteria, ctx.principal)
    return [p for p in page.items if p.id not in args.exclude_ids]


def _matches(s: ProductSummary, a: RecommendInput) -> bool:
    """Ràng buộc CỨNG trên dữ liệu đã xác minh. Không xác minh được → loại (không gợi ý sai)."""
    price = s.price_from
    if (a.budget_max is not None or a.budget_min is not None) and price is None:
        return False
    if a.budget_max is not None and price > a.budget_max:
        return False
    if a.budget_min is not None and price < a.budget_min:
        return False
    if a.category and not (_contains(s.name, a.category) or fold(s.category or "") == fold(a.category)):
        return False
    if a.material and not _contains(f"{s.material or ''} {s.name}", a.material):
        return False
    if a.supplier and fold(a.supplier) not in fold(s.supplier_name or ""):
        return False
    if a.room and not a.category:
        affinity = ROOM_AFFINITY.get(fold(a.room), ())
        if affinity and not any(_contains(s.name, t) for t in affinity):
            return False
    return True


def _matches_detail(c: Candidate, a: RecommendInput) -> bool:
    if a.seats and (c.seats is None or c.seats < a.seats):
        return False
    if a.color and not any(_contains(col, a.color) for col in c.colors):
        return False
    if a.max_area_cm2 and (not c.dims.area or c.dims.area >= a.max_area_cm2):
        return False
    if a.min_area_cm2 and (not c.dims.area or c.dims.area <= a.min_area_cm2):
        return False
    if a.size and not c.dims.area:
        return False
    return True


def _score(c: Candidate, a: RecommendInput) -> None:
    """Chỉ xếp hạng giữa các sản phẩm ĐÃ đạt ràng buộc cứng; lý do lấy từ dữ liệu thật (trả trong block)."""
    s = c.summary
    text = f"{s.name} {s.category or ''} {(c.product.description if c.product else '') or ''}"
    if a.category:
        c.reasons.append(f"Đúng loại {a.category}")
    if c.price is not None and a.budget_max is not None:
        c.reasons.append(f"Giá {vnd(c.price)} trong ngân sách {vnd(a.budget_max)}")
    if a.material:
        c.reasons.append(f"Chất liệu {s.material or a.material}")
    if a.color:
        c.reasons.append(f"Có màu {a.color}")
    if a.seats and c.seats is not None:
        c.score += 2 if c.seats <= a.seats + 2 else 1
        c.reasons.append(f"~{c.seats} người" + (" (ước tính theo kích thước)" if c.seats_estimated else ""))
    if a.room:
        if any(_contains(s.name, t) for t in ROOM_AFFINITY.get(fold(a.room), ())):
            c.score += 1.5
    for want in (a.style, a.use_case):
        if want and _contains(text, want):
            c.score += 1


async def _load_details(cands: list[Candidate], ctx: ToolContext) -> None:
    sem = asyncio.Semaphore(6)

    async def load(c: Candidate) -> None:
        async with sem:
            try:
                c.product = await ctx.ports.catalog.get_product(c.summary.id, ctx.principal)
            except errors.NotFound:
                return
        c.dims_text = next((v.dimensions for v in c.product.variants if v.dimensions), None)
        c.dims = parse_dimensions(c.dims_text)
        c.seats, c.seats_estimated = seat_capacity(c.product, c.dims)
        c.colors = [v.color for v in c.product.variants if v.color]

    await asyncio.gather(*(load(c) for c in cands))


MAX_STOCK_CHECKS = 8
LOW_STOCK = 5


async def _with_stock(cands: list[Candidate], args: RecommendInput, ctx: ToolContext) -> list[Candidate]:
    """'… còn hàng': đọc tồn kho THẬT (Backend) cho từng ứng viên theo thứ tự xếp hạng; hết hàng → loại;
    Backend không trả tồn kho (chưa công khai) → giữ nhưng đánh dấu 'unknown' (không khẳng định còn hàng)."""
    out: list[Candidate] = []
    for c in cands[:MAX_STOCK_CHECKS]:
        if len(out) >= args.limit:
            break
        product = c.product or await ctx.ports.catalog.get_product(c.summary.id, ctx.principal)
        totals: list[int] = []
        try:
            for v in product.variants[:3]:
                totals.append((await ctx.ports.inventory.get_inventory(v.id, ctx.principal)).total)
        except errors.CapabilityUnavailable:
            totals = []
        if totals and sum(totals) <= 0:
            continue  # hết hàng thật → không gợi ý
        c.stock = "unknown" if not totals else ("low_stock" if sum(totals) <= LOW_STOCK else "in_stock")
        out.append(c)
    return out


async def recommend_products(args: RecommendInput, ctx: ToolContext) -> ToolResult:
    catalog = ctx.ports.catalog
    try:
        cands = [Candidate(summary=s, price=s.price_from) for s in await _collect(args, ctx) if _matches(s, args)]
        if _needs_detail(args) and cands:
            cands = cands[:MAX_DETAILS]
            await _load_details(cands, ctx)
            cands = [c for c in cands if c.product is not None and _matches_detail(c, args)]
    except errors.PortError as exc:
        return error_result("recommend_products", exc)

    for c in cands:
        _score(c, args)
    budget_ref = args.budget_max
    cands.sort(key=lambda c: (-c.score,
                              (c.price or 1e12) if args.price_pref == "low" else 0,
                              -(c.price or 0) if args.price_pref == "high" else 0,
                              (c.dims.area or 1e9) if (args.size == "compact" or args.max_area_cm2) else 0,
                              -(c.dims.area or 0) if (args.size == "large" or args.min_area_cm2) else 0,
                              # tìm kiếm: liệt kê theo giá tăng dần; tư vấn: ưu tiên gần ngân sách
                              (c.price or 1e12) if args.mode == "search"
                              else abs((budget_ref * 0.85) - (c.price or 0)) if budget_ref else (c.price or 0)))
    if args.distinct_suppliers:
        seen: set[str] = set()
        cands = [c for c in cands if not (c.summary.supplier_name in seen or seen.add(c.summary.supplier_name or c.summary.id))]
    try:
        top = await _with_stock(cands, args, ctx) if args.in_stock else cands[: args.limit]
    except errors.PortError as exc:
        return error_result("recommend_products", exc)
    data = {
        "mode": args.mode, "distinct_suppliers": args.distinct_suppliers,
        "requirements": args.model_dump(exclude_none=True, exclude={"limit", "mode", "exclude_ids", "distinct_suppliers"}),
        "matched": len(cands),
        "unverifiable": ["phong cách (dữ liệu sản phẩm chưa gắn phong cách)"] if args.style else [],
        "items": [{"id": c.summary.id, "name": c.summary.name, "price": c.price, "category": c.summary.category,
                   "supplier": c.summary.supplier_name,
                   "material": c.summary.material, "dimensions": c.dims_text, "area_cm2": c.dims.area,
                   "seats": c.seats, "seats_estimated": c.seats_estimated, "colors": c.colors,
                   "image_url": c.summary.image_url, "reasons": c.reasons,
                   **({"stock": c.stock} if args.in_stock else {})} for c in top],
    }
    return ToolResult(tool="recommend_products", status=ToolStatus.OK if top else ToolStatus.NOT_FOUND, data=data,
                      message=None if top else NO_INFO,
                      sources=[ctx.source("products", Freshness.REALTIME, system=catalog.source_system,
                                          record_id=c.summary.id) for c in top])


ADVISOR_TOOLS = [
    ToolSpec("recommend_products",
             "Tư vấn/tìm sản phẩm theo nhu cầu: loại, ngân sách (VND), số người, kích thước, chất liệu, màu, phòng, phong cách. "
             "Lọc chặt trên catalog thật.",
             RecommendInput, OperationType.SEARCH, "low", "Backend /api/products + /api/products/{id}",
             read_handler=recommend_products),
]
