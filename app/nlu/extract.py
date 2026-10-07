"""
Trích xuất entity DETERMINISTIC (độ chính xác cao) — nguồn duy nhất cho giá trị entity.
LLM chỉ đề xuất intent; giá trị (mã, tiền, số người, tham chiếu…) luôn lấy/đối chiếu ở đây.
"""
from __future__ import annotations

import re

from app.nlp.vietnamese import normalize_vietnamese_chat
from app.nlu.lexicon import CITIES, COMPACT_TERMS, LARGE_TERMS, POLICY_TERMS, Lexicon, fold
from app.nlu.schema import Entities

# Cụm che trước khi khớp danh mục (từ đa nghĩa khi viết không dấu).
_MASKS = [
    r"\btu van\b", r"\btu\s+\d(?![\d.,]*\s*(canh|ngan|tang|buong|cua|khoang)\b)",  # "từ 2tr…" ≠ "tủ 2 cánh"
r"\btu (nhien|anh|dong|choi|do|lam|tao|chon|xa)\b",
    r"\btu (ngay|thang|luc|dau|khi|nay|hom|sang|chieu|toi)\b", r"\bke (hoach|toan|ca|chuyen|cho|ve|ra|lai|tu|ten)\b",
    r"\bban (oi|co|la|dang|muon|can|giup|cho (minh|toi|em)|nhe|a)\b", r"\b(cam on|chao|nho|hoi) ban\b", r"^ban\b(?= (oi|co|la)\b)",
    r"\bban hang\b", r"\bban chay\b",
    r"\b(nha cung cap|ncc|shop|nguoi|ai|ben|cua hang|noi|duoc) ban\b",  # "bán" (sell), không phải "bàn"
]
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,5})[\s\-_]?(\d{1,4}[A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*)(?![A-Za-z0-9])")
_NOT_CODE_PREFIX = {"mau", "cai", "so", "thu", "loai", "top", "ban", "ghe", "tu", "ke", "phong", "nguoi", "ng", "cho", "tang",
                    "tr", "trieu", "cu", "k", "m", "cm", "mm", "x", "gia", "duoi", "tren", "tam", "khoang", "den", "toi",
                    "giam", "size", "nam", "ngay", "thang", "lan", "kho", "ma", "under", "below", "over", "about",
                    "around", "than", "for", "max", "min", "only", "page", "step", "item", "option"}
_IDENT_RE = re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z]{2})"
                       r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+){2,}(?![A-Za-z0-9_-])")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _mask(folded: str) -> str:
    out = folded
    for rx in _MASKS:
        out = re.sub(rx, lambda m: "#" * len(m.group(0)), out)
    return out


def product_codes(raw: str) -> list[str]:
    """Mã model/SKU: 'KTV01', 'Oak-01', 'oak 01', 'BHS01C-M6'. Loại 'mẫu 2', '10tr', '6 người'…"""
    text = UUID_RE.sub(" ", raw or "")
    out = []
    # Định danh dài nối bằng '_'/'-' có chữ số ('TEST_NON_EXISTENT_PRODUCT_987654321', 'SKU-ABC-123') → một mã duy nhất
    for m in _IDENT_RE.finditer(text):
        out.append(m.group(0).upper())
    text = _IDENT_RE.sub(" ", text)
    for m in _CODE_RE.finditer(text):
        prefix, rest = m.group(1), m.group(2)
        if fold(prefix) in _NOT_CODE_PREFIX or re.fullmatch(r"\d+(k|tr|trieu|m|cu|d|nghin)", rest.lower()):
            continue  # "kệ sách 500k" là giá, không phải mã
        if re.fullmatch(r"\d+(tr|k|m|cm|mm|h|d|ng)?", rest.lower()) and len(rest) <= 2 and m.group(0)[len(prefix):len(prefix) + 1] == " ":
            # "oak 01" hợp lệ nhưng "size 2" không: yêu cầu số có ≥2 chữ số khi tách bằng khoảng trắng
            if not re.fullmatch(r"\d{2,}", rest):
                continue
        sep = "-" if re.search(r"[\s\-_]", m.group(0)[len(prefix):len(prefix) + 1] or "") else ""
        out.append(f"{prefix}{sep}{rest}".upper())
    return list(dict.fromkeys(out))


