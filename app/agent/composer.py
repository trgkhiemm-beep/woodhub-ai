"""
Composer: câu trả lời NGẮN, CHỈ từ dữ liệu trong ToolResult (không suy diễn, không marketing, không CTA).

- Sản phẩm: "- Tên — 1.990.000đ"; chỉ trả trường được hỏi (giá / kích thước / chất liệu / màu / chi tiết).
- Không có dữ liệu → NO_PRODUCT; lỗi hệ thống dữ liệu sản phẩm → PRODUCT_API_ERROR (app.domain.messages).
- Dữ liệu đầy đủ (lý do gợi ý, nguồn, biến thể…) nằm trong blocks/sources cho Frontend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.api.schemas import ActionOut, Block, ConfirmationOut, FieldChangeOut, SourceOut
from app.domain.actions import ActionState, PendingAction
from app.domain.messages import NO_PRODUCT, PRODUCT_API_ERROR, PRODUCT_TOOLS, SYSTEM_ERROR
from app.domain.results import ToolResult, ToolStatus

DAY_LABELS = {"mon": "T2", "tue": "T3", "wed": "T4", "thu": "T5", "fri": "T6", "sat": "T7", "sun": "CN"}
TOOL_TOPICS = {
    "get_store_info": "thông tin cửa hàng", "list_branches": "chi nhánh", "get_promotions": "khuyến mãi",
    "get_policy": "chính sách", "search_knowledge": "câu hỏi này", "get_inventory": "tồn kho",
    "find_nearby_workshops": "xưởng gần bạn",
}

CLARIFY = "Bạn cần tìm sản phẩm nào (tên, mã hoặc loại nội thất)?"
MAX_DESCRIPTION = 300


def vnd(value: Any) -> str:
    if value is None:
        return "chưa có giá"
    return f"{float(value):,.0f}đ".replace(",", ".")


def _hours(items: list[dict[str, Any]]) -> str:
    parts = []
    for h in items:
        days = h.get("days") or []
        label = f"{DAY_LABELS.get(days[0], days[0])}–{DAY_LABELS.get(days[-1], days[-1])}" if len(days) > 1 else ", ".join(DAY_LABELS.get(d, d) for d in days)
        parts.append(f"{label}: {h.get('open')}–{h.get('close')}")
    return "; ".join(parts)


class Composed:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.blocks: list[Block] = []
        self.sources: list[SourceOut] = []
        self.kind: str = "answer"


def compose_results(results: list[ToolResult], *, asked: list[str] | None = None) -> Composed:
    out = Composed()
    for r in results:
        for s in r.sources:
            out.sources.append(SourceOut(**s.model_dump(mode="json")))
        product = r.tool in PRODUCT_TOOLS
        if r.status == ToolStatus.OK:
            _compose_ok(r, out, asked or [])
        elif r.status == ToolStatus.UNKNOWN and r.tool == "get_inventory" and r.data:
            _compose_ok(r, out, [])  # "- Tên — chưa có dữ liệu tồn kho"
        elif r.status == ToolStatus.UNKNOWN:
            topic = TOOL_TOPICS.get(r.tool, "thông tin này")
            out.lines.append(f"Hiện chưa có thông tin đã xác minh về {topic}.")
        elif r.status == ToolStatus.NEEDS_INPUT:
            out.kind = "clarification"
            out.lines.append(r.message or CLARIFY)
            if r.data and r.data.get("candidates"):
                out.lines += [f"- {c['name']} — {vnd(c.get('price_from'))}" for c in r.data["candidates"]]
                out.blocks.append(Block(kind="candidates", data=r.data["candidates"]))
        elif r.status == ToolStatus.ERROR:
            out.kind = "error"
            out.lines.append(PRODUCT_API_ERROR if product else (r.message or SYSTEM_ERROR))
        elif r.status == ToolStatus.NOT_FOUND and product:
            out.lines.append(NO_PRODUCT)
            if r.data:
                out.blocks.append(Block(kind="recommendation", data=r.data))
        else:  # NOT_FOUND, DENIED, INVALID
            out.lines.append(r.message or "Không tìm thấy thông tin phù hợp.")
    return out


def _product_lines(d: dict[str, Any], asked: list[str]) -> list[str]:
    name = d["name"]
    rv = d.get("requested_variant")
    variants = [rv] if rv else (d.get("variants") or [])
    want = set(asked) or {"price"}
    if "description" in want:
        want |= {"price", "material", "dimensions", "color"}
    lines: list[str] = []
    if "price" in want:
        prices = {v.get("price") for v in variants}
        if len(prices) <= 1:
            lines.append(f"- {name} — {vnd(next(iter(prices), None))}")
        else:
            for v in variants:
                spec = ", ".join(x for x in (v.get("color"), v.get("dimensions")) if x) or v.get("sku") or "phiên bản"
                lines.append(f"- {name} ({spec}) — {vnd(v.get('price'))}")
    if "dimensions" in want:
        dims = list(dict.fromkeys(v["dimensions"] for v in variants if v.get("dimensions")))
        lines.append(f"- Kích thước: {'; '.join(dims) if dims else 'chưa có thông tin'}")
    if "material" in want:
        lines.append(f"- Chất liệu: {d.get('material') or 'chưa có thông tin'}")
    if "color" in want:
        colors = list(dict.fromkeys(v["color"] for v in variants if v.get("color")))
        lines.append(f"- Màu: {', '.join(colors) if colors else 'chưa có thông tin'}")
    if "description" in set(asked) and d.get("description"):
        desc = d["description"].strip()
        lines.append(f"- Mô tả: {desc[:MAX_DESCRIPTION]}{'…' if len(desc) > MAX_DESCRIPTION else ''}")
    if "price" not in want:
        lines.insert(0, name)
    return lines


def _compose_ok(r: ToolResult, out: Composed, asked: list[str]) -> None:
    d = r.data
    t = r.tool
    if t == "get_store_info":
        if "opening_hours" in d:
            out.lines.append(f"- Giờ mở cửa: {_hours(d['opening_hours'])}" if d["opening_hours"] else "- Giờ mở cửa: chưa có thông tin")
        if "hotline" in d:
            out.lines.append(f"- Hotline: {d['hotline'] or 'chưa có thông tin'}")
        if "email" in d:
            out.lines.append(f"- Email: {d['email'] or 'chưa có thông tin'}")
        if "address" in d:
            out.lines.append(f"- Địa chỉ: {d['address'] or 'chưa có thông tin'}")
        out.blocks.append(Block(kind="store_info", data=d))
    elif t in ("list_branches", "find_nearby_workshops"):
        for b in d:
            loc = ", ".join(x for x in (b.get("address"), b.get("district"), b.get("city")) if x)
            dist = f" — {b['distance_km']:.1f} km" if b.get("distance_km") is not None else ""
            phone = f" — {b['phone']}" if b.get("phone") else ""
            out.lines.append(f"- {b.get('name') or 'Cửa hàng'}: {loc or 'chưa có địa chỉ'}{dist}{phone}")
        out.blocks.append(Block(kind="workshop_list" if t == "find_nearby_workshops" else "branch_list", data=d))
    elif t == "recommend_products":
        out.lines += [f"- {it['name']} — {vnd(it.get('price'))}" for it in d["items"]]
        out.blocks.append(Block(kind="recommendation", data=d))
    elif t == "get_product":
        out.lines += _product_lines(d, asked)
        out.blocks.append(Block(kind="product_detail", data=d))
    elif t == "compare_products":
        for row in d["rows"]:
            extra = ", ".join(x for x in (row.get("material"), row.get("dimensions")) if x)
            out.lines.append(f"- {row['name']} — {vnd(row.get('price'))}" + (f" ({extra})" if extra else ""))
        out.blocks.append(Block(kind="product_comparison", data=d["rows"]))
    elif t == "get_inventory":
        for v in d["variants"]:
            label = d["product"] + (f" ({v['sku']})" if v.get("sku") and len(d["variants"]) > 1 else "")
            if v["status"] == "known":
                out.lines.append(f"- {label} — {'còn ' + str(v['total']) if v['total'] > 0 else 'hết hàng'}")
            else:
                out.lines.append(f"- {label} — chưa có dữ liệu tồn kho")
        out.blocks.append(Block(kind="inventory", data={**d, "checked_at": datetime.now(timezone.utc).isoformat()}))
    elif t == "get_promotions":
        for p in d:
            val = f"giảm {p['value']:g}%" if p["type"] == "percentage" else ("miễn phí vận chuyển" if p["type"] == "free_shipping" else f"giảm {vnd(p['value'])}")
            cap = f" (tối đa {vnd(p['max_discount'])})" if p.get("max_discount") else ""
            code = f", mã {p['code']}" if p.get("code") else ""
            out.lines.append(f"- {p['name']}: {val}{cap}{code}, đến {p['ends_at'][:10]}")
        out.blocks.append(Block(kind="promotion_list", data=d))
    elif t == "get_policy":
        out.lines.append(d["content"])
        out.blocks.append(Block(kind="policy", data=d))
    elif t == "search_knowledge":
        out.lines.append(d[0]["snippet"])
        out.blocks.append(Block(kind="knowledge", data=d))
    elif t == "list_taxonomy":
        out.lines.append(", ".join(i["name"] for i in d["items"]))
        out.blocks.append(Block(kind="taxonomy", data=d))
    elif t == "get_design_task_status":
        pct = f" ({d['progress']}%)" if d.get("progress") is not None else ""
        out.lines.append(f"Task 3D: {d['status']}{pct}.")
        out.blocks.append(Block(kind="design_task", data=d))


# ---------------------------------------------------------------- actions
def action_view(a: PendingAction, *, include_code: bool) -> ActionOut:
    pending = a.state == ActionState.PENDING_CONFIRMATION
    conf = None
    if pending:
        conf = ConfirmationOut(
            required=True, level=a.confirmation_level.value, code=a.confirmation_code if include_code else None,
            expires_at=a.expires_at, confirm_endpoint=f"/v1/agent/actions/{a.id}/confirm",
            cancel_endpoint=f"/v1/agent/actions/{a.id}/cancel", chat_phrase=f"xác nhận {a.confirmation_code}",
        )
    verified = True if a.state == ActionState.COMPLETED else (False if a.state == ActionState.UNVERIFIED else None)
    return ActionOut(
        id=a.id, tool=a.tool, operation=a.operation, state=a.state.value, summary=a.summary,
        target={"type": a.target_type, "id": a.target_id, "label": a.target_label},
        changes=[FieldChangeOut(**c.model_dump()) for c in a.changes], warnings=a.warnings, confirmation=conf,
        verified=verified, error_code=a.error_code, error_message=a.error_message, created_at=a.created_at, updated_at=a.updated_at,
    )


def _fmt(v: Any) -> str:
    if v is None:
        return "(trống)"
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1000:
        return vnd(v)
    if isinstance(v, list):
        return _hours(v) if v and isinstance(v[0], dict) and "open" in v[0] else ", ".join(map(str, v))
    return str(v)


def confirmation_message(a: PendingAction) -> str:
    lines = [f"Mình chuẩn bị thực hiện: {a.summary}."]
    for c in a.changes:
        lines.append(f"- {c.label}: {_fmt(c.before)} → {_fmt(c.after)}" if c.before is not None else f"- {c.label}: {_fmt(c.after)}")
    for w in a.warnings:
        lines.append(f"⚠ {w}")
    local = a.expires_at.astimezone().strftime("%H:%M")
    lines.append(f"Để xác nhận, trả lời \"xác nhận {a.confirmation_code}\" (hiệu lực đến {local}), hoặc \"hủy\" để bỏ qua. "
                 "Chưa có thay đổi nào được thực hiện.")
    return "\n".join(lines)


def action_result_message(a: PendingAction) -> str:
    if a.state == ActionState.COMPLETED:
        return f"Đã thực hiện và xác minh thành công: {a.summary}."
    if a.state == ActionState.UNVERIFIED:
        return (f"Đã gửi yêu cầu \"{a.summary}\" nhưng CHƯA xác minh được kết quả trên hệ thống. "
                f"{a.error_message or ''} Vui lòng kiểm tra lại trước khi thử lại.").strip()
    if a.state == ActionState.FAILED:
        reason = "dữ liệu đã bị thay đổi từ lúc đề xuất" if a.error_code == "STALE_DATA" else (a.error_message or a.error_code or "lỗi không xác định")
        return f"Chưa thực hiện được \"{a.summary}\": {reason}. Không có thay đổi nào được ghi nhận."
    if a.state == ActionState.CANCELLED:
        return f"Đã hủy yêu cầu: {a.summary}. Không có thay đổi nào được thực hiện."
    if a.state == ActionState.EXPIRED:
        return f"Yêu cầu \"{a.summary}\" đã hết hạn xác nhận. Vui lòng tạo lại yêu cầu."
    return f"Yêu cầu \"{a.summary}\" đang ở trạng thái {a.state.value}."
