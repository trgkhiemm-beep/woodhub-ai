import re
import json
import logging
from app.core.config import settings
from app.services.input_normalizer import normalize_input, is_gibberish
from app.services.bedrock_service import BedrockService

logger = logging.getLogger("woodhub.classifier")

IN_SCOPE_FURNITURE_TERMS = [
    "ban", "bàn", "ghe", "ghế", "tu", "tủ", "giuong", "giường", "sofa", "ke", "kệ",
    "bo ban an", "bộ bàn ăn", "ban hoc", "bàn học", "ban trang diem", "bàn trang điểm",
    "ban lam viec", "bàn làm việc", "tu quan ao", "tủ quần áo", "ke tivi", "kệ tivi",
    "tu sach", "tủ sách", "ke sach", "kệ sách", "truong ky", "trường kỷ", "salon",
    "go", "gỗ", "go soi", "gỗ sồi", "go oc cho", "gỗ óc chó", "tan bi", "tần bì",
    "go thong", "gỗ thông", "go cao su", "gỗ cao su", "mdf", "mfc", "plywood", "go cong nghiep",
    "noi that", "nội thất", "phong khach", "phòng khách", "phong ngu", "phòng ngủ",
    "phong an", "phòng ăn", "showrum", "showroom", "xưởng", "xuong", "cua hang", "cửa hàng",
    "woodhub", "gio hang", "giỏ hàng", "dat lam", "đặt làm", "custom"
]

OUT_OF_SCOPE_PATTERNS = [
    r"\bthoi tiet\b", r"\bthời tiết\b", r"\bbitcoin\b", r"\bcoin\b", r"\bworld cup\b",
    r"\bbong da\b", r"\bbóng đá\b", r"\blap trinh\b", r"\blập trình\b", r"\bpython\b",
    r"\bjavascript\b", r"\bviet code\b", r"\bviết code\b", r"\bviet bai van\b", r"\bviết bài văn\b",
    r"\bgiai phuong trinh\b", r"\bgiải phương trình\b", r"\btin tuc\b", r"\btin tức\b",
    r"\bsuc khoe\b", r"\bsức khỏe\b", r"\bphap luat\b", r"\bpháp luật\b", r"\bchinh tri\b",
    r"\bchính trị\b", r"\bdu lich\b", r"\bdu lịch\b", r"\bnau an\b", r"\bnấu ăn\b",
    r"\btong thong\b", r"\btổng thống\b", r"\bbau cu\b", r"\bbầu cử\b", r"\bthe thao\b", r"\bthể thao\b"
]

AMBIGUOUS_PATTERNS = [
    r"^cái này", r"^mẫu này", r"^sản phẩm này", r"^nó\b", r"^cái nay", r"^mau nay", r"^san pham nay",
    r"giá bao nhiêu$", r"bao nhiêu tiền$", r"bao nhieu tien$", r"có màu trắng không$", r"co mau trang khong$",
    r"^bàn này", r"^tủ này", r"^giường này", r"^ghế này", r"^ban nay", r"^tu nay", r"^giuong nay", r"^ghe nay"
]

class ScopeClassifier:
    def __init__(self):
        self.ai_service = BedrockService()

    async def classify(self, query: str, context_has_product: bool = False) -> dict:
        norm = normalize_input(query)
        cleaned = norm["cleaned"]
        unaccented = norm["unaccented"]

        # 1. UNKNOWN check (gibberish / meaningless text like 'xyz abc 123')
        if is_gibberish(cleaned):
            return {
                "scope": "UNKNOWN",
                "intent": None,
                "confidence": 0.99,
                "needs_clarification": True,
                "search_query": None
            }

        # 2. AMBIGUOUS check (e.g. "cái này giá bao nhiêu" without product context)
        is_ambiguous_phrase = any(re.search(pat, cleaned) or re.search(pat, unaccented) for pat in AMBIGUOUS_PATTERNS)
        if is_ambiguous_phrase and not context_has_product:
            # Check if there is a specific product name mentioned in the sentence
            # If no product name is present, it is AMBIGUOUS
            has_product_name = any(term in unaccented for term in ["go soi", "go oc cho", "go thong", "ban an", "tu quan ao", "ke tivi"])
            if not has_product_name:
                return {
                    "scope": "AMBIGUOUS",
                    "intent": "PRODUCT_PRICE",
                    "confidence": 0.90,
                    "needs_clarification": True,
                    "search_query": None
                }

        # 3. OUT OF SCOPE check (rule-based domain check)
        is_out_of_scope = any(re.search(pat, cleaned) or re.search(pat, unaccented) for pat in OUT_OF_SCOPE_PATTERNS)
        if is_out_of_scope:
            return {
                "scope": "OUT_OF_SCOPE",
                "intent": None,
                "confidence": 0.99,
                "needs_clarification": False,
                "search_query": None
            }

        # 4. IN SCOPE check (rule-based furniture check)
        has_furniture_term = any(term in unaccented for term in IN_SCOPE_FURNITURE_TERMS)
        if has_furniture_term:
            return {
                "scope": "IN_SCOPE",
                "intent": "PRODUCT_SEARCH",
                "confidence": 0.96,
                "needs_clarification": False,
                "search_query": {
                    "keyword": query,
                    "category": None,
                    "material": None,
                    "wood_type": None,
                    "min_price": None,
                    "max_price": None,
                    "color": None,
                    "requested_attribute": None
                }
            }

        # 5. LLM Fallback Classification (for unhandled phrasing or semantic evaluation)
        llm_res = await self.ai_service.classify_and_extract(query)
        if llm_res and isinstance(llm_res, dict):
            scope = llm_res.get("scope", "UNKNOWN")
            confidence = float(llm_res.get("confidence", 0.5))

            # Apply Confidence Policy (Section 28)
            if confidence >= settings.CONFIDENCE_IN_SCOPE_MIN:
                return llm_res
            elif confidence >= settings.CONFIDENCE_AMBIGUOUS_MIN:
                llm_res["scope"] = "AMBIGUOUS"
                llm_res["needs_clarification"] = True
                return llm_res
            else:
                llm_res["scope"] = "UNKNOWN"
                llm_res["needs_clarification"] = True
                return llm_res

        # Default fallback for completely unclassified non-furniture queries
        return {
            "scope": "OUT_OF_SCOPE",
            "intent": None,
            "confidence": 0.80,
            "needs_clarification": False,
            "search_query": None
        }

classifier = ScopeClassifier()