# Đơn vị triệu, kể cả lỗi gõ phổ biến: triệu/trịu/trẹo/trj/tr/củ, million; "m" chỉ khi kèm số lẻ ("2m5").
_MIL = r"(?:trieu|triu|treo|trj|tr|cu|million|millions|mil|mio|m(?=\s?\d))"
_NOT_UNIT_AFTER = r"(?!\s*(?:nguoi|ng|ghe|cho|cm|mm|m\b|met|%|trieu|triu|treo|trj|tr\b|cu\b|k\b|nghin|ngan|cai|chiec|sp|san pham))"
_MILLION_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*{_MIL}\s*(ruoi|(\d{{1,3}}){_NOT_UNIT_AFTER}(?:\s*(?:k|nghin|ngan))?)?(?![a-z0-9])")


def money(folded: str) -> list[float]:
    """Tiền VND từ văn bản đã fold (thường, không dấu).

    2,5 triệu | 2.5tr | 2tr5 | 2 triệu 5 | 2 triệu rưỡi | 2m5 | 2 trịu | 2 trẹo | 3 củ → triệu
    2 triệu 500 → 2.500.000 · 2 triệu 50 → 2.050.000 · 500k / 500 nghìn → 500.000 · 2.500.000 | 2500000 | 1000đ
    """
    folded = _IDENT_RE.sub(lambda m: " " * len(m.group(0)), folded)  # số trong mã định danh không phải tiền
    folded = re.sub(r"(?<=[a-z_])\d{5,}", lambda m: " " * len(m.group(0)), folded)  # 'ktv12345'
    vals: list[tuple[int, float]] = []
    taken: list[tuple[int, int]] = []
    matches = list(_MILLION_RE.finditer(folded))
    if any(not re.match(r"\d+(?:[.,]\d+)?\s*m(?![a-z])", m.group(0)) for m in matches) or \
            re.search(r"\d\s*(?:k|nghin|ngan)\b", folded):
        # đã có đơn vị tiền rõ ràng → "1m2", "1m6" là kích thước (mét), không phải tiền
        matches = [m for m in matches if not re.match(r"\d+(?:[.,]\d+)?\s*m(?![a-z])", m.group(0))]
    for m in matches:
        v = float(m.group(1).replace(",", ".")) * 1_000_000
        if m.group(2) == "ruoi":
            v += 500_000
        elif m.group(3):
            d = m.group(3)
            v += int(d) * (100_000 if len(d) == 1 else 1_000)  # "2tr5"=2,5tr · "2 triệu 50"=2.050.000
        vals.append((m.start(), v))
        taken.append(m.span())
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(nghin|ngan|k|thousand)(?![a-z])", folded):
        if m.group(2) == "ngan" and float(m.group(1).replace(",", ".")) < 10:
            continue  # "tủ 3 ngăn" (bỏ dấu trùng "3 ngàn"): không ai bán nội thất 3.000đ
        if not any(a <= m.start() < b for a, b in taken):
            vals.append((m.start(), float(m.group(1).replace(",", ".")) * 1_000))
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{5,})(?![\d.,]*\s*(?:trieu|tr|k|nghin|%|cm|mm))", folded):
        if not any(a <= m.start() < b for a, b in taken):
            vals.append((m.start(), float(re.sub(r"[.,]", "", m.group(1)))))
    for m in re.finditer(r"(?<![\d.,])(\d{1,4})\s*(?:d|dong|vnd)\b", folded):
        vals.append((m.start(), float(m.group(1))))
    return [v for _, v in sorted(vals)]


_MAX_PREFIX = r"(duoi|nho hon|khong qua|toi da|it hon|re hon|thap hon|under|below|less than|max|up to|<=?|tam|khoang|chung|co|around|about|do)"
_MIN_PREFIX = r"(tren|hon|lon hon|cao hon|it nhat|toi thieu|over|above|more than|from|>=?)"


_PRICE_M = re.compile(r"\b(duoi|tam|khoang|toi da|khong qua|re hon|gia|tren|hon|<|>)\s*(\d{1,3}(?:[.,]\d)?)\s*m\b(?!\s*\d)")
_SIZE_WORDS = r"\b(dai|rong|cao|sau|kich thuoc|kich co|chieu|size|met|mét|dien tich)\b"


def _price_m(folded: str) -> str:
    """'dưới 3m' (tiền tố giá, không có từ kích thước) → 'dưới 3tr'. 'dài dưới 2m' giữ nguyên (mét)."""
    if re.search(_SIZE_WORDS, folded):
        return folded
    return _PRICE_M.sub(lambda m: f"{m.group(1)} {m.group(2)}tr", folded)


