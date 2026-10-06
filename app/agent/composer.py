"""
Composer: câu trả lời NGẮN, CHỈ từ dữ liệu trong ToolResult (không suy diễn, không marketing, không CTA).

- Sản phẩm: "- Tên — 1.990.000đ"; chỉ trả trường được hỏi (giá / kích thước / chất liệu / màu / chi tiết).
- Nhà cung cấp: chỉ trường Backend công khai (điện thoại, email, khu vực); giờ mở cửa/chính sách chưa có → nói rõ.
- Không có dữ liệu → NO_INFO; lỗi hệ thống dữ liệu sản phẩm → PRODUCT_API_ERROR (app.domain.messages).
- Dữ liệu đầy đủ (lý do gợi ý, nguồn, biến thể…) nằm trong blocks/sources cho Frontend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.api.schemas import Block, SourceOut
from app.domain.messages import NO_INFO, PRODUCT_API_ERROR, PRODUCT_TOOLS, SYSTEM_ERROR
from app.domain.results import ToolResult, ToolStatus

TOOL_TOPICS = {
    "list_branches": "chi nhánh", "search_knowledge": "câu hỏi này", "get_inventory": "tồn kho",
    "find_nearby_workshops": "xưởng gần bạn", "get_order_status": "đơn hàng",
}
POLICY_LABELS = {"shipping": "giao hàng", "return": "đổi trả", "warranty": "bảo hành", "payment": "thanh toán",
                 "terms": "điều khoản", "privacy": "bảo mật"}
LOW_STOCK_THRESHOLD = 5  # ≤ ngưỡng → "sắp hết" (không công khai số lượng chính xác)

CLARIFY = "Bạn cần tìm sản phẩm nào (tên, mã hoặc loại nội thất)?"
MAX_DESCRIPTION = 300


def vnd(value: Any) -> str:
    if value is None:
        return "chưa có giá"
    return f"{float(value):,.0f}đ".replace(",", ".")


def stock_label(total: int) -> str:
    if total <= 0:
        return "hết hàng"
    return "sắp hết" if total <= LOW_STOCK_THRESHOLD else "còn hàng"


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
                out.lines += [f"- {c['name']}" + (f" — {vnd(c['price_from'])}" if c.get("price_from") is not None else "")
                              for c in r.data["candidates"]]
                out.blocks.append(Block(kind="candidates", data=r.data["candidates"]))
        elif r.status == ToolStatus.ERROR:
            out.kind = "error"
            out.lines.append(PRODUCT_API_ERROR if product else (r.message or SYSTEM_ERROR))
        elif r.status == ToolStatus.NOT_FOUND and product:
            out.lines.append(NO_INFO)
            if r.data:
                out.blocks.append(Block(kind="recommendation", data=r.data))
        else:  # NOT_FOUND, DENIED, INVALID
            out.lines.append(r.message or NO_INFO)
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
    if "description" in set(asked):
        if d.get("supplier_name"):
            lines.append(f"- Nhà cung cấp: {d['supplier_name']}")
        if d.get("description"):
            desc = d["description"].strip()
            lines.append(f"- Mô tả: {desc[:MAX_DESCRIPTION]}{'…' if len(desc) > MAX_DESCRIPTION else ''}")
    if "price" not in want:
        lines.insert(0, name)
    return lines


def _supplier_lines(d: dict[str, Any]) -> list[str]:
    name = d["name"]
    if d.get("topic"):
        label = POLICY_LABELS.get(d["topic"], d["topic"])
        lines = [f"Hiện chưa có thông tin đã xác minh về chính sách {label} của {name}."]
        if d.get("phone") or d.get("email"):
            lines.append(f"- Liên hệ {name}: " + " · ".join(x for x in (d.get("phone"), d.get("email")) if x))
        return lines
    want = set(d.get("fields") or []) or {"hotline", "email", "address"}
    area = "; ".join(", ".join(x for x in (s.get("district"), s.get("city")) if x) for s in d.get("stores") or [])
    lines = [f"{name}" + (f" (nhà cung cấp của {d['product']})" if d.get("product") else "")]
    if "hotline" in want:
        lines.append(f"- Điện thoại: {d.get('phone') or 'chưa có thông tin'}")
    if "email" in want:
        lines.append(f"- Email: {d.get('email') or 'chưa có thông tin'}")
    if "address" in want:
        lines.append(f"- Khu vực cửa hàng: {area or 'chưa có thông tin'}")
    if "opening_hours" in want:
        lines.append("- Giờ hoạt động: chưa có thông tin đã xác minh")
    return lines


def _compose_ok(r: ToolResult, out: Composed, asked: list[str]) -> None:
    d = r.data
    t = r.tool
    if t == "get_supplier_info":
        out.lines += _supplier_lines(d)
        out.blocks.append(Block(kind="supplier_info", data=d))
    elif t in ("list_branches", "find_nearby_workshops"):
        for b in d:
            loc = ", ".join(x for x in (b.get("address"), b.get("district"), b.get("city")) if x)
            dist = f" — {b['distance_km']:.1f} km" if b.get("distance_km") is not None else ""
            phone = f" — {b['phone']}" if b.get("phone") else ""
            out.lines.append(f"- {b.get('name') or 'Cửa hàng'}: {loc or 'chưa có địa chỉ'}{dist}{phone}")
        out.blocks.append(Block(kind="workshop_list" if t == "find_nearby_workshops" else "branch_list", data=d))
    elif t == "recommend_products":
        show_supplier = d.get("distinct_suppliers")
        out.lines += [f"- {it['name']} — {vnd(it.get('price'))}" + (f" ({it['supplier']})" if show_supplier and it.get("supplier") else "")
                      for it in d["items"]]
        out.blocks.append(Block(kind="recommendation", data=d))
    elif t == "get_product":
        out.lines += _product_lines(d, asked)
        out.blocks.append(Block(kind="product_detail", data=d))
    elif t == "compare_products":
        for row in d["rows"]:
            extra = ", ".join(x for x in (row.get("supplier"), row.get("material"), row.get("dimensions")) if x)
            out.lines.append(f"- {row['name']} — {vnd(row.get('price'))}" + (f" ({extra})" if extra else ""))
        out.blocks.append(Block(kind="product_comparison", data=d["rows"]))
    elif t == "get_inventory":
        for v in d["variants"]:
            label = d["product"] + (f" ({v['sku']})" if v.get("sku") and len(d["variants"]) > 1 else "")
            out.lines.append(f"- {label} — " + (stock_label(v["total"]) if v["status"] == "known" else "chưa có dữ liệu tồn kho"))
        out.blocks.append(Block(kind="inventory", data={**d, "checked_at": datetime.now(timezone.utc).isoformat()}))
    elif t == "get_order_status":
        for o in d:
            ref = o.get("order_number") or o["id"][:8]
            extra = ", ".join(x for x in (o.get("workshop_name"), (o.get("updated_at") or "")[:10]) if x)
            out.lines.append(f"- Đơn {ref} — {o['status']}" + (f" ({extra})" if extra else ""))
        out.blocks.append(Block(kind="order_status", data=d))
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
