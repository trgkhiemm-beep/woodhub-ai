"""Tiện ích dùng chung cho tools: map lỗi port → ToolResult, phân giải sản phẩm."""
from __future__ import annotations

import logging
import re

from pydantic import Field, model_validator

from app.domain import errors
from app.request_context import current_request_id
from app.domain.messages import BACKEND_DENIED, NO_INFO, PRODUCT_API_ERROR, PRODUCT_TOOLS, SYSTEM_ERROR, UPSTREAM_BUSY
from app.domain.models import Product, SearchCriteria, Variant
from app.domain.results import ToolResult, ToolStatus
from app.nlp.vietnamese import restore_diacritics_for_search
from app.tools.base import ToolContext, ToolInput

logger = logging.getLogger("woodhub.tools")

SKU_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-]{1,49}$"


def error_result(tool: str, exc: errors.PortError) -> ToolResult:
    """Không lộ chi tiết kỹ thuật cho người dùng; chi tiết chỉ vào log."""
    logger.warning("tool=%s port_error=%s request_id=%s detail=%s", tool, exc.code, current_request_id.get(),
                   (exc.detail or "")[:300])
    product = tool in PRODUCT_TOOLS
    if isinstance(exc, errors.NotFound):
        return ToolResult(tool=tool, status=ToolStatus.NOT_FOUND, message=NO_INFO if product else exc.message,
                          error_code=exc.code)
    if isinstance(exc, errors.CapabilityUnavailable):
        return ToolResult(tool=tool, status=ToolStatus.UNKNOWN, message=exc.message, error_code=exc.code)
    if isinstance(exc, (errors.Forbidden, errors.Unauthenticated)):
        # Backend từ chối (token hết hạn/không đủ quyền): câu dễ hiểu cho khách, chi tiết chỉ nằm trong log
        return ToolResult(tool=tool, status=ToolStatus.DENIED, message=BACKEND_DENIED, error_code=exc.code)
    if isinstance(exc, errors.RateLimited):
        logger.warning("rate_limited layer=backend tool=%s request_id=%s", tool, current_request_id.get())
        return ToolResult(tool=tool, status=ToolStatus.ERROR, message=UPSTREAM_BUSY, error_code="UPSTREAM_RATE_LIMITED")
    if isinstance(exc, (errors.ValidationFailed, errors.Conflict)):
        return ToolResult(tool=tool, status=ToolStatus.INVALID, message=exc.message, error_code=exc.code)
    return ToolResult(tool=tool, status=ToolStatus.ERROR, message=PRODUCT_API_ERROR if product else SYSTEM_ERROR,
                      error_code=exc.code)


class ProductRef(ToolInput):
    """Tham chiếu sản phẩm: đúng một trong sku / product_id / name (hoặc để trống để dùng sản phẩm vừa nhắc tới)."""
    sku: str | None = Field(default=None, pattern=SKU_PATTERN, description="SKU biến thể hoặc mã model trong tên (vd KTV01).")
    product_id: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, min_length=2, max_length=120)

    @model_validator(mode="after")
    def _at_most_one(self) -> "ProductRef":
        if sum(x is not None for x in (self.sku, self.product_id, self.name)) > 1:
            raise ValueError("Chỉ cung cấp một trong sku, product_id hoặc name.")
        return self


def pick_variant(product: Product, code: str | None) -> Variant | None:
    """Biến thể theo SKU; nếu mã là mã model trong tên (dữ liệu thật: phần lớn variant chưa có SKU)
    và sản phẩm chỉ có một biến thể thì dùng biến thể đó."""
    if code:
        v = product.variant_by_sku(code)
        if v is not None:
            return v
    return product.variants[0] if len(product.variants) == 1 else None


async def _find_by_code(code: str, ctx: ToolContext) -> Product:
    """Mã người dùng đưa có thể là SKU hoặc mã model nằm trong tên (vd 'KTV01' trong 'Kệ Tivi Gỗ KTV01')."""
    catalog = ctx.ports.catalog
    page = await catalog.search_products(SearchCriteria(keyword=code, size=5), ctx.principal)
    named = [p for p in page.items if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", p.name, re.IGNORECASE)]
    if len(named) == 1:  # mã model trong tên (trường hợp phổ biến) → chỉ 1 lần đọc chi tiết
        return await catalog.get_product(named[0].id, ctx.principal)
    for summary in page.items:
        product = await catalog.get_product(summary.id, ctx.principal)
        if product.variant_by_sku(code):
            return product
    return await catalog.find_product_by_sku(code, ctx.principal)


async def resolve_product(tool: str, ref: ProductRef, ctx: ToolContext) -> Product | ToolResult:
    catalog = ctx.ports.catalog
    try:
        if ref.sku:
            return await _find_by_code(ref.sku, ctx)
        if ref.product_id:
            return await catalog.get_product(ref.product_id, ctx.principal)
        if ref.name:
            page = await catalog.search_products(
                SearchCriteria(keyword=restore_diacritics_for_search(ref.name), size=5), ctx.principal)
            if not page.items:
                return ToolResult(tool=tool, status=ToolStatus.NOT_FOUND, message=NO_INFO)
            if len(page.items) > 1:
                return ToolResult(tool=tool, status=ToolStatus.NEEDS_INPUT,
                                  message="Có nhiều sản phẩm phù hợp, bạn muốn hỏi sản phẩm nào?",
                                  data={"candidates": [p.model_dump() for p in page.items]})
            return await catalog.get_product(page.items[0].id, ctx.principal)
        if ctx.conversation.last_product_id:
            return await catalog.get_product(ctx.conversation.last_product_id, ctx.principal)
    except errors.PortError as exc:
        return error_result(tool, exc)
    return ToolResult(tool=tool, status=ToolStatus.NEEDS_INPUT,
                      message="Bạn cho mình biết mã SKU hoặc tên sản phẩm cụ thể nhé.")


def remember_product(ctx: ToolContext, product: Product, sku: str | None = None) -> None:
    ctx.conversation.last_product_id = product.id
    ctx.conversation.last_product_sku = sku or next((v.sku for v in product.variants if v.sku), None)
