"""
Tools UPDATE / SENSITIVE_UPDATE / ACTION.

Mỗi tool KHÔNG tự thực thi khi được gọi: `prepare` chỉ đọc trạng thái hiện tại và tạo Proposal
(diff before → after). Việc thực thi chỉ xảy ra trong ActionService sau khi người dùng xác nhận
đúng action + mã xác nhận. Sau thực thi luôn `verify` bằng cách đọc lại từ source of truth.

Quyền (tôn trọng RBAC của Backend thật):
  - Sản phẩm, giá, tồn kho: chỉ SUPPLIER sở hữu (Backend kiểm tra ownership). Admin chỉ đọc (DEC-4).
  - Danh mục, vật liệu: ADMIN (endpoint Backend có sẵn).
  - Thông tin cửa hàng, FAQ, khuyến mãi: ADMIN (Backend chưa có API — GAP B.1/B.6/B.7).
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.domain import errors
from app.domain.actions import ConfirmationLevel, FieldChange, PendingAction
from app.domain.models import OpeningHours, PromotionStatus, SearchCriteria
from app.domain.principal import Role
from app.domain.results import ToolResult, ToolStatus
from app.tools.base import OperationType, Proposal, ToolContext, ToolInput, ToolSpec
from app.tools.common import SKU_PATTERN, ProductRef, pick_variant, resolve_product
from app.tools.read_tools import match_named

VN_TZ = timezone(timedelta(hours=7))
PHONE_RE = re.compile(r"^(\+?84|0)[0-9 .\-]{8,13}$|^1[89]00[0-9 .]{4,8}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def fmt_vnd(value: float | None) -> str:
    return "không rõ" if value is None else f"{value:,.0f}đ".replace(",", ".")


# =====================================================================
# Giá sản phẩm
# =====================================================================
class UpdatePriceInput(ToolInput):
    sku: str = Field(pattern=SKU_PATTERN)
    new_price: float = Field(gt=0, le=10_000_000_000)
    reason: str | None = Field(default=None, max_length=300)


class UpdatePriceHandler:
    async def prepare(self, args: UpdatePriceInput, ctx: ToolContext) -> Proposal | ToolResult:
        product = await resolve_product("update_product_price", ProductRef(sku=args.sku), ctx)
        if isinstance(product, ToolResult):
            return product
        variant = pick_variant(product, args.sku)
        if variant is None:
            return ToolResult(tool="update_product_price", status=ToolStatus.NEEDS_INPUT,
                              message=f"{product.name} có nhiều phiên bản, bạn muốn đổi giá phiên bản nào?",
                              data={"candidates": [{"name": f"{v.sku or v.id} ({v.dimensions or ''} {v.color or ''})".strip(), "price_from": v.price} for v in product.variants]})
        if variant.price is not None and abs(variant.price - args.new_price) < 0.5:
            return ToolResult(tool="update_product_price", status=ToolStatus.INVALID, message="Giá mới trùng với giá hiện tại.")
        warnings = []
        if variant.price:
            ratio = abs(args.new_price - variant.price) / variant.price
            if ratio >= ctx.settings.PRICE_CHANGE_STRONG_RATIO:
                warnings.append(f"Giá thay đổi {ratio:.0%} so với hiện tại — vui lòng kiểm tra kỹ.")
        return Proposal(
            target_type="product_variant", target_id=variant.id, target_label=f"{product.name} ({args.sku})",
            summary=f"Đổi giá {product.name} ({args.sku}): {fmt_vnd(variant.price)} → {fmt_vnd(args.new_price)}",
            changes=[FieldChange(field="price", label="Giá", before=variant.price, after=args.new_price)],
            params={"product_id": product.id, "variant_id": variant.id, "sku": args.sku, "new_price": args.new_price},
            snapshot={"price": variant.price}, warnings=warnings,
        )

    async def _current_price(self, action: PendingAction, ctx: ToolContext) -> float | None:
        product = await ctx.ports.catalog.get_product(action.params["product_id"], ctx.principal)
        variant = next((v for v in product.variants if v.id == action.params["variant_id"]), None)
        if variant is None:
            raise errors.NotFound("Biến thể không còn tồn tại.")
        return variant.price

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        if await self._current_price(action, ctx) != action.snapshot["price"]:
            raise errors.Conflict("Giá đã bị thay đổi từ lúc đề xuất. Vui lòng tạo yêu cầu mới.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        product = await ctx.ports.catalog.get_product(action.params["product_id"], ctx.principal)
        await ctx.ports.catalog.update_variant_price(product, action.params["variant_id"], action.params["new_price"], ctx.principal)
        return {"product_id": product.id, "variant_id": action.params["variant_id"]}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        current = await self._current_price(action, ctx)
        return current is not None and abs(current - action.params["new_price"]) < 0.5


# =====================================================================
# Mô tả sản phẩm
# =====================================================================
class UpdateDescriptionInput(ProductRef):
    description: str = Field(min_length=5, max_length=5000)


class UpdateDescriptionHandler:
    async def prepare(self, args: UpdateDescriptionInput, ctx: ToolContext) -> Proposal | ToolResult:
        product = await resolve_product("update_product_description", ProductRef(sku=args.sku, product_id=args.product_id, name=args.name), ctx)
        if isinstance(product, ToolResult):
            return product
        if (product.description or "").strip() == args.description.strip():
            return ToolResult(tool="update_product_description", status=ToolStatus.INVALID, message="Mô tả mới trùng mô tả hiện tại.")
        return Proposal(
            target_type="product", target_id=product.id, target_label=product.name,
            summary=f"Cập nhật mô tả sản phẩm {product.name}",
            changes=[FieldChange(field="description", label="Mô tả", before=product.description, after=args.description)],
            params={"product_id": product.id, "description": args.description},
            snapshot={"description": product.description},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        product = await ctx.ports.catalog.get_product(action.params["product_id"], ctx.principal)
        if product.description != action.snapshot["description"]:
            raise errors.Conflict("Mô tả đã bị thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        product = await ctx.ports.catalog.get_product(action.params["product_id"], ctx.principal)
        await ctx.ports.catalog.update_product_description(product, action.params["description"], ctx.principal)
        return {"product_id": product.id}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        product = await ctx.ports.catalog.get_product(action.params["product_id"], ctx.principal)
        return (product.description or "").strip() == action.params["description"].strip()


# =====================================================================
# Tồn kho
# =====================================================================
class AdjustInventoryInput(ToolInput):
    sku: str = Field(pattern=SKU_PATTERN)
    store_id: str | None = Field(default=None, max_length=64)
    delta: int
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("delta")
    @classmethod
    def _non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Số lượng điều chỉnh phải khác 0.")
        return v


class AdjustInventoryHandler:
    async def _store_qty(self, variant_id: str, store_id: str, ctx: ToolContext) -> int:
        inv = await ctx.ports.inventory.get_inventory(variant_id, ctx.principal)
        row = next((s for s in inv.by_store if s.store_id == store_id), None)
        if row is None:
            raise errors.NotFound("Biến thể chưa có trong kho này hoặc bạn không có quyền với kho này.")
        return row.quantity

    async def prepare(self, args: AdjustInventoryInput, ctx: ToolContext) -> Proposal | ToolResult:
        if abs(args.delta) > ctx.settings.MAX_INVENTORY_DELTA:
            return ToolResult(tool="adjust_inventory", status=ToolStatus.INVALID,
                              message=f"Mỗi lần chỉ điều chỉnh tối đa {ctx.settings.MAX_INVENTORY_DELTA} đơn vị.")
        product = await resolve_product("adjust_inventory", ProductRef(sku=args.sku), ctx)
        if isinstance(product, ToolResult):
            return product
        variant = pick_variant(product, args.sku)
        if variant is None:
            return ToolResult(tool="adjust_inventory", status=ToolStatus.NEEDS_INPUT,
                              message=f"{product.name} có nhiều phiên bản, bạn muốn điều chỉnh phiên bản nào?")
        inv = await ctx.ports.inventory.get_inventory(variant.id, ctx.principal)
        store_id = args.store_id
        if store_id is None:
            if len(inv.by_store) != 1:
                return ToolResult(tool="adjust_inventory", status=ToolStatus.NEEDS_INPUT,
                                  message="Bạn muốn điều chỉnh tồn kho ở kho nào? Vui lòng cho biết mã kho.",
                                  data={"stores": [s.model_dump() for s in inv.by_store]})
            store_id = inv.by_store[0].store_id
        before = await self._store_qty(variant.id, store_id, ctx)
        after = before + args.delta
        if after < 0:
            return ToolResult(tool="adjust_inventory", status=ToolStatus.INVALID, message="Tồn kho sau điều chỉnh không được âm.")
        return Proposal(
            target_type="inventory", target_id=f"{store_id}:{variant.id}", target_label=f"{product.name} ({args.sku}) tại kho {store_id}",
            summary=f"Điều chỉnh tồn kho {args.sku} tại kho {store_id}: {before} → {after} ({args.delta:+d})",
            changes=[FieldChange(field="stock_quantity", label="Tồn kho", before=before, after=after)],
            params={"variant_id": variant.id, "store_id": store_id, "delta": args.delta, "expected_after": after},
            snapshot={"quantity": before},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        if await self._store_qty(action.params["variant_id"], action.params["store_id"], ctx) != action.snapshot["quantity"]:
            raise errors.Conflict("Tồn kho đã thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        p = action.params
        await ctx.ports.inventory.adjust_inventory(p["store_id"], p["variant_id"], p["delta"], ctx.principal)
        return {"store_id": p["store_id"], "variant_id": p["variant_id"]}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        return await self._store_qty(action.params["variant_id"], action.params["store_id"], ctx) == action.params["expected_after"]


# =====================================================================
# Thông tin cửa hàng
# =====================================================================
class UpdateStoreInfoInput(ToolInput):
    hotline: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, min_length=5, max_length=300)
    opening_hours: list[OpeningHours] | None = Field(default=None, max_length=7)

    @field_validator("hotline")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is not None and not PHONE_RE.match(v.strip()):
            raise ValueError("Số hotline không hợp lệ.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is not None and not EMAIL_RE.match(v.strip()):
            raise ValueError("Email không hợp lệ.")
        return v

    @field_validator("opening_hours")
    @classmethod
    def _hours(cls, v: list[OpeningHours] | None) -> list[OpeningHours] | None:
        for h in v or []:
            for t in (h.open, h.close):
                if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
                    raise ValueError("Giờ phải có dạng HH:MM.")
            if h.open >= h.close:
                raise ValueError("Giờ mở cửa phải trước giờ đóng cửa.")
        return v

    @model_validator(mode="after")
    def _any(self) -> "UpdateStoreInfoInput":
        if not any([self.hotline, self.email, self.address, self.opening_hours]):
            raise ValueError("Cần ít nhất một thông tin để cập nhật.")
        return self


class UpdateStoreInfoHandler:
    LABELS = {"hotline": "Hotline", "email": "Email", "address": "Địa chỉ", "opening_hours": "Giờ mở cửa"}

    async def prepare(self, args: UpdateStoreInfoInput, ctx: ToolContext) -> Proposal | ToolResult:
        info = await ctx.ports.store.get_store_info(ctx.principal)
        current = info.model_dump(mode="json")
        new = args.model_dump(mode="json", exclude_none=True)
        changes = [FieldChange(field=k, label=self.LABELS[k], before=current.get(k), after=v) for k, v in new.items() if current.get(k) != v]
        if not changes:
            return ToolResult(tool="update_store_info", status=ToolStatus.INVALID, message="Thông tin mới trùng với hiện tại.")
        return Proposal(
            target_type="store_info", target_id="store", target_label=info.name,
            summary="Cập nhật thông tin cửa hàng: " + ", ".join(c.label for c in changes),
            changes=changes, params={"changes": {c.field: c.after for c in changes}, "expected_version": info.version},
            snapshot={"version": info.version},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        info = await ctx.ports.store.get_store_info(ctx.principal)
        if info.version != action.snapshot["version"]:
            raise errors.Conflict("Thông tin cửa hàng đã bị thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        info = await ctx.ports.store.update_store_info(action.params["changes"], action.params["expected_version"], ctx.principal)
        return {"version": info.version}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        info = (await ctx.ports.store.get_store_info(ctx.principal)).model_dump(mode="json")
        return all(info.get(k) == v for k, v in action.params["changes"].items())


# =====================================================================
# FAQ
# =====================================================================
class UpsertFaqInput(ToolInput):
    document_id: str | None = Field(default=None, max_length=64)
    question: str = Field(min_length=5, max_length=300)
    answer: str = Field(min_length=5, max_length=4000)


class UpsertFaqHandler:
    async def prepare(self, args: UpsertFaqInput, ctx: ToolContext) -> Proposal | ToolResult:
        before_q = before_a = None
        version = None
        if args.document_id:
            doc = await ctx.ports.knowledge.get_document(args.document_id, ctx.principal)
            if doc.kind.value != "faq":
                return ToolResult(tool="upsert_faq", status=ToolStatus.INVALID, message="Tài liệu này không phải FAQ.")
            before_q, before_a, version = doc.title, doc.content, doc.version
        return Proposal(
            target_type="faq", target_id=args.document_id or "new", target_label=args.question,
            summary=("Cập nhật" if args.document_id else "Thêm") + f" FAQ: {args.question}",
            changes=[FieldChange(field="question", label="Câu hỏi", before=before_q, after=args.question),
                     FieldChange(field="answer", label="Trả lời", before=before_a, after=args.answer)],
            params={"document_id": args.document_id, "question": args.question, "answer": args.answer, "expected_version": version},
            snapshot={"version": version},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        if action.params["document_id"]:
            doc = await ctx.ports.knowledge.get_document(action.params["document_id"], ctx.principal)
            if doc.version != action.snapshot["version"]:
                raise errors.Conflict("FAQ đã bị thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        p = action.params
        doc = await ctx.ports.knowledge.upsert_faq(p["document_id"], p["question"], p["answer"], p["expected_version"], ctx.principal)
        return {"document_id": doc.id, "version": doc.version}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        doc = await ctx.ports.knowledge.get_document(result["document_id"], ctx.principal)
        return doc.title == action.params["question"] and doc.content == action.params["answer"]


# =====================================================================
# Khuyến mãi
# =====================================================================
class CreatePromotionInput(ToolInput):
    name: str = Field(min_length=3, max_length=120)
    type: Literal["percentage", "fixed_amount", "free_shipping"] = "percentage"
    value: float = Field(ge=0)
    categories: list[str] = Field(default_factory=list, max_length=10)
    all_products: bool = False
    starts_on: date
    ends_on: date
    code: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{3,30}$")
    max_discount: float | None = Field(default=None, gt=0)
    min_order_value: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "CreatePromotionInput":
        if self.ends_on <= self.starts_on:
            raise ValueError("Ngày kết thúc phải sau ngày bắt đầu.")
        if not self.all_products and not self.categories:
            raise ValueError("Cần chọn danh mục áp dụng hoặc áp dụng toàn bộ sản phẩm.")
        if self.type != "free_shipping" and self.value <= 0:
            raise ValueError("Giá trị giảm phải lớn hơn 0.")
        return self


class CreatePromotionHandler:
    async def prepare(self, args: CreatePromotionInput, ctx: ToolContext) -> Proposal | ToolResult:
        s = ctx.settings
        if args.type == "percentage" and args.value > s.MAX_PROMOTION_PERCENT:
            return ToolResult(tool="create_promotion", status=ToolStatus.INVALID,
                              message=f"Mức giảm tối đa cho phép là {s.MAX_PROMOTION_PERCENT:g}%.")
        if args.starts_on < datetime.now(VN_TZ).date():
            return ToolResult(tool="create_promotion", status=ToolStatus.INVALID, message="Ngày bắt đầu không được ở quá khứ.")
        if len(args.categories) > s.MAX_PROMOTION_SCOPE_CATEGORIES:
            return ToolResult(tool="create_promotion", status=ToolStatus.INVALID,
                              message=f"Tối đa {s.MAX_PROMOTION_SCOPE_CATEGORIES} danh mục mỗi chương trình.")
        all_cats = await ctx.ports.catalog.list_categories(ctx.principal)
        resolved, missing = [], []
        for name in args.categories:
            m = match_named(all_cats, name)
            (resolved.append(m) if m else missing.append(name))
        if missing:
            return ToolResult(tool="create_promotion", status=ToolStatus.NOT_FOUND,
                              message="Không tìm thấy danh mục: " + ", ".join(missing))
        affected = 0
        for c in resolved:
            affected += (await ctx.ports.catalog.search_products(SearchCriteria(category_id=c.id, size=1), ctx.principal)).total
        if args.all_products:
            affected = (await ctx.ports.catalog.search_products(SearchCriteria(size=1), ctx.principal)).total
        warnings = []
        if args.all_products:
            warnings.append("Áp dụng cho TOÀN BỘ sản phẩm.")
        if affected == 0:
            warnings.append("Hiện không có sản phẩm đang bán trong phạm vi này.")
        value_label = {"percentage": f"{args.value:g}%", "fixed_amount": fmt_vnd(args.value), "free_shipping": "Miễn phí vận chuyển"}[args.type]
        scope_label = "Toàn bộ sản phẩm" if args.all_products else ", ".join(c.name for c in resolved)
        draft = {
            "name": args.name, "type": args.type, "value": args.value, "max_discount": args.max_discount, "code": args.code,
            "scope": {"all_products": args.all_products, "category_ids": [c.id for c in resolved], "product_ids": []},
            "starts_at": datetime.combine(args.starts_on, time(0, 0), VN_TZ).isoformat(),
            "ends_at": datetime.combine(args.ends_on, time(23, 59, 59), VN_TZ).isoformat(),
            "min_order_value": args.min_order_value,
        }
        return Proposal(
            target_type="promotion", target_id="new", target_label=args.name,
            summary=f"Tạo khuyến mãi '{args.name}': giảm {value_label} cho {scope_label} "
                    f"từ {args.starts_on:%d/%m/%Y} đến {args.ends_on:%d/%m/%Y} (ảnh hưởng ~{affected} sản phẩm)",
            changes=[FieldChange(field="value", label="Mức giảm", after=value_label),
                     FieldChange(field="scope", label="Phạm vi", after=scope_label),
                     FieldChange(field="period", label="Thời gian", after=f"{args.starts_on:%d/%m/%Y} – {args.ends_on:%d/%m/%Y}")],
            params={"draft": draft}, warnings=warnings, escalate_to_strong=True,
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        return None

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        promo = await ctx.ports.promotions.create_promotion(action.params["draft"], ctx.principal, idempotency_key=action.id)
        return {"promotion_id": promo.id}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        promo = await ctx.ports.promotions.get_promotion(result["promotion_id"], ctx.principal)
        d = action.params["draft"]
        return promo.name == d["name"] and abs(promo.value - d["value"]) < 1e-6


class SetPromotionStatusInput(ToolInput):
    promotion: str = Field(min_length=2, max_length=64, description="id hoặc mã khuyến mãi")
    status: Literal["active", "paused", "ended"]


_PROMO_TRANSITIONS = {
    PromotionStatus.DRAFT: {"active", "ended"}, PromotionStatus.SCHEDULED: {"active", "paused", "ended"},
    PromotionStatus.ACTIVE: {"paused", "ended"}, PromotionStatus.PAUSED: {"active", "ended"}, PromotionStatus.ENDED: set(),
}


class SetPromotionStatusHandler:
    async def prepare(self, args: SetPromotionStatusInput, ctx: ToolContext) -> Proposal | ToolResult:
        promo = await ctx.ports.promotions.get_promotion(args.promotion, ctx.principal)
        if args.status not in _PROMO_TRANSITIONS[promo.status]:
            return ToolResult(tool="set_promotion_status", status=ToolStatus.INVALID,
                              message=f"Không thể chuyển khuyến mãi từ '{promo.status.value}' sang '{args.status}'.")
        return Proposal(
            target_type="promotion", target_id=promo.id, target_label=promo.name,
            summary=f"Đổi trạng thái khuyến mãi '{promo.name}': {promo.status.value} → {args.status}",
            changes=[FieldChange(field="status", label="Trạng thái", before=promo.status.value, after=args.status)],
            params={"promotion_id": promo.id, "status": args.status, "expected_version": promo.version},
            snapshot={"version": promo.version},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        promo = await ctx.ports.promotions.get_promotion(action.params["promotion_id"], ctx.principal)
        if promo.version != action.snapshot["version"]:
            raise errors.Conflict("Khuyến mãi đã bị thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        p = action.params
        promo = await ctx.ports.promotions.set_promotion_status(p["promotion_id"], PromotionStatus(p["status"]),
                                                                p["expected_version"], ctx.principal)
        return {"promotion_id": promo.id}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        promo = await ctx.ports.promotions.get_promotion(action.params["promotion_id"], ctx.principal)
        return promo.status.value == action.params["status"]


# =====================================================================
# Danh mục / vật liệu (endpoint admin Backend có sẵn)
# =====================================================================
class UpsertTaxonomyInput(ToolInput):
    name: str = Field(min_length=2, max_length=100)
    rename_from: str | None = Field(default=None, min_length=2, max_length=100)
    parent: str | None = Field(default=None, max_length=100)


class _TaxonomyHandler:
    kind: str
    label: str

    async def _items(self, ctx: ToolContext):
        raise NotImplementedError

    async def _write(self, item_id: str | None, args: dict[str, Any], ctx: ToolContext):
        raise NotImplementedError

    async def prepare(self, args: UpsertTaxonomyInput, ctx: ToolContext) -> Proposal | ToolResult:
        items = await self._items(ctx)
        target = None
        if args.rename_from:
            target = match_named(items, args.rename_from)
            if target is None:
                return ToolResult(tool=f"upsert_{self.kind}", status=ToolStatus.NOT_FOUND, message=f"Không tìm thấy {self.label} '{args.rename_from}'.")
        dup = match_named(items, args.name)
        if dup and (target is None or dup.id != target.id) and dup.name.lower() == args.name.lower():
            return ToolResult(tool=f"upsert_{self.kind}", status=ToolStatus.INVALID, message=f"{self.label.capitalize()} '{args.name}' đã tồn tại.")
        parent_id = None
        if args.parent:
            parent = match_named(items, args.parent)
            if parent is None:
                return ToolResult(tool=f"upsert_{self.kind}", status=ToolStatus.NOT_FOUND, message=f"Không tìm thấy {self.label} cha '{args.parent}'.")
            parent_id = parent.id
        return Proposal(
            target_type=self.kind, target_id=target.id if target else "new", target_label=args.name,
            summary=(f"Đổi tên {self.label} '{target.name}' → '{args.name}'" if target else f"Tạo {self.label} '{args.name}'"),
            changes=[FieldChange(field="name", label="Tên", before=target.name if target else None, after=args.name)],
            params={"id": target.id if target else None, "name": args.name, "parent_id": parent_id},
            snapshot={"name": target.name if target else None},
        )

    async def check_fresh(self, action: PendingAction, ctx: ToolContext) -> None:
        if action.params["id"]:
            cur = next((i for i in await self._items(ctx) if i.id == action.params["id"]), None)
            if cur is None or cur.name != action.snapshot["name"]:
                raise errors.Conflict(f"{self.label.capitalize()} đã bị thay đổi từ lúc đề xuất.")

    async def execute(self, action: PendingAction, ctx: ToolContext) -> dict[str, Any]:
        ref = await self._write(action.params["id"], action.params, ctx)
        return {"id": ref.id}

    async def verify(self, action: PendingAction, result: dict[str, Any], ctx: ToolContext) -> bool:
        cur = next((i for i in await self._items(ctx) if i.id == result["id"]), None)
        return cur is not None and cur.name == action.params["name"]


class UpsertCategoryHandler(_TaxonomyHandler):
    kind, label = "category", "danh mục"

    async def _items(self, ctx: ToolContext):
        return await ctx.ports.catalog.list_categories(ctx.principal)

    async def _write(self, item_id, args, ctx):
        return await ctx.ports.catalog.upsert_category(item_id, args["name"], args.get("parent_id"), ctx.principal)


class UpsertMaterialHandler(_TaxonomyHandler):
    kind, label = "material", "vật liệu"

    async def _items(self, ctx: ToolContext):
        return await ctx.ports.catalog.list_materials(ctx.principal)

    async def _write(self, item_id, args, ctx):
        return await ctx.ports.catalog.upsert_material(item_id, args["name"], ctx.principal)


SUPPLIER = frozenset({Role.SUPPLIER})
ADMIN = frozenset({Role.ADMIN})
STD, STRONG = ConfirmationLevel.STANDARD, ConfirmationLevel.STRONG

MUTATION_TOOLS: list[ToolSpec] = [
    ToolSpec("update_product_price", "Đổi giá một biến thể sản phẩm theo SKU (chỉ nhà cung cấp sở hữu).", UpdatePriceInput,
             OperationType.SENSITIVE_UPDATE, SUPPLIER, "high", "Backend PUT /api/variants/{id}",
             mutation=UpdatePriceHandler(), confirmation=STRONG, requires_auth=True),
    ToolSpec("update_product_description", "Cập nhật mô tả sản phẩm (chỉ nhà cung cấp sở hữu).", UpdateDescriptionInput,
             OperationType.UPDATE, SUPPLIER, "medium", "Backend PUT /api/products/{id}",
             mutation=UpdateDescriptionHandler(), confirmation=STD, requires_auth=True),
    ToolSpec("adjust_inventory", "Nhập/xuất tồn kho theo số lượng chênh lệch tại một kho (chỉ nhà cung cấp sở hữu).",
             AdjustInventoryInput, OperationType.SENSITIVE_UPDATE, SUPPLIER, "high",
             "Backend PATCH /api/stores/{storeId}/inventory/{variantId}",
             mutation=AdjustInventoryHandler(), confirmation=STRONG, requires_auth=True),
    ToolSpec("update_store_info", "Cập nhật hotline, email, địa chỉ, giờ mở cửa của WoodHub (admin).", UpdateStoreInfoInput,
             OperationType.SENSITIVE_UPDATE, ADMIN, "high", "Backend (GAP B.1)",
             mutation=UpdateStoreInfoHandler(), confirmation=STRONG, requires_auth=True),
    ToolSpec("upsert_faq", "Thêm hoặc sửa một câu hỏi thường gặp (admin).", UpsertFaqInput,
             OperationType.UPDATE, ADMIN, "medium", "Backend (GAP B.7)",
             mutation=UpsertFaqHandler(), confirmation=STD, requires_auth=True),
    ToolSpec("create_promotion", "Tạo chương trình khuyến mãi mới cho danh mục hoặc toàn bộ sản phẩm (admin).",
             CreatePromotionInput, OperationType.ACTION, ADMIN, "high", "Backend (GAP B.6)",
             mutation=CreatePromotionHandler(), confirmation=STRONG, requires_auth=True),
    ToolSpec("set_promotion_status", "Kích hoạt, tạm dừng hoặc kết thúc khuyến mãi (admin).", SetPromotionStatusInput,
             OperationType.SENSITIVE_UPDATE, ADMIN, "high", "Backend (GAP B.6)",
             mutation=SetPromotionStatusHandler(), confirmation=STRONG, requires_auth=True),
    ToolSpec("upsert_category", "Tạo hoặc đổi tên danh mục sản phẩm (admin).", UpsertTaxonomyInput,
             OperationType.UPDATE, ADMIN, "medium", "Backend POST/PUT /api/categories",
             mutation=UpsertCategoryHandler(), confirmation=STD, requires_auth=True),
    ToolSpec("upsert_material", "Tạo hoặc đổi tên vật liệu (admin).", UpsertTaxonomyInput,
             OperationType.UPDATE, ADMIN, "medium", "Backend POST/PUT /api/materials",
             mutation=UpsertMaterialHandler(), confirmation=STD, requires_auth=True),
]
