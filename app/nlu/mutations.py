"""
Parser DETERMINISTIC cho lệnh thay đổi dữ liệu (mutation) + nhận diện xác nhận/hủy/prompt-injection.

Tham số mutation (giá, số lượng, mô tả, ngày…) KHÔNG BAO GIỜ lấy từ LLM — chỉ từ parser này.
Thiếu tham số → ToolCall(args={"_needs": <slot>}) để agent hỏi lại.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.nlp.vietnamese import remove_vietnamese_diacritics
from app.nlu.extract import money  # cùng bộ chuẩn hóa tiền với tư vấn/tìm kiếm

VN_TZ = timezone(timedelta(hours=7))

@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- vocab
GREETINGS = {"chao", "xin chao", "chao shop", "shop oi", "hello", "hi", "alo", "chao ban", "co ai khong", "chao ad"}
INJECTION = [r"bo qua (moi |tat ca |cac )?(huong dan|quy tac|chi dan)", r"ignore (all |previous |the )*(instructions|rules)",
             r"system prompt", r"\bprompt he thong\b", r"(toi|minh|tao) la (admin|quan tri|chu (shop|cua hang))",
             r"\bban (bay gio )?la admin\b", r"developer mode", r"\bjailbreak\b", r"cap quyen admin"]
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
CODE_RE = re.compile(r"^\s*(?:xac nhan|dong y|confirm)\b[\s:]*([a-z0-9]{6})?\s*$")
CANCEL_RE = re.compile(r"^\s*(huy|huy bo|cancel|khong dong y|khong xac nhan|thoi khong)\b")


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def plain(text: str) -> str:
    return remove_vietnamese_diacritics((text or "").lower())


def parse_dates(text_plain: str, today: date) -> tuple[date | None, date | None]:
    found = []
    for m in re.finditer(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", text_plain):
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if not m.group(3) and dt < today - timedelta(days=1):
            dt = date(y + 1, mo, d)
        found.append(dt)
    return (found[0] if found else None, found[1] if len(found) > 1 else None)


# ---------------------------------------------------------------- mutation parsers
def _text_after(raw: str, markers: list[str]) -> str | None:
    for mk in markers:
        m = re.search(mk + r"\s*[:：]?\s*[\"“']?(.+?)[\"”']?\s*$", raw, flags=re.IGNORECASE | re.DOTALL)
        if m and len(m.group(1).strip()) >= 2:
            return m.group(1).strip()
    return None


def parse_mutation(raw: str, u: str, p: str, skus: list[str], today: date) -> ToolCall | None:
    sku = skus[0] if skus else None
    promo_words = r"khuyen mai|campaign|chien dich|chuong trinh|ctkm|voucher|ma giam|uu dai"

    # ---- khuyến mãi: tạo
    if _has(r"\b(tao|lap|them|mo|len)\b.*\b(" + promo_words + r")\b", u):
        pct = re.search(r"(\d+(?:[.,]\d+)?)\s*(%|phan tram)", p)
        amounts = money(p)
        args: dict[str, Any] = {}
        if pct:
            args.update(type="percentage", value=float(pct.group(1).replace(",", ".")))
        elif _has(r"mien phi (van chuyen|ship|giao hang)|free ship", u):
            args.update(type="free_shipping", value=0)
        elif amounts:
            args.update(type="fixed_amount", value=amounts[0])
        else:
            return ToolCall("create_promotion", {"_needs": "value"})
        if _has(r"toan bo|tat ca (san pham|mat hang)|moi san pham|toan shop", u):
            args["all_products"] = True
        else:
            m = re.search(r"\bcho\s+(?:nhom|danh muc|dong|cac|san pham|mat hang)?\s*(.+?)(?:\s+(?:tu|trong|den|ap dung|voi|ma)\b|$)", p)
            if m:
                phrase = _orig_span(raw, p, m.start(1), m.end(1))
                args["categories"] = [c.strip() for c in re.split(r",|\s+và\s+|\s+va\s+", phrase) if c.strip()][:10]
        start, end = parse_dates(p, today)
        args["starts_on"] = (start or today).isoformat()
        args["ends_on"] = (end or ((start or today) + timedelta(days=30))).isoformat()
        code = re.search(r"\bm[aã]\s+([A-Z0-9_-]{3,30})\b", raw)
        if code:
            args["code"] = code.group(1)
        label = f"{args['value']:g}%" if args["type"] == "percentage" else ("Freeship" if args["type"] == "free_shipping" else f"{args['value']:,.0f}đ")
        scope = "toàn bộ" if args.get("all_products") else ", ".join(args.get("categories", []))
        args["name"] = f"Giảm {label} {scope}".strip()[:120]
        return ToolCall("create_promotion", args)

    # ---- khuyến mãi: đổi trạng thái
    m = re.search(r"\b(tam dung|dung|ket thuc|kich hoat|bat|tat|mo lai)\b.*\b(" + promo_words + r")\b", u)
    if m:
        status = {"tam dung": "paused", "dung": "paused", "tat": "paused", "ket thuc": "ended"}.get(m.group(1), "active")
        ids = re.findall(r"\bpromo-[a-z0-9]+\b", p)
        codes = re.findall(r"\b[A-Z][A-Z0-9_-]{2,29}\b", raw)
        token = ids[0] if ids else (codes[0] if codes else None)
        return ToolCall("set_promotion_status", {"promotion": token, "status": status} if token else {"_needs": "promotion"})

    # ---- giá sản phẩm
    if _has(r"\b(doi|cap nhat|sua|chinh|dat|thay doi|dieu chinh|tang|giam|set)\b.*\bgia\b", u) and not _has(promo_words, u):
        amounts = money(p)
        if sku and amounts:
            return ToolCall("update_product_price", {"sku": sku, "new_price": amounts[-1]})
        if sku or amounts:
            return ToolCall("update_product_price", {"_needs": "sku" if not sku else "price"})

    # ---- mô tả sản phẩm
    if _has(r"\b(cap nhat|sua|doi|thay|viet lai)\b.*\bmo ta\b", u):
        desc = _text_after(raw, [r"mô tả[^:]*?:", r"mo ta[^:]*?:", r"\bthành\b", r"\bthanh\b"])
        if desc:
            desc = re.sub(r"^(?:[A-Za-z]{2,}[-_]?\d+\S*\s*)", "", desc).strip(" :")
        args = {"description": desc} if desc else {}
        if sku:
            args["sku"] = sku
        return ToolCall("update_product_description", args if (desc and sku) else {"_needs": "description" if not desc else "sku"})

    # ---- tồn kho
    if _has(r"\b(ton kho|nhap kho|xuat kho|so luong kho)\b", u) or (_has(r"\b(nhap them|xuat)\b", u) and sku):
        m = re.search(r"\b(nhap them|nhap|them|tang|cong|bo sung|xuat|giam|tru|bot)\s+(\d+)", p)
        if sku and m:
            delta = int(m.group(2))
            if m.group(1) in {"xuat", "giam", "tru", "bot"}:
                delta = -delta
            args = {"sku": sku, "delta": delta}
            store = re.search(r"\b(?:kho|store)\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b", raw, flags=re.IGNORECASE)
            if store:
                args["store_id"] = store.group(1)
            return ToolCall("adjust_inventory", args)

    # ---- thông tin cửa hàng
    if _has(r"\b(doi|cap nhat|sua|thay|dat lai|chinh)\b", u):
        if _has(r"\b(hotline|so dien thoai|dien thoai|sdt)\b", u):
            phone = re.search(r"(\+?\d[\d .-]{6,14}\d)", raw)
            return ToolCall("update_store_info", {"hotline": phone.group(1).strip()} if phone else {"_needs": "hotline"})
        if _has(r"\bemail\b", u):
            mail = re.search(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", raw)
            return ToolCall("update_store_info", {"email": mail.group(0)} if mail else {"_needs": "email"})
        if _has(r"\bgio (mo|lam viec|hoat dong)", u):
            m = re.search(r"(\d{1,2})\s*(?:h|:|gio)\s*(\d{2})?\s*(?:-|den|toi|->)\s*(\d{1,2})\s*(?:h|:|gio)?\s*(\d{2})?", p)
            if m:
                open_, close = f"{int(m.group(1)):02d}:{m.group(2) or '00'}", f"{int(m.group(3)):02d}:{m.group(4) or '00'}"
                days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
                return ToolCall("update_store_info", {"opening_hours": [{"days": days, "open": open_, "close": close}]})
            return ToolCall("update_store_info", {"_needs": "opening_hours"})
        if _has(r"\bdia chi (cua hang|shop|woodhub)\b", u):
            addr = _text_after(raw, [r"\bthành\b", r"\bthanh\b", r":"])
            return ToolCall("update_store_info", {"address": addr} if addr else {"_needs": "address"})

    # ---- FAQ
    if _has(r"\b(them|tao|sua|cap nhat)\b.*\b(faq|cau hoi thuong gap)\b", u):
        m = re.search(r"(?:\bhỏi|\bhoi|\bq)\s*[:：]\s*(.+?)\s*(?:\||;|\n)\s*(?:trả lời|tra loi|đáp|dap|a)\s*[:：]\s*(.+)$", raw,
                      flags=re.IGNORECASE | re.DOTALL)
        if m:
            args = {"question": m.group(1).strip(), "answer": m.group(2).strip()}
            doc = re.search(r"\b(faq-[a-z0-9]+)\b", p)
            if doc:
                args["document_id"] = doc.group(1)
            return ToolCall("upsert_faq", args)
        return ToolCall("upsert_faq", {"_needs": "faq"})

    # ---- danh mục / vật liệu
    for kind, words in (("upsert_category", r"danh muc"), ("upsert_material", r"vat lieu|chat lieu")):
        m = re.search(rf"\bdoi ten ({words})\s+(.+?)\s+thanh\s+(.+)$", p)
        if m:
            src, dst = _orig_span(raw, p, m.start(2), m.end(2)), _orig_span(raw, p, m.start(3), m.end(3))
            return ToolCall(kind, {"rename_from": src, "name": dst})
        m = re.search(rf"\b(tao|them)\s+({words})(?: moi)?\s+(.+)$", p)
        if m:
            return ToolCall(kind, {"name": _orig_span(raw, p, m.start(3), m.end(3))})
    return None


def _orig_span(raw: str, p: str, start: int, end: int) -> str:
    """Lấy lại đoạn có dấu từ tin nhắn gốc (plain() giữ nguyên độ dài khi bỏ dấu)."""
    text = raw[start:end] if len(raw) == len(p) else p[start:end]
    return text.strip(" \"'“”.")
