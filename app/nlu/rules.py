"""
Phân loại intent dự phòng (khi LLM không khả dụng) + tách câu nhiều ý.

Dựa trên TÍN HIỆU từ entity đã trích xuất (mã sản phẩm, ngân sách, tham chiếu…) cộng một nhóm nhỏ
từ khóa hành vi — không cố liệt kê mọi cách nói. Intent chính do LLM quyết định khi có LLM.
"""
from __future__ import annotations

import re

from app.nlp.vietnamese import is_gibberish
from app.nlu.lexicon import fold
from app.nlu.patterns import GREETINGS
from app.nlu.schema import Entities, Intent

_ASK_RECOMMEND = r"\b(tu van|goi y|recommend|suggest|nen mua|nen chon|chon giup|chon dum|chon ho|dang mua|nen lay|phu hop|co mau nao|mau nao|loai nao|cai nao|muon mua|can mua|can tim|muon tim|minh can|toi can|em can|can (1|mot|mot cai|cai)|need|looking for|advise)\b"
_ASK_SEARCH = r"\b(tim|kiem|tim kiem|liet ke|show|find|search|xem cac|xem nhung|co nhung)\b"
_ASK_STOCK = r"\b(con hang|con khong|con ko|con k|con hem|con hong|het hang|ton kho|con bao nhieu|con may|so luong|in stock|available|con (1|mot) cai)\b|\bcon\s*\??$"
_ASK_PRICE = r"\b(gia|bao nhieu tien|bao tien|bn tien|price|cost|how much|may tien)\b|\bgia bn\b"
_ASK_DETAIL = r"\b(chi tiet|thong tin|kich thuoc|chat lieu|mau sac|mo ta|detail|size|dimension|lam bang gi)\b"
_ASK_COMPARE = r"\b(so sanh|khac nhau|compare|vs|hay hon|tot hon)\b"
_PROMO = r"\b(khuyen mai|giam gia|voucher|ma giam|coupon|uu dai|sale|campaign|chuong trinh|discount|promotion|deal)s?\b"
_GUIDE = r"\b(huong dan|cach|lam sao|lam the nao|tai khoan|dang ky|dang nhap|mat khau|theo doi don|dat lam|tinh nang|faq|how to|thiet ke 3d|mau 3d)\b"
_BRANCH = r"\b(chi nhanh|showroom|cua hang (o dau|nao|gan)|co cua hang|branch|store location|dia diem)\b"
_WORKSHOP = r"\b(xuong|workshop|tho moc|gia cong)\b"
_ORDER = (r"\b(don hang|don cua (toi|minh|em)|don dat|ma don|order)\b.*\b(den dau|toi dau|o dau|trang thai|giao chua|"
          r"chua giao|da giao|bao gio|tinh trang|status|sao roi|the nao|dang)\b|\b(kiem tra|tra cuu|xem|theo doi) (don|don hang|order)\b"
          r"|\border status\b|\b(don|don hang) (cua )?(toi|minh|em)\b")
_CART = r"\b(gio hang|them vao gio|xem gio|cart|thanh toan don)\b"
_SPLIT = re.compile(r"\s+(?:va|voi lai|them nua|con nua|and also|and|also)\s+|[;?]|"
                    r",\s+(?=(?:con|va|gia|chinh sach|co|giao hang|doi tra|bao hanh|thanh toan|hotline|gio mo cua|ship)\b)")