def budget(folded: str) -> tuple[float | None, float | None]:
    """Ràng buộc giá: (min, max). 'từ 2tr đến 5tr', '2tr-5tr', 'dưới 2tr5', '< 3tr', 'tầm 3 củ', 'dưới 3m',
    '2 triệu trở xuống', '5 triệu trở lên', 'hơn 5 triệu'."""
    folded = _price_m(folded)
    m = re.search(r"\b(?:tu|from|between)\s+(.+?)\s*(?:den|toi|to|and|-|~)\s*(.+)", folded) or \
        re.search(r"(\d[\d.,]*\s*[a-z]*)\s*(?:-|~|den|toi)\s*(\d[\d.,]*\s*[a-z]*\d*)", folded)
    whole = money(folded)
    if m and (b := money(m.group(2))) and b[0] in whole:
        a = [x for x in money(m.group(1)) if x in whole]
        bare = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", m.group(1))
        if not a and bare:  # "từ 2 đến 5 triệu", "2-3tr": số đầu dùng chung đơn vị với số sau
            unit = 1_000_000 if b[0] >= 1_000_000 else 1_000 if b[0] >= 1_000 else 1
            a = [float(bare.group(1).replace(",", ".")) * unit]
        if a:
            return min(a[0], b[0]), max(a[0], b[0])
    amounts = money(folded)
    if not amounts:
        return None, None
    if re.search(r"\b(tro xuong|do lai|or less|or below)\b", folded):
        return None, amounts[0]
    if re.search(r"\b(tro len|or more|or above|\+)", folded):
        return amounts[0], None
    m = re.search(rf"(?:\b|^|\s){_MAX_PREFIX}\s*(.+)", folded)
    if m and (a := money(m.group(2))):
        return None, a[0]
    m = re.search(rf"(?:\b|^|\s){_MIN_PREFIX}\s*(.+)", folded)
    if m and (a := money(m.group(2))):
        return a[0], None
    if re.search(r"\b(ngan sach|budget|tai chinh|gia|tien|duoc khong|ko|khong|nao)\b", folded):
        return None, amounts[0]
    return None, None


def seats(folded: str) -> int | None:
    m = re.search(r"(\d{1,2})\s*(nguoi|ng|cho ngoi|cho|ghe|seats?|people|persons?)\b", folded)
    if m:
        return int(m.group(1))
    words = {"hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "sau": 6, "bay": 7, "tam": 8, "muoi": 10,
             "two": 2, "four": 4, "six": 6, "eight": 8}
    m = re.search(r"\b(hai|ba|bon|nam|sau|bay|tam|muoi|two|four|six|eight)\s+(nguoi|ng|ghe|people|seats?)\b", folded)
    return words[m.group(1)] if m else None


def ordinals(folded: str) -> list[int]:
    """'mẫu 2', 'cái thứ 3', 'mẫu 1 và 3', 'số 2', 'the second one'."""
    out: list[int] = []
    for m in re.finditer(r"\b(?:mau|cai|san pham|sp|so|loai|option|item|ket qua)\s*(?:thu|so)?\s*(\d{1,2})"
                         r"((?:\s*(?:va|,|voi|and|&)\s*(?:mau|cai|so)?\s*\d{1,2})*)\b(?!\s*(?:nguoi|ng|trieu|tr|cu|k|cm)\b|\s*%)", folded):
        out.append(int(m.group(1)))
        out += [int(x) for x in re.findall(r"\d{1,2}", m.group(2) or "")]
    words = {"dau tien": 1, "thu nhat": 1, "thu hai": 2, "thu ba": 3, "thu tu": 4, "first": 1, "second": 2, "third": 3}
    for k, v in words.items():
        if re.search(rf"\b(mau|cai|san pham|sp|option|the)\s+{k}\b|\b{k}\s+(mau|cai|one)\b|^{k}\b", folded):
            out.append(v)
    return [n for n in dict.fromkeys(out) if 1 <= n <= 20]


def reference(folded: str) -> str | None:
    return "current" if re.search(
        r"\b(cai nay|cai do|mau nay|mau do|san pham nay|sp nay|ban nay|ghe nay|tu nay|ke nay|giuong nay|no|con nay|"
        r"cai kia|mau kia|this one|that one|it)\b", folded) else None


def relative(folded: str) -> str | None:
    table = [("cheaper", r"\b(re hon|gia thap hon|mem hon|cheaper|less expensive|giam gia (duoc )?(khong|ko|k)|bot (duoc )?(khong|ko|k))\b"),
             ("more_expensive", r"\b(dat hon|cao cap hon|xin hon|more expensive|pricier)\b"),
             ("smaller", r"\b(nho hon|gon hon|nho gon hon|be hon|smaller|more compact)\b"),
             ("larger", r"\b(to hon|lon hon|rong hon|dai hon|bigger|larger)\b")]
    for name, rx in table:
        if re.search(rx, folded):
            return name
    return None


