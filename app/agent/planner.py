"""
Rule-based planner (deterministic): tin nhắn tiếng Việt → danh sách tool call hoặc phản hồi trực tiếp.

Planner chỉ ĐỀ XUẤT tool; quyền, validation, xác nhận do ToolExecutor/ActionService quyết định.
Mọi xác nhận/hủy thay đổi luôn đi qua planner này (kể cả khi bật LLM) để không phụ thuộc vào LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from app.nlp.vietnamese import (
    FURNITURE_CANONICAL_TERMS, normalize_vietnamese_chat, remove_vietnamese_diacritics,
)
from app.tools.base import ConversationContext

VN_TZ = timezone(timedelta(hours=7))

DirectKind = Literal["greeting", "out_of_scope", "clarify", "unsupported_cart", "unsupported_image"]


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    calls: list[ToolCall] = field(default_factory=list)
    direct: DirectKind | None = None
    confirm: bool = False
    confirm_code: str | None = None
    cancel: bool = False
    flags: set[str] = field(default_factory=set)
    note: str | None = None

    def calls_need_input(self) -> bool:
        return any("_needs" in c.args for c in self.calls)


# ---------------------------------------------------------------- vocab
GREETINGS = {"chao", "xin chao", "chao shop", "shop oi", "hello", "hi", "alo", "chao ban", "co ai khong", "chao ad"}
OUT_OF_SCOPE = [r"\bthoi tiet\b", r"\bbitcoin\b", r"\bcrypto\b", r"\bchung khoan\b", r"\bbong da\b", r"\bworld cup\b",
                r"\bthe thao\b", r"\blap trinh\b", r"\bviet code\b", r"\bpython\b", r"\bjavascript\b", r"\bbai van\b",
                r"\bgiai phuong trinh\b", r"\bchinh tri\b", r"\bbau cu\b", r"\btong thong\b", r"\bnau an\b", r"\bdu lich\b",
                r"\btin tuc\b"]
INJECTION = [r"bo qua (moi |tat ca |cac )?(huong dan|quy tac|chi dan)", r"ignore (all |previous |the )*(instructions|rules)",
             r"system prompt", r"\bprompt he thong\b", r"(toi|minh|tao) la (admin|quan tri|chu (shop|cua hang))",
             r"\bban (bay gio )?la admin\b", r"developer mode", r"\bjailbreak\b", r"cap quyen admin"]
CITIES = {"ha noi": "Hà Nội", "ho chi minh": "Hồ Chí Minh", "hcm": "Hồ Chí Minh", "sai gon": "Hồ Chí Minh",
          "da nang": "Đà Nẵng", "can tho": "Cần Thơ", "hai phong": "Hải Phòng"}
POLICY_KEYWORDS = [("return", r"doi tra|tra hang|hoan tien|doi hang"), ("warranty", r"bao hanh"),
                   ("shipping", r"giao hang|van chuyen|\bship\b|phi ship"), ("payment", r"thanh toan|tra gop|chuyen khoan|cod\b"),
                   ("terms", r"dieu khoan"), ("privacy", r"bao mat|quyen rieng tu|du lieu ca nhan")]
WOODS = [("go soi", "gỗ sồi"), ("go oc cho", "gỗ óc chó"), ("go tan bi", "gỗ tần bì"), ("go thong", "gỗ thông"),
         ("go cao su", "gỗ cao su"), ("go cong nghiep", "gỗ công nghiệp"), ("mdf", "MDF")]
CATEGORY_WORDS = {"ban": "bàn", "ghe": "ghế", "tu": "tủ", "giuong": "giường", "ke": "kệ", "sofa": "sofa"}

SKU_RE = re.compile(r"\b([A-Za-z]{2,}[-_]?\d+[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)\b")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
CODE_RE = re.compile(r"^\s*(?:xac nhan|dong y|confirm)\b[\s:]*([a-z0-9]{6})?\s*$")
CANCEL_RE = re.compile(r"^\s*(huy|huy bo|cancel|khong dong y|khong xac nhan|thoi khong)\b")


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def plain(text: str) -> str:
    return remove_vietnamese_diacritics((text or "").lower())


def extract_skus(raw: str) -> list[str]:
    out = []
    for m in SKU_RE.finditer(raw or ""):
        token = m.group(1)
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token) and not UUID_RE.search(token):
            if not re.fullmatch(r"\d+(tr|k|m|cm|mm|h)", token.lower()):
                out.append(token.upper())
    return list(dict.fromkeys(out))


def parse_amounts(text_plain: str) -> list[float]:
    """Tiền VND từ văn bản không dấu: '8 trieu', '8tr5', '8,5 trieu', '500k', '8.000.000d'."""
    amounts: list[tuple[int, float]] = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(trieu|tr|cu)\s*(\d)?(?![a-z])", text_plain):
        val = float(m.group(1).replace(",", "."))
        if m.group(3):  # "8tr5" = 8.5 triệu
            val += int(m.group(3)) / 10
        amounts.append((m.start(), val * 1_000_000))
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(nghin|ngan|k)(?![a-z])", text_plain):
        amounts.append((m.start(), float(m.group(1).replace(",", ".")) * 1_000))
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{5,})(?![\d.,]*\s*(?:trieu|tr|k|nghin|%))", text_plain):
        amounts.append((m.start(), float(re.sub(r"[.,]", "", m.group(1)))))
    for m in re.finditer(r"(?<![\d.,])(\d{1,4})\s*(?:d|dong|vnd)\b", text_plain):
        amounts.append((m.start(), float(m.group(1))))
    return [v for _, v in sorted(amounts)]


def parse_price_range(text_plain: str) -> tuple[float | None, float | None]:
    lo = hi = None
    m = re.search(r"tu\s+(.+?)\s+(?:den|toi|-)\s+(.+?)(?:$|\s(?:cho|nhe|a)\b)", text_plain)
    if m:
        a, b = parse_amounts(m.group(1)), parse_amounts(m.group(2))
        if a and b:
            return a[0], b[0]
    m = re.search(r"(duoi|nho hon|khong qua|toi da|re hon|<)\s*(.+)", text_plain)
    if m and (a := parse_amounts(m.group(2))):
        hi = a[0]
    m = re.search(r"(tren|lon hon|it nhat|toi thieu|>)\s*(.+)", text_plain)
    if m and (a := parse_amounts(m.group(2))):
        lo = a[0]
    return lo, hi


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


def detect_product_phrase(u: str) -> str | None:
    """Cụm sản phẩm nội thất dài nhất xuất hiện (trả dạng có dấu)."""
    best = None
    for key, val in FURNITURE_CANONICAL_TERMS.items():
        if re.search(rf"\b{re.escape(key)}\b", u) and not key.startswith(("go ", "phong ")):
            if best is None or len(key) > len(best[0]):
                best = (key, val)
    if best:
        return best[1]
    for key, val in CATEGORY_WORDS.items():
        if re.search(rf"\b{key}\b", u):
            return val
    return None


def detect_material(u: str) -> str | None:
    return next((label for key, label in WOODS if re.search(rf"\b{key}\b", u)), None)


def detect_city(u: str) -> str | None:
    return next((label for key, label in CITIES.items() if re.search(rf"\b{key}\b", u)), None)


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
        amounts = parse_amounts(p)
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
        amounts = parse_amounts(p)
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


# ---------------------------------------------------------------- main
class RulePlanner:
    name = "rules"

    def plan(self, message: str, conversation: ConversationContext, has_location: bool = False,
             today: date | None = None) -> Plan:
        today = today or datetime.now(VN_TZ).date()
        raw = (message or "").strip()
        p = plain(raw)
        norm = normalize_vietnamese_chat(raw)
        u = norm["normalized_input"]
        plan = Plan()
        if any(_has(rx, p) for rx in INJECTION):
            plan.flags.add("injection_suspected")

        m = CODE_RE.match(p)
        if m:
            plan.confirm, plan.confirm_code = True, (m.group(1) or "").upper() or None
            return plan
        if CANCEL_RE.match(p):
            plan.cancel = True
            return plan
        if not u or (norm["is_gibberish"] and not extract_skus(raw)):
            plan.direct = "clarify"
            return plan
        if u in GREETINGS or p.strip(" !.?") in GREETINGS:
            plan.direct = "greeting"
            return plan

        skus = extract_skus(raw)
        mutation = parse_mutation(raw, u, p, skus, today)
        if mutation:
            plan.calls.append(mutation)
            return plan
        if any(_has(rx, p) for rx in OUT_OF_SCOPE):
            plan.direct = "out_of_scope"
            return plan
        if _has(r"\b(gio hang|them vao gio|xem gio)\b", u):
            plan.direct = "unsupported_cart"
            return plan

        uuid_m = UUID_RE.search(raw)
        if uuid_m and _has(r"\b(task|3d|mau 3d|thiet ke)\b", u):
            plan.calls.append(ToolCall("get_design_task_status", {"task_id": uuid_m.group(0)}))
            return plan
        if _has(r"\bxuong\b", u) and _has(r"\b(gan|o dau|tim|nao|gia cong|dat lam)\b", u):
            plan.calls.append(ToolCall("find_nearby_workshops", {}))
            return plan
        if _has(r"\bso sanh\b", u) and len(skus) >= 2:
            plan.calls.append(ToolCall("compare_products", {"skus": skus[:3]}))
            return plan

        product_ref = self._product_ref(skus, conversation, u)
        if _has(r"\b(con bao nhieu|ton kho|con hang|het hang|con khong|so luong|con may)\b", u) and product_ref is not None:
            plan.calls.append(ToolCall("get_inventory", product_ref))
            return plan

        fields = []
        if _has(r"\b(gio mo cua|mo cua|dong cua|gio lam viec|gio hoat dong|may gio)\b", u):
            fields.append("opening_hours")
        if _has(r"\b(hotline|so dien thoai|sdt|lien he|tong dai)\b", u):
            fields += ["hotline", "email"]
        if _has(r"\bemail\b", u):
            fields.append("email")
        if _has(r"\bdia chi\b", u) and not _has(r"\b(chi nhanh|showroom)\b", u):
            fields.append("address")
        if fields and not skus:
            plan.calls.append(ToolCall("get_store_info", {"fields": list(dict.fromkeys(fields))}))
            return plan
        if _has(r"\b(chi nhanh|showroom|cua hang (o dau|nao)|co cua hang)\b", u):
            city = detect_city(u)
            plan.calls.append(ToolCall("list_branches", {"city": city} if city else {}))
            return plan

        for ptype, rx in POLICY_KEYWORDS:
            if _has(rf"\b({rx})", u) and not skus:
                plan.calls.append(ToolCall("get_policy", {"policy_type": ptype}))
                return plan
        if _has(r"\b(chinh sach|quy dinh)\b", u):
            plan.calls.append(ToolCall("search_knowledge", {"query": raw[:300], "kinds": ["policy"]}))
            return plan

        if _has(r"\b(khuyen mai|giam gia|voucher|ma giam|uu dai|sale|campaign|chuong trinh)\b", u):
            args: dict[str, Any] = {}
            code = next((c for c in re.findall(r"\b[A-Z][A-Z0-9_-]{2,29}\b", raw) if any(ch.isdigit() for ch in c)), None)
            if code and code not in skus:
                args["code"] = code
            elif (phrase := detect_product_phrase(u)):
                args["category"] = phrase
            plan.calls.append(ToolCall("get_promotions", args))
            return plan

        if _has(r"\b(danh muc|loai san pham)\b", u) and _has(r"\b(nao|gi|nhung|liet ke|co)\b", u):
            plan.calls.append(ToolCall("list_taxonomy", {"kind": "categories"}))
            return plan
        if _has(r"\b(chat lieu|vat lieu) (nao|gi)\b|\bnhung (chat lieu|vat lieu)\b", u) and not detect_product_phrase(u):
            plan.calls.append(ToolCall("list_taxonomy", {"kind": "materials"}))
            return plan

        if skus:
            plan.calls.append(ToolCall("get_product", {"sku": skus[0]}))
            return plan
        if _has(r"\b(cai nay|san pham nay|mau nay|no)\b", u) and conversation.last_product_id:
            plan.calls.append(ToolCall("get_product", {}))
            return plan

        if _has(r"\b(huong dan|cach|lam sao|lam the nao|tai khoan|dang ky|dang nhap|mat khau|theo doi don|dat lam|3d|tinh nang|faq)\b", u):
            plan.calls.append(ToolCall("search_knowledge", {"query": raw[:300], "kinds": ["faq", "guide"]}))
            return plan

        phrase = detect_product_phrase(u)
        material = detect_material(u)
        lo, hi = parse_price_range(p)
        if phrase or material or lo is not None or hi is not None:
            args = {"keyword": phrase, "material": material, "min_price": lo, "max_price": hi}
            if _has(r"\b(con hang|san co)\b", u):
                args["available_only"] = True
            plan.calls.append(ToolCall("search_products", {k: v for k, v in args.items() if v is not None}))
            return plan
        if _has(r"\b(cai nay|san pham nay|mau nay)\b", u):
            plan.direct = "clarify"
            plan.note = "ambiguous_product"
            return plan
        if len(u.split()) >= 3 and _has(r"\b(la gi|nhu the nao|bao lau|khong|nao|sao|gi)\b|\?", u + (" ?" if "?" in raw else "")):
            plan.calls.append(ToolCall("search_knowledge", {"query": raw[:300]}))
            return plan
        plan.direct = "clarify"
        return plan

    @staticmethod
    def _product_ref(skus: list[str], conversation: ConversationContext, u: str) -> dict[str, Any] | None:
        if skus:
            return {"sku": skus[0]}
        phrase = detect_product_phrase(u)
        if phrase and not _has(r"\b(cai nay|san pham nay|mau nay|no)\b", u):
            return {"name": phrase}
        if conversation.last_product_id:
            return {}
        return None
