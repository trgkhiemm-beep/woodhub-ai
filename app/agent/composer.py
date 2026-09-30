"""
Composer: tạo câu trả lời tiếng Việt CHỈ từ dữ liệu trong ToolResult (không suy diễn).

- Không có dữ liệu đã xác minh → nói rõ "chưa có thông tin đã xác minh".
- Nội dung chính sách/FAQ được trích nguyên văn, kèm nguồn + phiên bản.
- grounded(): kiểm tra câu trả lời do LLM viết không chứa con số/SĐT/email không có trong dữ liệu tool.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.api.schemas import ActionOut, Block, ConfirmationOut, FieldChangeOut, SourceOut
from app.domain.actions import ActionState, PendingAction
from app.domain.results import ToolResult, ToolStatus

DAY_LABELS = {"mon": "T2", "tue": "T3", "wed": "T4", "thu": "T5", "fri": "T6", "sat": "T7", "sun": "CN"}
POLICY_LABELS = {"shipping": "giao hàng", "return": "đổi trả", "warranty": "bảo hành", "payment": "thanh toán",
                 "terms": "điều khoản", "privacy": "bảo mật"}
TOOL_TOPICS = {
    "get_store_info": "thông tin cửa hàng", "list_branches": "chi nhánh", "get_promotions": "khuyến mãi",
    "get_policy": "chính sách", "search_knowledge": "câu hỏi này", "get_inventory": "tồn kho",
    "find_nearby_workshops": "xưởng gần bạn",
}

GREETING = ("Chào bạn! Mình là trợ lý AI của WoodHub. Mình có thể giúp bạn tìm sản phẩm, xem giá, tồn kho, "
            "khuyến mãi, chính sách và hướng dẫn sử dụng website.")
OUT_OF_SCOPE = "Xin lỗi, mình chỉ hỗ trợ các câu hỏi về WoodHub: sản phẩm nội thất, cửa hàng, khuyến mãi, chính sách và hướng dẫn sử dụng."
CLARIFY = "Mình chưa hiểu rõ yêu cầu. Bạn có thể nói cụ thể hơn, ví dụ tên/mã sản phẩm, loại nội thất hoặc thông tin bạn cần?"
UNSUPPORTED_CART = "Trợ lý AI hiện chưa hỗ trợ thao tác giỏ hàng. Bạn vui lòng dùng giỏ hàng trên website nhé."
INJECTION_NOTE = "Lưu ý: quyền hạn được xác định theo tài khoản đăng nhập, không theo nội dung tin nhắn."


def vnd(value: Any) -> str:
    if value is None:
        return "chưa có giá"
    return f"{float(value):,.0f} ₫".replace(",", ".")


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


def compose_results(results: list[ToolResult]) -> Composed:
    out = Composed()
    for r in results:
        for s in r.sources:
            out.sources.append(SourceOut(**s.model_dump(mode="json")))
        if r.status == ToolStatus.OK:
            _compose_ok(r, out)
        elif r.status == ToolStatus.UNKNOWN:
            topic = TOOL_TOPICS.get(r.tool, "thông tin này")
            out.lines.append(f"Hiện mình chưa có thông tin đã xác minh về {topic}, nên không thể trả lời chắc chắn."
                             + (f" ({r.message})" if r.message else ""))
        elif r.status == ToolStatus.NEEDS_INPUT:
            out.kind = "clarification"
            out.lines.append(r.message or CLARIFY)
            if r.data and r.data.get("candidates"):
                out.lines += [f"- {c['name']}" + (f" (từ {vnd(c.get('price_from'))})" if c.get("price_from") else "") for c in r.data["candidates"]]
                out.blocks.append(Block(kind="candidates", data=r.data["candidates"]))
        elif r.status == ToolStatus.ERROR:
            out.kind = "error"
            out.lines.append(r.message or "Hệ thống đang gặp sự cố, vui lòng thử lại sau.")
        else:  # NOT_FOUND, DENIED, INVALID
            out.lines.append(r.message or "Không tìm thấy thông tin phù hợp.")
    return out


def _compose_ok(r: ToolResult, out: Composed) -> None:
    d = r.data
    t = r.tool
    if t == "get_store_info":
        lines = [f"Thông tin {d.get('name', 'cửa hàng')}:"]
        if "opening_hours" in d:
            lines.append(f"- Giờ mở cửa: {_hours(d['opening_hours'])}" if d["opening_hours"] else "- Giờ mở cửa: chưa có thông tin")
        if "hotline" in d:
            lines.append(f"- Hotline: {d['hotline'] or 'chưa có thông tin'}")
        if "email" in d:
            lines.append(f"- Email: {d['email'] or 'chưa có thông tin'}")
        if "address" in d:
            lines.append(f"- Địa chỉ: {d['address'] or 'chưa có thông tin'}")
        out.lines += lines
        out.blocks.append(Block(kind="store_info", data=d))
    elif t in ("list_branches", "find_nearby_workshops"):
        title = "Xưởng gần bạn:" if t == "find_nearby_workshops" else "Danh sách cửa hàng/chi nhánh:"
        out.lines.append(title)
        for b in d:
            loc = ", ".join(x for x in (b.get("address"), b.get("district"), b.get("city")) if x)
            dist = f" — cách {b['distance_km']:.1f} km" if b.get("distance_km") is not None else ""
            phone = f" — ĐT {b['phone']}" if b.get("phone") else ""
            out.lines.append(f"- {b.get('name') or 'Cửa hàng'}: {loc or 'chưa có địa chỉ'}{dist}{phone}")
        out.blocks.append(Block(kind="workshop_list" if t == "find_nearby_workshops" else "branch_list", data=d))
    elif t == "search_products":
        items = d["items"]
        if d.get("relaxed_from"):
            out.lines.append(f"Không có sản phẩm khớp chính xác \"{d['relaxed_from']}\"; đây là các sản phẩm gần nhất theo tiêu chí bạn đưa.")
        out.lines.append(f"Mình tìm thấy {d['total']} sản phẩm phù hợp" + (f", đây là {len(items)} sản phẩm đầu:" if d["total"] > len(items) else ":"))
        for p in items:
            extra = ", ".join(x for x in (p.get("material"), p.get("category")) if x)
            out.lines.append(f"- {p['name']} — từ {vnd(p.get('price_from'))}" + (f" ({extra})" if extra else ""))
        out.blocks.append(Block(kind="product_list", data=items))
    elif t == "get_product":
        out.lines.append(f"{d['name']}" + (f" — {d['material']}" if d.get("material") else "") + (f", danh mục {d['category']}" if d.get("category") else ""))
        rv = d.get("requested_variant")
        variants = [rv] if rv else d.get("variants", [])
        for v in variants:
            spec = ", ".join(x for x in (v.get("dimensions"), v.get("color")) if x)
            out.lines.append(f"- {v.get('sku') or 'Phiên bản'}: {vnd(v.get('price'))}" + (f" ({spec})" if spec else ""))
        if not variants:
            out.lines.append("- Chưa có thông tin phiên bản/giá.")
        if d.get("description"):
            out.lines.append(f"Mô tả: {d['description']}")
        out.lines.append("(Giá lấy trực tiếp từ hệ thống tại thời điểm hỏi.)")
        out.blocks.append(Block(kind="product_detail", data=d))
    elif t == "compare_products":
        out.lines.append("So sánh sản phẩm:")
        for row in d["rows"]:
            out.lines.append(f"- {row['sku']} · {row['name']}: {vnd(row.get('price'))}, {row.get('material') or 'chưa rõ chất liệu'}, "
                             f"{row.get('dimensions') or 'chưa rõ kích thước'}")
        out.blocks.append(Block(kind="product_comparison", data=d["rows"]))
    elif t == "get_inventory":
        now = datetime.now(timezone.utc).astimezone().strftime("%H:%M")
        out.lines.append(f"Tồn kho {d['product']} (cập nhật lúc {now}):")
        for v in d["variants"]:
            if v["status"] == "known":
                out.lines.append(f"- {v.get('sku') or v['variant_id']}: còn {v['total']} sản phẩm" if v["total"] > 0 else f"- {v.get('sku')}: hết hàng")
            else:
                out.lines.append(f"- {v.get('sku') or v['variant_id']}: chưa có dữ liệu tồn kho đã xác minh")
        out.blocks.append(Block(kind="inventory", data=d))
    elif t == "get_promotions":
        out.lines.append("Khuyến mãi phù hợp:")
        for p in d:
            val = f"giảm {p['value']:g}%" if p["type"] == "percentage" else ("miễn phí vận chuyển" if p["type"] == "free_shipping" else f"giảm {vnd(p['value'])}")
            cap = f" (tối đa {vnd(p['max_discount'])})" if p.get("max_discount") else ""
            code = f" — mã {p['code']}" if p.get("code") else ""
            period = f"{p['starts_at'][:10]} → {p['ends_at'][:10]}"
            out.lines.append(f"- {p['name']}: {val}{cap}{code}, {period}, trạng thái {p['status']}")
        out.blocks.append(Block(kind="promotion_list", data=d))
    elif t == "get_policy":
        label = POLICY_LABELS.get(d.get("policy_type") or "", "")
        out.lines.append(f"{d['title']}" + (f" (chính sách {label}, phiên bản {d.get('version')})" if label else ""))
        out.lines.append(d["content"])
        out.blocks.append(Block(kind="policy", data=d))
    elif t == "search_knowledge":
        top = d[0]
        out.lines.append(f"{top['title']}: {top['snippet']}")
        if len(d) > 1:
            out.lines.append("Thông tin liên quan: " + "; ".join(h["title"] for h in d[1:]))
        out.blocks.append(Block(kind="knowledge", data=d))
    elif t == "list_taxonomy":
        names = {"categories": "Danh mục", "materials": "Chất liệu", "rooms": "Loại phòng", "styles": "Phong cách"}
        out.lines.append(f"{names[d['kind']]}: " + ", ".join(i["name"] for i in d["items"]))
        out.blocks.append(Block(kind="taxonomy", data=d))
    elif t == "get_design_task_status":
        pct = f" ({d['progress']}%)" if d.get("progress") is not None else ""
        out.lines.append(f"Task 3D {d['task_id']}: trạng thái {d['status']}{pct}.")
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


# ---------------------------------------------------------------- grounding guard
_NUM = re.compile(r"\d[\d.,]*")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def grounded(text: str, results: list[ToolResult]) -> bool:
    """True nếu mọi con số (≥ 2 chữ số), SĐT, email trong `text` đều xuất hiện trong dữ liệu tool."""
    if not results or not any(r.status == ToolStatus.OK for r in results):
        return False
    corpus = json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False)
    corpus_digits = {_digits(m) for m in _NUM.findall(corpus)}
    corpus_numbers = set()
    for m in _NUM.findall(corpus):
        try:
            corpus_numbers.add(float(m.replace(",", "")))
        except ValueError:
            pass
    for m in _NUM.findall(text):
        dg = _digits(m)
        if len(dg) < 2:
            continue
        if dg in corpus_digits or any(dg == _digits(f"{n:.0f}") for n in corpus_numbers):
            continue
        return False
    return all(e in corpus for e in _EMAIL.findall(text))