def size_pref(folded: str) -> str | None:
    if any(re.search(rf"\b{t}\b", folded) for t in COMPACT_TERMS):
        return "compact"
    if any(re.search(rf"\b{t}\b", folded) for t in LARGE_TERMS):
        return "large"
    return None


_ASKED = (
    ("price", r"\b(gia|bao tien|may tien|price|cost|how much)\b"),
    ("dimensions", r"\b(kich thuoc|kich co|size|chieu (dai|rong|cao)|dimensions?|bao to|bao lon)\b"),
    ("material", r"\b(chat lieu|vat lieu|lam bang|go gi|material)\b"),
    ("color", r"\b(mau sac|mau gi|mau nao|co mau|color|colour)\b"),
    ("description", r"\b(chi tiet|thong tin|mo ta|detail|details|info|dac diem)\b"),
)


_ITEM_RE = re.compile(r"^(?:shop |cua hang |ben (?:ban|minh|shop|em) )?(?:co ban|co)\s+([a-z0-9 ]{2,40}?)\s+"
                      r"(?:khong|ko|k|hem|hong|chua)\s*\??$")
_NOT_ITEM = re.compile(r"^(mau|loai|cai|san pham|sp|gi|ai|nao|mau nao|loai nao|gi moi|hang|do)$")


def asked_item(raw: str) -> str | None:
    """'Có đèn ngủ không?' → 'đèn ngủ' (giữ dấu để tìm theo tên). Món không có trong catalog → trả lời 'chưa cập nhật'."""
    text = (raw or "").strip()
    folded = fold(text)
    m = _ITEM_RE.search(folded)
    if not m or _NOT_ITEM.match(m.group(1).strip()) or len(folded) != len(text):
        return None
    return text[m.start(1):m.end(1)].strip()


_COUNT_NOUNS = r"(mau|cai|chiec|san pham|sp|bo|loai|lua chon|ban|ghe|tu|ke|giuong|sofa|den|guong)"


def wanted_count(folded: str) -> int | None:
    """'cho tôi 3 bàn', 'chọn giúp 3 mẫu bàn', 'top 5 ghế' → số sản phẩm muốn xem (không nhầm '6 người', '2 cánh', 'mẫu 2')."""
    m = re.search(rf"(?:^|\b(?:cho|chon|lay|goi y|tim|xem|top|liet ke|gioi thieu)\b[\w\s]{{0,12}}?)\b([1-9])\s+{_COUNT_NOUNS}\b", folded)
    if not m:
        return None
    rest = folded[m.end():m.end() + 12]
    if re.match(r"\s*(nguoi|ng|cho ngoi|canh|ngan|tang|met|m\b|cm)", rest):
        return None
    return int(m.group(1))


def asked_fields(folded: str) -> list[str]:
    """Trường người dùng hỏi về một sản phẩm → câu trả lời chỉ gồm trường đó."""
    out = [name for name, rx in _ASKED if re.search(rx, folded)]
    if not out and re.search(r"\b(bao nhieu|bn)\b", folded):
        out.append("price")  # "KTV01 bao nhiêu" = hỏi giá; "kích thước bao nhiêu" thì không
    return out