# ---------------------------------------------------------------- domain guard
# Chủ đề chắc chắn ngoài phạm vi cửa hàng (kiểm tra deterministic, KHÔNG gọi LLM).
OUT_OF_SCOPE = [
    r"\bthoi tiet\b", r"\bnhiet do\b", r"\bmua bao\b", r"\bweather\b", r"\bdu bao\b",
    r"\bchinh tri\b", r"\bbau cu\b", r"\btong thong\b", r"\bthu tuong\b", r"\bquoc hoi\b", r"\bdang phai\b", r"\bpresident\b",
    r"\btin tuc\b", r"\bthoi su\b", r"\bnews\b", r"\bbao chi\b",
    r"\bbong da\b", r"\bworld cup\b", r"\bthe thao\b", r"\bbong ro\b", r"\btennis\b", r"\bfootball\b", r"\bsoccer\b",
    r"\blich su\b(?! (don|mua|giao dich|thanh toan|dat|chat|tro chuyen|dang nhap))", r"\bchien tranh\b", r"\btrieu dai\b", r"\bhistory\b",
    r"\bgiai phuong trinh\b", r"\btich phan\b", r"\bdao ham\b", r"\bbai toan\b", r"\btoan hoc\b", r"\bmath\b",
    r"\blap trinh\b", r"\bviet code\b", r"\bpython\b", r"\bjavascript\b", r"\bjava\b", r"\bsql\b", r"\bprogramming\b",
    r"\bbitcoin\b", r"\bcrypto\b", r"\bchung khoan\b", r"\bco phieu\b", r"\bstock market\b",
    r"\bbai van\b", r"\blam tho\b", r"\bdich (sang|tieng)\b", r"\btranslate\b", r"\bnau an\b", r"\bcong thuc nau\b",
    r"\bdu lich\b", r"\bbenh vien\b", r"\bsuc khoe\b", r"\bbac si\b", r"\bphap luat\b", r"\bluat su\b",
    r"\bphim\b", r"\bca si\b", r"\bbai hat\b", r"\bgame\b",
    # hỏi về bản thân model
    r"\b(ban|may) la (ai|gi|model|gpt|chatgpt|gemini|claude|llm)\b", r"\bai tao ra (ban|may)\b",
    r"\b(model|mo hinh) (nao|gi)\b", r"\bwho (are|made|created) you\b", r"\bwhat model\b",
    r"\bsystem prompt\b", r"\bprompt (he thong|cua ban)\b", r"\bapi key\b",
]
# Yêu cầu ngoài phạm vi kể cả khi có nhắc tới nội thất ("dịch sang tiếng Anh: cái bàn", "viết code cho bàn").
HARD_OUT_OF_SCOPE = [r"\bdich (sang|tieng|cau|doan)\b", r"\btranslate\b", r"\bviet code\b", r"\bbai van\b", r"\blam tho\b",
                     r"\bke chuyen\b", r"\bchuyen cuoi\b", r"\bjoke\b", r"\bsystem prompt\b", r"\bprompt (he thong|cua ban)\b",
                     r"\bapi key\b"]
# Tín hiệu thuộc phạm vi cửa hàng (từ khóa hành vi/miền). Entity nội thất cũng là tín hiệu (xem in_domain).
_DOMAIN = (r"\b(shop|cua hang|woodhub|san pham|sp|noi that|do go|go|gia|bao nhieu|tien|mua|dat hang|don hang|dat lam|"
           r"giao|ship|van chuyen|doi tra|bao hanh|thanh toan|tra gop|voucher|khuyen mai|giam gia|uu dai|ma giam|"
           r"website|web|app|ung dung|tai khoan|dang ky|dang nhap|mat khau|3d|thiet ke|xuong|showroom|chi nhanh|"
           r"hotline|lien he|dia chi|mo cua|dong cua|gio|ton kho|con hang|het hang|tu van|goi y|mau|kich thuoc|chat lieu|"
           r"mau sac|faq|huong dan|chinh sach|danh muc|phong|lap dat|catalog|product|price|order|delivery|furniture|"
           r"discount|store|buy|stock|promotion|warranty|return|shipping|deal|nha cung cap|ncc|supplier|seller)s?\b")


