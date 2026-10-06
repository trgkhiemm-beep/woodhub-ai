"""
Planner hiểu ngữ cảnh: IntentFrame + DialogueState → bước thực thi (tool call) hoặc câu hỏi làm rõ.

Quy tắc làm rõ: không đoán khi thiếu thông tin QUAN TRỌNG (sản phẩm nào? loại nội thất nào?),
hỏi nhu cầu (ngân sách/số người/kích thước) tối đa MỘT lần cho mỗi loại, rồi tư vấn với những gì đã có.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.agent.dialogue import DialogueState, ShownProduct
from app.domain.messages import NO_INFO, OUT_OF_SCOPE, READ_ONLY
from app.nlu.schema import Entities, Intent, IntentFrame

POLICY_LABELS = {"shipping": "giao hàng", "return": "đổi trả", "warranty": "bảo hành", "payment": "thanh toán",
                 "terms": "điều khoản", "privacy": "bảo mật"}
REPLIES = {
    Intent.GREETING: "Chào bạn! Bạn cần tìm sản phẩm, xem giá hay thông tin nhà cung cấp?",
    Intent.OUT_OF_SCOPE: OUT_OF_SCOPE,
    Intent.CART: "Trợ lý chưa hỗ trợ giỏ hàng. Bạn dùng giỏ hàng trên website nhé.",
    Intent.CHANGE_REQUEST: READ_ONLY,
    Intent.PROMOTION: NO_INFO,   # Backend/Supabase chưa có dữ liệu khuyến mãi; giá do nhà cung cấp tự quản lý
}
CLARIFY_GENERIC = "Bạn cần tìm sản phẩm nào (tên, mã hoặc loại nội thất)?"
CATEGORY_QUESTION = "Bạn cần loại nội thất nào (bàn, ghế, tủ, giường, kệ…) và ngân sách khoảng bao nhiêu?"


@dataclass
class Step:
    kind: Literal["tool", "reply", "clarify"]
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    message: str | None = None
    reference: ShownProduct | None = None       # sản phẩm tham chiếu cho "rẻ hơn/nhỏ hơn"
    needs_reference_detail: bool = False        # cần đọc chi tiết sản phẩm tham chiếu trước
    note: str | None = None


def _product_ref(e: Entities, state: DialogueState) -> tuple[dict[str, Any] | None, str | None]:
    """(args tham chiếu sản phẩm, câu hỏi làm rõ nếu không xác định được)."""
    if e.product_codes:
        return {"sku": e.product_codes[0]}, None
    if e.ordinal:
        p = state.by_ordinal(e.ordinal)
        if p:
            return {"product_id": p.id}, None
        return None, (f"Danh sách vừa rồi chỉ có {len(state.shown)} mẫu. Bạn muốn hỏi mẫu nào?" if state.shown
                      else "Bạn đang hỏi mẫu nào? Cho mình tên hoặc mã sản phẩm nhé.")
    if e.reference == "current" or not (e.category or e.product_name):
        cur = state.current()
        if cur:
            return {"product_id": cur.id}, None
        if state.shown:
            names = "; ".join(f"{i}) {p.name}" for i, p in enumerate(state.shown[:5], 1))
            return None, f"Bạn đang hỏi mẫu nào trong các mẫu vừa rồi? {names}"
        return None, "Bạn đang hỏi sản phẩm nào? Cho mình tên hoặc mã sản phẩm nhé."
    return None, None


def _supplier_ref(e: Entities, state: DialogueState) -> dict[str, Any]:
    """Nhà cung cấp đang được nói tới: tên nêu rõ > mã sản phẩm > sản phẩm trong ngữ cảnh. Rỗng → tool hỏi lại."""
    if e.supplier_name:
        return {"supplier_name": e.supplier_name}
    if e.product_codes:
        return {"sku": e.product_codes[0]}
    if e.ordinal and state.by_ordinal(e.ordinal):
        return {"product_id": state.by_ordinal(e.ordinal).id}
    cur = state.current()
    return {"product_id": cur.id} if cur else {}


def _diversity(e: Entities) -> dict[str, Any]:
    return {"distinct_suppliers": True} if e.distinct_suppliers else {}


def _recommend_args(c: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in c.items() if v not in (None, "", [])}


class Planner:
    def plan(self, frame: IntentFrame, state: DialogueState) -> Step:
        i, e = frame.intent, frame.entities

        if i in REPLIES:
            return Step("reply", message=REPLIES[i])

        # Trả lời cho câu hỏi làm rõ đang chờ (vd trợ lý hỏi ngân sách, người dùng đáp "10 triệu")
        answering = state.pending == "recommend" or (bool(state.constraints) and not e.category and not e.relative)
        if answering and i in (Intent.UNCLEAR, Intent.PRODUCT_SEARCH, Intent.RECOMMEND, Intent.PRODUCT_DETAIL) \
                and not (e.product_codes or e.ordinal or e.reference) \
                and (e.amounts or e.seats or e.size or e.category or e.material or e.color or e.room):
            return self._recommend(e, state, continuation=True)

        if i == Intent.SUPPLIER_INFO:
            return Step("tool", tool="get_supplier_info", args={**_supplier_ref(e, state), **(
                {"fields": [f for f in e.store_fields if f in ("hotline", "email", "address", "opening_hours")]}
                if e.store_fields else {})})
        if i == Intent.BRANCHES:
            return Step("tool", tool="list_branches", args={"city": e.city} if e.city else {})
        if i == Intent.POLICY:
            # Chính sách giao hàng/đổi trả/bảo hành thuộc từng NHÀ CUNG CẤP; Backend chưa có dữ liệu chính sách.
            ref = _supplier_ref(e, state)
            topic = e.policy_type if e.policy_type in POLICY_LABELS else None
            if ref:
                return Step("tool", tool="get_supplier_info", args={**ref, **({"topic": topic} if topic else {})})
            label = f" {POLICY_LABELS[topic]}" if topic else ""
            return Step("reply", message=f"Hiện chưa có thông tin đã xác minh về chính sách{label}. "
                                         "Chính sách do từng nhà cung cấp quy định — bạn đang hỏi về sản phẩm hoặc nhà cung cấp nào?")
        if i == Intent.ORDER_STATUS:
            return Step("tool", tool="get_order_status", args={"order_id": e.task_id} if e.task_id else {})
        if i == Intent.GUIDE_FAQ:
            return Step("tool", tool="search_knowledge", args={"query": (e.question or "")[:300] or "hướng dẫn", "kinds": ["faq", "guide"]})
        if i == Intent.TAXONOMY:
            return Step("tool", tool="list_taxonomy", args={"kind": e.taxonomy_kind or "categories"})
        if i == Intent.WORKSHOP:
            return Step("tool", tool="find_nearby_workshops", args={})
        if i == Intent.DESIGN_TASK:
            if not e.task_id:
                return Step("clarify", message="Bạn cho mình mã task 3D (dạng xxxxxxxx-xxxx-…) nhé.")
            return Step("tool", tool="get_design_task_status", args={"task_id": e.task_id})

        if i in (Intent.PRODUCT_DETAIL, Intent.INVENTORY):
            ref, question = _product_ref(e, state)
            if ref is None and question:
                return Step("clarify", message=question)
            if ref is None:  # có loại/tên nhưng không có sản phẩm cụ thể → tìm theo tiêu chí
                return self._search(e, state)
            return Step("tool", tool="get_product" if i == Intent.PRODUCT_DETAIL else "get_inventory", args=ref)

        if i == Intent.COMPARE:
            refs: list[dict[str, Any]] = [{"sku": c} for c in e.product_codes]
            for n in e.ordinals:
                p = state.by_ordinal(n)
                if p:
                    refs.append({"product_id": p.id})
            if len(refs) < 2 and e.reference and state.current():
                refs.insert(0, {"product_id": state.current().id})
            if len(refs) < 2 and len(state.shown) >= 2 and not e.product_codes:
                refs = [{"product_id": p.id} for p in state.shown[:3]]
            if len(refs) < 2:
                return Step("clarify", message="Bạn muốn so sánh những sản phẩm nào? Cho mình 2–3 mã hoặc số thứ tự trong danh sách nhé.")
            return Step("tool", tool="compare_products", args={"products": refs[:3]})

        if i == Intent.RECOMMEND:
            return self._recommend(e, state)
        if i == Intent.PRODUCT_SEARCH:
            return self._search(e, state)
        return Step("clarify", message=CLARIFY_GENERIC)

    # ------------------------------------------------------------------ product advisor
    def _search(self, e: Entities, state: DialogueState) -> Step:
        c = {k: getattr(e, k) for k in ("category", "material", "color", "style", "room", "use_case",
                                         "budget_min", "budget_max", "seats", "size", "price_pref")}
        if c["budget_max"] is None and c["budget_min"] is None and e.amounts and c["category"]:
            c["budget_max"] = e.amounts[0]  # "bàn làm việc 2tr5" = ngân sách tối đa
        if not any(v not in (None, "") for v in c.values()):
            if e.product_name:  # món không thuộc danh mục đã biết → tra theo tên, không đoán
                return Step("tool", tool="get_product", args={"name": e.product_name})
            return Step("clarify", message=CATEGORY_QUESTION)
        state.constraints = state.merge_constraints(c)
        return Step("tool", tool="recommend_products", args={**_recommend_args(state.constraints), "mode": "search", "limit": 5,
                                                             **_diversity(e)})

    def _recommend(self, e: Entities, state: DialogueState, continuation: bool = False) -> Step:
        new = {k: getattr(e, k) for k in ("category", "material", "color", "style", "room", "use_case",
                                           "budget_min", "budget_max", "seats", "size", "price_pref")}
        if new.get("budget_max") is None and new.get("budget_min") is None and e.amounts:
            new["budget_max"] = e.amounts[0]  # "10 triệu", "tầm 10 củ" khi đang tư vấn = ngân sách
        constraints = state.merge_constraints(new)

        # So sánh tương đối: rẻ hơn / đắt hơn / nhỏ hơn / lớn hơn so với sản phẩm tham chiếu
        if e.relative:
            ref = None
            if e.product_codes:
                return Step("tool", tool="get_product", args={"sku": e.product_codes[0]}, needs_reference_detail=True,
                            note=e.relative)
            if e.ordinal:
                ref = state.by_ordinal(e.ordinal)
            ref = ref or state.current() or (state.shown[0] if state.shown else None)
            if ref is None:
                return Step("clarify", message="Bạn muốn so với sản phẩm nào? Cho mình tên hoặc mã sản phẩm nhé.")
            # Luôn đọc lại sản phẩm tham chiếu trong lượt này: giá/kích thước là dữ liệu realtime,
            # không dùng giá trị lưu trong memory từ lượt trước.
            state.constraints = constraints
            return Step("tool", tool="get_product", args={"product_id": ref.id}, reference=ref,
                        needs_reference_detail=True, note=e.relative)

        if not constraints.get("category"):
            state.pending, state.constraints = "recommend", constraints
            return Step("clarify", message=CATEGORY_QUESTION)
        detail_keys = set(constraints) - {"category"}
        key = f"recommend:{constraints['category']}"
        if not detail_keys and key not in state.asked:
            state.asked.add(key)
            state.pending, state.constraints = "recommend", constraints
            extra = "số người dùng" if "bàn ăn" in constraints["category"] else "kích thước/không gian đặt"
            return Step("clarify", message=f"Ngân sách khoảng bao nhiêu và {extra} thế nào?")
        state.pending = None
        state.constraints = constraints
        return Step("tool", tool="recommend_products", args={**_recommend_args(constraints), "mode": "recommend", **_diversity(e)})

    @staticmethod
    def relative_step(relative: str, ref: ShownProduct, constraints: dict[str, Any], state: DialogueState) -> Step:
        c = dict(constraints)
        if ref.category and not c.get("category"):
            c["category"] = ref.category.lower()
        args: dict[str, Any] = {**_recommend_args(c), "exclude_ids": [ref.id], "mode": "recommend"}
        if relative == "cheaper" and ref.price:
            args["budget_max"] = ref.price - 1
            args.pop("budget_min", None)
        elif relative == "more_expensive" and ref.price:
            args["budget_min"] = ref.price + 1
            args.pop("budget_max", None)
        elif relative == "smaller" and ref.area_cm2:
            args["max_area_cm2"] = ref.area_cm2
            args.pop("seats", None)
            args.pop("size", None)
        elif relative == "larger" and ref.area_cm2:
            args["min_area_cm2"] = ref.area_cm2
            args.pop("size", None)
        state.constraints = {k: v for k, v in c.items()}
        state.pending = None
        return Step("tool", tool="recommend_products", args=args, reference=ref, note=relative)