def extract(raw: str, lex: Lexicon) -> Entities:
    """Toàn bộ entity deterministic cho một tin nhắn."""
    norm = normalize_vietnamese_chat(raw)
    # dùng cả bản thô (giữ số, dấu chấm tiền) và bản đã giải teencode
    raw_folded = fold(raw)
    base = fold(norm["normalized_input"])
    masked = _mask(base)
    e = Entities()
    e.product_codes = product_codes(raw)
    e.budget_min, e.budget_max = budget(raw_folded)
    e.seats = seats(raw_folded)
    e.ordinals = ordinals(base)
    e.ordinal = e.ordinals[0] if e.ordinals else None
    e.reference = reference(base) if e.ordinal is None else None
    e.relative = relative(base)
    if e.relative is None:
        e.size = size_pref(base)
        if re.search(r"\b(gia re|re|binh dan|gia tot|tiet kiem|cheap|budget|affordable|gia mem)\b", base):
            e.price_pref = "low"
        elif re.search(r"\b(cao cap|sang trong|xin|premium|luxury|hang hieu)\b", base):
            e.price_pref = "high"
    code_spans = set()
    for c in e.product_codes:
        prefix = re.match(r"[A-Za-z]+", c)
        if prefix:
            code_spans |= {(m.start(), m.end()) for m in re.finditer(rf"(?<![a-z]){prefix.group(0).lower()}[\s\-_]?\d", masked)}
    e.amounts = money(_price_m(raw_folded))
    cat = lex.match(masked, "categories", exclude=code_spans)
    if cat:
        e.category = cat[0]
    mat = lex.match(masked, "materials", exclude=code_spans)
    if mat and not (cat and mat[1][0] >= cat[1][0] and mat[1][1] <= cat[1][1]):
        e.material = mat[0]
    col = lex.match(masked, "colors", exclude=({mat[1]} if mat else set()) | code_spans)
    if col and re.search(r"\b(mau|color|colour)\b", base):
        e.color = col[0]
    room = lex.match(masked, "rooms")
    e.room = room[0] if room else None
    style = lex.match(masked, "styles")
    sup = lex.match(raw_folded, "suppliers")
    e.supplier_name = sup[0] if sup else None
    e.style = style[0] if style else None
    e.city = next((v for k, v in CITIES.items() if re.search(rf"\b{k}\b", raw_folded)), None)
    e.policy_type = next((p for p, rx in POLICY_TERMS if re.search(rf"\b({rx})", base)), None)
    fields = []
    if re.search(r"\b(gio mo cua|mo cua|dong cua|gio lam viec|gio hoat dong|may gio|opening hours?|open)\b", base):
        fields.append("opening_hours")
    if re.search(r"\b(hotline|so dien thoai|sdt|dien thoai|lien he|tong dai|phone|contact)\b", base):
        fields += ["hotline", "email"]
    if re.search(r"\bemail\b", base):
        fields.append("email")
    if re.search(r"\b(dia chi|address)\b", base) or (
            re.search(r"\b(o dau|nam o|cho nao)\b", base) and not (e.city and not e.product_codes)  # "cửa hàng ở HCM ở đâu" = chi nhánh
            and (e.product_codes or re.search(r"\b(shop|cua hang|nha cung cap|ncc|ben ban|nguoi ban)\b", base))):
        fields.append("address")  # "Shop của bàn TB06 ở đâu?", "KTV01 bán ở đâu"
    e.store_fields = list(dict.fromkeys(fields))
    if u := UUID_RE.search(raw or ""):
        e.task_id = u.group(0)
    # nhà cung cấp: "shop này ở đâu", "liên hệ nhà cung cấp", "từ các nhà cung cấp khác nhau"
    if re.search(r"\b(shop|cua hang|nha cung cap|ncc|nha ban|ben ban|xuong|supplier|seller|nguoi ban)\s*(nay|do|kia|ay|cua (mau|san pham|cai) (nay|do))\b"
                 r"|\b(ai ban|ben nao ban|cua (shop|nha cung cap) nao|nha cung cap (nao|la ai))\b", base):
        e.supplier_ref = True
    if re.search(r"\b(nhieu|cac|khac nhau|moi)\s*(nha cung cap|ncc|shop|cua hang|ben)\b.*\b(khac nhau)?|"
                 r"\b(nha cung cap|ncc|shop|cua hang) khac nhau\b|\bdifferent (suppliers|sellers|shops)\b", base) \
            and re.search(r"\bkhac nhau\b|\bdifferent\b|\bmoi (nha cung cap|ncc|shop|ben)\b", base):
        e.distinct_suppliers = True
    if re.search(r"\b(danh muc|loai san pham|categories)\b", base):
        e.taxonomy_kind = "categories"
    elif re.search(r"\b(chat lieu|vat lieu|materials?)\s+(nao|gi|gom|co nhung|nhung)|\bnhung (chat lieu|vat lieu)\b", base):
        e.taxonomy_kind = "materials"
    m = re.search(r"\b(cho|de|dung cho|for)\s+(tre em|be|con|con nho|tre nho|hoc sinh|sinh vien|van phong|can ho nho|can ho|phong tro|gia dinh|lam viec|hoc tap|an uong)\b", base)
    if m:
        e.use_case = None if (e.room and fold(e.room) == m.group(2).strip()) else m.group(2).strip()
    if not (e.category or e.product_codes):
        e.product_name = asked_item(raw)
    e.count = wanted_count(masked)
    e.in_stock = bool(re.search(r"\b(con hang|co san hang|san hang|in stock)\b", base))
    e.asked = asked_fields(base)
    e.question = (raw or "").strip()[:300]
    return e


def is_english(raw: str) -> bool:
    words = re.findall(r"[a-zA-Z]+", raw or "")
    en = {"the", "a", "do", "you", "have", "what", "is", "price", "how", "much", "table", "chair", "under", "show", "me",
          "i", "need", "want", "any", "for", "with", "and", "shop", "store", "open", "please", "cheaper", "smaller"}
    return bool(words) and sum(w.lower() in en for w in words) >= max(2, len(words) // 3)