def out_of_scope(folded: str, e: Entities | None = None) -> bool:
    """Ngoài phạm vi cửa hàng: yêu cầu cấm tuyệt đối, hoặc chủ đề ngoài lề không kèm sản phẩm/loại nội thất."""
    if any(re.search(rx, folded) for rx in HARD_OUT_OF_SCOPE):
        return True
    if e is not None and (e.product_codes or e.category):
        return False
    return any(re.search(rx, folded) for rx in OUT_OF_SCOPE)


def in_domain(folded: str, e: Entities) -> bool:
    """Có tín hiệu thuộc phạm vi cửa hàng hay không (entity nội thất / mã / tiền / tham chiếu / từ khóa miền)."""
    if e.product_codes or e.category or e.material or e.room or e.style or e.policy_type or e.store_fields \
            or e.task_id or e.supplier_name or e.supplier_ref or e.taxonomy_kind or e.seats or e.amounts or e.relative or e.size \
            or e.price_pref or e.ordinal or e.reference:
        return True
    return re.search(_DOMAIN, folded) is not None


def is_compare(folded: str) -> bool:
    return re.search(_ASK_COMPARE, folded) is not None


def split_compare(folded: str) -> list[str]:
    """Câu so sánh: không tách theo 'và' (vd 'so sánh A và B') nhưng vẫn tách ý sau 'rồi', 'sau đó', ';', '?'."""
    return [p.strip(" ,.!") for p in re.split(r"\s+(?:roi|sau do|then)\s+|[;?]", folded) if p.strip(" ,.!")] or [folded]


def split_clauses(folded: str) -> list[str]:
    parts = [p.strip(" ,.!") for p in _SPLIT.split(folded) if p and p.strip(" ,.!")]
    return parts or [folded]


def classify(folded: str, e: Entities, *, has_context: bool = False) -> Intent:
    return classify_conf(folded, e, has_context=has_context)[0]


def classify_conf(folded: str, e: Entities, *, has_context: bool = False) -> tuple[Intent, bool]:
    """(intent, tự_tin). Không tự tin → engine mới hỏi LLM (tiết kiệm token: phần lớn câu không cần LLM)."""
    t = folded
    if not t.strip():
        return Intent.UNCLEAR, True
    if t.strip(" !.?") in GREETINGS:
        return Intent.GREETING, True
    if re.search(r"^((cam on|thank you|thanks|thank|ok|oke|okay|vang|da|nhe|nha|a|ban|shop|nhieu)\b[\s!.,]*)+$", t):
        return Intent.GREETING, True
    if out_of_scope(t, e):
        return Intent.OUT_OF_SCOPE, True
    if re.search(_CART, t):
        return Intent.CART, True
    if e.task_id and re.search(r"\b(task|3d|thiet ke|design)\b", t):
        return Intent.DESIGN_TASK, True
    if re.search(_WORKSHOP, t) and re.search(r"\b(gan|o dau|tim|nao|near|dat lam)\b", t):
        return Intent.WORKSHOP, True
    refers = bool(e.product_codes or e.reference or e.ordinal)
    many = len(e.product_codes) >= 2 or len(e.ordinals) >= 2
    asks_which = many and re.search(r"\b(nao|which|hay|vs|voi)\b", t)
    if (re.search(_ASK_COMPARE, t) and (many or e.ordinal or e.reference)) or asks_which:
        return Intent.COMPARE, True
    if re.search(_ASK_STOCK, t) and (refers or has_context or e.category):
        # "tìm bàn dưới 3tr còn hàng" = TÌM KIẾM có điều kiện còn hàng (không có sản phẩm cụ thể) — không phải hỏi tồn kho
        if e.category and not refers and (e.budget_max or e.budget_min or e.material or e.supplier_name or re.search(_ASK_SEARCH, t)):
            return Intent.PRODUCT_SEARCH, True
        return Intent.INVENTORY, True
    if re.search(_ORDER, t) and not e.product_codes and not re.search(r"\b(huong dan|lam sao|lam the nao|cach)\b", t):
        return Intent.ORDER_STATUS, True
    # Liên hệ / khu vực của NHÀ CUNG CẤP (theo tên, mã sản phẩm hoặc ngữ cảnh). Hỏi chính sách → nhánh POLICY bên dưới.
    # "Shop của bàn TB06 ở đâu?": có mã sản phẩm → loại sản phẩm trong câu chỉ để gọi tên, không phải đang tìm sản phẩm
    asks_product = re.search(_ASK_PRICE, t) or re.search(_ASK_STOCK, t) or (e.category and not e.product_codes)
    names_supplier = re.search(r"\b(nha cung cap|ncc|supplier|seller|ben ban|ai ban|shop ban|nguoi ban|shop cua|cua hang cua)\b", t)
    if (e.store_fields or e.supplier_ref or (e.supplier_name and re.search(r"\b(o dau|lien he|thong tin|la ai|ban gi)\b", t))
            or (names_supplier and e.product_codes)) \
            and not asks_product and not e.policy_type and (not e.product_codes or names_supplier or e.store_fields):
        return Intent.SUPPLIER_INFO, True
    if re.search(_BRANCH, t) or (e.city and re.search(r"\b(cua hang|shop|store|chi nhanh|showroom)\b", t)):
        return Intent.BRANCHES, True
    if e.policy_type and not e.product_codes:
        return Intent.POLICY, True
    if re.search(r"\b(chinh sach|quy dinh|policy)\b", t):
        return Intent.POLICY, True
    if re.search(_PROMO, t) and not re.search(r"\b(re hon|giam gia (duoc )?(khong|ko|k))\b", t):
        return Intent.PROMOTION, True
    if e.taxonomy_kind and not e.product_codes:
        return Intent.TAXONOMY, True
    if re.search(r"\b(huong dan|lam sao|lam the nao|how to|cach (tao|dat|dang|theo doi|doi|dung|su dung|huy))\b", t) and not e.product_codes:
        return Intent.GUIDE_FAQ, True
    if e.product_codes and not e.relative:
        return Intent.PRODUCT_DETAIL, True
    if e.relative:
        return Intent.RECOMMEND, True
    if (e.reference or e.ordinal) and (re.search(_ASK_PRICE, t) or re.search(_ASK_DETAIL, t) or has_context):
        return Intent.PRODUCT_DETAIL, True
    # TÌM KIẾM ("bàn dưới 3 củ", "tìm bàn dưới 3tr", "có ghế gỗ không") = lọc deterministic, liệt kê theo giá.
    # TƯ VẤN chỉ khi có tín hiệu cần xếp hạng theo nhu cầu ("gợi ý", "chọn giúp", "nên mua", số người, nhỏ gọn, mục đích…).
    if re.search(_ASK_RECOMMEND, t) or e.seats or e.size or e.use_case or e.price_pref:
        return Intent.RECOMMEND, bool(e.category or e.seats or e.size or e.price_pref or e.use_case)
    if e.category or e.material or e.budget_max or e.budget_min or e.room or e.style:
        return Intent.PRODUCT_SEARCH, bool(e.category or e.material)
    if re.search(_GUIDE, t):
        return Intent.GUIDE_FAQ, True
    if e.product_name and not re.search(_DOMAIN, fold(e.product_name)):
        return Intent.PRODUCT_SEARCH, True  # "có đèn ngủ không" → tra theo tên trên catalog thật
    if not in_domain(t, e) and not has_context:
        # không có tín hiệu thuộc cửa hàng: chuỗi vô nghĩa → hỏi lại; còn lại → ngoài phạm vi
        if is_gibberish(t):
            return Intent.UNCLEAR, True
        return Intent.OUT_OF_SCOPE, len(t.split()) <= 12
    if len(t.split()) >= 3 and re.search(r"\b(la gi|nhu the nao|bao lau|khong|ko|nao|sao|gi|what|how|when)\b", t):
        return Intent.GUIDE_FAQ, False
    return Intent.UNCLEAR, False
