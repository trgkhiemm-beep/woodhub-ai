import re
import json
import logging
from typing import Dict, Any, Optional
from app.core.config import settings
from app.services.input_normalizer import normalize_vietnamese_chat, remove_vietnamese_diacritics, is_gibberish
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
    "woodhub", "gio hang", "giỏ hàng", "dat lam", "đặt làm", "custom", "make up", "makeup"
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

# Patterns that are inherently ambiguous without product context (Section 7, 20)
AMBIGUOUS_PATTERNS = [
    r"^cái này", r"^mẫu này", r"^sản phẩm này", r"^nó\b", r"^cái nay", r"^mau nay", r"^san pham nay",
    r"^bàn này", r"^tủ này", r"^giường này", r"^ghế này", r"^ban nay", r"^tu nay", r"^giuong nay", r"^ghe nay",
    r"^bn nay", r"^co ban dep k$", r"^co ban dep ko$", r"^co ban dep khong$", r"^co ban dep hk$", r"^co ban dep hok$",
    r"^co cai nay k$", r"^co cai nay ko$", r"^co mau nay k$"
]

KNOWN_SPECIFIC_FURNITURE = [
    "ban trang diem", "bàn trang điểm", "ban an", "bàn ăn", "ban hoc", "bàn học",
    "ban lam viec", "bàn làm việc", "tu quan ao", "tủ quần áo", "ke tivi", "kệ tivi",
    "tu sach", "tủ sách", "ke sach", "kệ sách", "giuong ngu", "giường ngủ", "ban tra",
    "ghe sofa", "bo ban an", "bộ bàn ăn", "ban go", "bàn gỗ", "tu go", "tủ gỗ", "giuong go", "giường gỗ"
]

WOOD_TYPES_LIST = [
    ("sồi", "soi"), ("óc chó", "oc cho"), ("tần bì", "tan bi"),
    ("thông", "thong"), ("cao su", "cao su"), ("công nghiệp", "cong nghiep"), ("mdf", "mdf")
]

COLORS_LIST = [
    "trắng", "trang", "nâu", "nau", "đen", "den", "vàng", "vang", "tự nhiên", "tu nhien", "xám", "xam"
]


class ScopeClassifier:
    def __init__(self):
        self.ai_service = BedrockService()

    def _extract_entities(self, normalized_text: str, raw_text: str) -> dict:
        """
        Entity Extraction tách rời khỏi Intent (Mục 11).
        Trích xuất category, material, dimensions, price limits, color.
        Tuyệt đối không hallucinate/thêm thông tin người dùng không nói (Mục 8).
        """
        entities = {
            "keyword": None,
            "product_query": None,
            "category": None,
            "material": None,
            "wood_type": None,
            "dimensions": None,
            "min_price": None,
            "max_price": None,
            "color": None,
            "requested_attribute": None
        }

        text = normalized_text.lower()

        # 1. Product Query / Category Identification
        for specific in KNOWN_SPECIFIC_FURNITURE:
            unaccented_specific = remove_vietnamese_diacritics(specific)
            if unaccented_specific in text:
                entities["product_query"] = specific
                if "ban" in unaccented_specific: entities["category"] = "bàn"
                elif "tu" in unaccented_specific: entities["category"] = "tủ"
                elif "giuong" in unaccented_specific: entities["category"] = "giường"
                elif "ghe" in unaccented_specific or "sofa" in unaccented_specific: entities["category"] = "ghế"
                elif "ke" in unaccented_specific: entities["category"] = "kệ"
                break

        if not entities["product_query"]:
            # Check basic category
            for cat in ["bàn", "ban", "tủ", "tu", "giường", "giuong", "ghế", "ghe", "sofa", "kệ", "ke"]:
                if re.search(rf'\b{cat}\b', text):
                    canon_cat = "bàn" if cat in ["ban", "bàn"] else ("tủ" if cat in ["tu", "tủ"] else ("giường" if cat in ["giuong", "giường"] else ("ghế" if cat in ["ghe", "ghế", "sofa"] else "kệ")))
                    entities["category"] = canon_cat
                    entities["product_query"] = canon_cat
                    break

        # 2. Material / Wood Type
        for wood_vn, wood_raw in WOOD_TYPES_LIST:
            if wood_raw in text:
                entities["material"] = wood_vn
                entities["wood_type"] = wood_vn
                break

        # 3. Color
        for c in COLORS_LIST:
            if re.search(rf'\bmau {c}\b', text) or re.search(rf'\bmàu {c}\b', text):
                entities["color"] = c
                break

        # 4. Price bounds extraction (e.g. 'duoi 5 trieu' -> max_price: 5000000)
        max_p_match = re.search(r'(?:duoi|duới|dưới|<|nho hon)\s*(\d+(?:[.,]\d+)?)\s*(trieu|tr|m)?', text)
        if max_p_match:
            val = float(max_p_match.group(1).replace(",", "."))
            unit = max_p_match.group(2)
            if unit in ["trieu", "tr", "m"] or val < 100:
                entities["max_price"] = val * 1_000_000
            else:
                entities["max_price"] = val

        min_p_match = re.search(r'(?:tren|trên|>|lon hon|tu)\s*(\d+(?:[.,]\d+)?)\s*(trieu|tr|m)?', text)
        if min_p_match:
            val = float(min_p_match.group(1).replace(",", "."))
            unit = min_p_match.group(2)
            if unit in ["trieu", "tr", "m"] or val < 100:
                entities["min_price"] = val * 1_000_000
            else:
                entities["min_price"] = val

        # 5. Dimensions
        dim_match = re.search(r'\b(\d+(?:m\d+|\s*x\s*\d+(?:\s*x\s*\d+)?|\s*cm|\s*m))\b', text)
        if dim_match:
            entities["dimensions"] = dim_match.group(1)

        # 6. Requested Attribute (for specific attribute inquiries)
        if any(w in text for w in ["mau gi", "mau trang", "mau nau", "co mau", "mau sac", "màu"]):
            entities["requested_attribute"] = "color"
        elif any(w in text for w in ["kich thuoc", "size", "dai bao nhieu", "rong bao nhieu", "cao bao nhieu", "kích thước"]):
            entities["requested_attribute"] = "dimensions"
        elif any(w in text for w in ["gia bao nhieu", "bao nhieu tien", "gia ca", "giá"]):
            entities["requested_attribute"] = "price"
        elif any(w in text for w in ["go gi", "chat lieu", "chất liệu", "loai go"]):
            entities["requested_attribute"] = "material"

        # Refine search keyword:
        # If query has additional specific keywords (e.g. "siêu cấp vũ trụ 100m"), keep full text for accurate DB filtering
        entities["keyword"] = normalized_text

        return entities

    def _detect_intent(self, normalized_text: str) -> str:
        """Xác định intent từ normalized input theo ngữ nghĩa."""
        text = normalized_text.lower()
        
        # PRODUCT_PRICE
        if any(w in text for w in ["gia bao nhieu", "bao nhieu tien", "co gia", "gia ca", "gia sp", "bao nhieu"]):
            return "PRODUCT_PRICE"
            
        # PRODUCT_EXISTENCE (e.g. 'co ban trang diem k', 'co ban go khong', 'co sp ban go nao ko')
        if (text.startswith("co ") or text.startswith("có ") or " co " in text or " có " in text) and text.endswith("khong"):
            return "PRODUCT_EXISTENCE"
        if text.startswith("co ") or text.startswith("có "):
            return "PRODUCT_EXISTENCE"
            
        # PRODUCT_SPEC / DETAIL
        if any(w in text for w in ["kich thuoc", "chieu dai", "chieu rong", "chat lieu", "bao hanh", "thong so"]):
            return "PRODUCT_DETAIL"
            
        # STORE_LOCATION
        if any(w in text for w in ["o dau", "dia chi", "showroom", "cua hang", "chi nhanh", "gan day"]):
            return "STORE_LOCATION"
            
        # CART_ACTION
        if any(w in text for w in ["gio hang", "xem gio", "them vao gio", "mua ngay"]):
            return "CART_ACTION"
            
        # CUSTOM_3D
        if any(w in text for w in ["dat lam", "custom", "dong theo yeu cau"]) or (re.search(r'\d+\s*x\s*\d+\s*x\s*\d+', text)):
            return "CUSTOM_3D"
            
        # PRODUCT_SEARCH (Default in-scope search)
        return "PRODUCT_SEARCH"

    async def classify(self, query: str, context_has_product: bool = False) -> dict:
        """
        Phân loại toàn diện theo pipeline Chuẩn hóa Semantic & Confident Normalization Policy:
        - raw_input
        - normalized_input
        - scope: IN_SCOPE | OUT_OF_SCOPE | UNKNOWN | AMBIGUOUS
        - intent: PRODUCT_EXISTENCE | PRODUCT_SEARCH | PRODUCT_PRICE | PRODUCT_DETAIL | STORE_LOCATION | CART_ACTION | CUSTOM_3D | null
        - entities: dict
        - confidence: float
        """
        # 1. Pipeline Normalization
        norm_res = normalize_vietnamese_chat(query)
        raw_input = norm_res["raw_input"]
        normalized_input = norm_res["normalized_input"]
        cleaned_input = norm_res["cleaned_input"]
        unaccented_input = norm_res["unaccented_input"]

        # 2. IMPOSSIBLE / UNKNOWN check (gibberish / meaningless text like 'xyz abc 123', 'asd qwe zzz')
        if norm_res["is_gibberish"] or is_gibberish(cleaned_input) or is_gibberish(normalized_input):
            return {
                "raw_input": raw_input,
                "normalized_input": normalized_input,
                "scope": "UNKNOWN",
                "intent": None,
                "confidence": 0.99,
                "needs_clarification": True,
                "clarification_reason": "Chuỗi ký tự vô nghĩa hoặc không đủ dữ liệu để xác định.",
                "entities": {},
                "search_query": None
            }

        # 3. AMBIGUOUS check (e.g. "cái này giá bao nhiêu" or "co ban dep k" without product context)
        is_ambiguous_phrase = any(
            re.search(pat, cleaned_input) or re.search(pat, unaccented_input) or re.search(pat, normalized_input)
            for pat in AMBIGUOUS_PATTERNS
        )
        if is_ambiguous_phrase and not context_has_product:
            has_specific_product = any(
                term in normalized_input for term in [
                    "go soi", "go oc cho", "go thong", "ban an", "tu quan ao", "ke tivi",
                    "ban trang diem", "ban hoc", "ban lam viec", "giuong ngu"
                ]
            )
            if not has_specific_product:
                return {
                    "raw_input": raw_input,
                    "normalized_input": normalized_input,
                    "scope": "AMBIGUOUS",
                    "intent": "PRODUCT_SEARCH",
                    "confidence": 0.90,
                    "needs_clarification": True,
                    "clarification_reason": "Câu hỏi mơ hồ hoặc có nhiều cách hiểu khi chưa rõ đối tượng sản phẩm cụ thể.",
                    "entities": {},
                    "search_query": None
                }

        # 4. OUT OF SCOPE check (rule-based domain check)
        is_out_of_scope = any(
            re.search(pat, cleaned_input) or re.search(pat, unaccented_input) or re.search(pat, normalized_input)
            for pat in OUT_OF_SCOPE_PATTERNS
        )
        if is_out_of_scope:
            return {
                "raw_input": raw_input,
                "normalized_input": normalized_input,
                "scope": "OUT_OF_SCOPE",
                "intent": None,
                "confidence": 0.99,
                "needs_clarification": False,
                "clarification_reason": "Ngoài phạm vi tư vấn nội thất gỗ.",
                "entities": {},
                "search_query": None
            }

        # 5. IN SCOPE check (rule-based furniture terms check after semantic normalization)
        has_furniture_term = any(
            term in normalized_input or term in unaccented_input
            for term in IN_SCOPE_FURNITURE_TERMS
        )
        if has_furniture_term:
            intent = self._detect_intent(normalized_input)
            entities = self._extract_entities(normalized_input, raw_input)
            return {
                "raw_input": raw_input,
                "normalized_input": normalized_input,
                "scope": "IN_SCOPE",
                "intent": intent,
                "confidence": 0.96,
                "needs_clarification": False,
                "clarification_reason": None,
                "entities": entities,
                "search_query": entities
            }

        # 6. LLM Fallback Classification (for unhandled phrasing or semantic evaluation)
        llm_res = await self.ai_service.classify_and_extract(query)
        if llm_res and isinstance(llm_res, dict):
            scope = llm_res.get("scope", "UNKNOWN")
            confidence = float(llm_res.get("confidence", 0.5))

            # Attach normalized metadata
            llm_res["raw_input"] = raw_input
            llm_res["normalized_input"] = normalized_input

            # Apply Confidence Policy
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

        # 7. Default fallback for unclassified queries
        return {
            "raw_input": raw_input,
            "normalized_input": normalized_input,
            "scope": "OUT_OF_SCOPE",
            "intent": None,
            "confidence": 0.80,
            "needs_clarification": False,
            "clarification_reason": "Không tìm thấy ý định liên quan tới nội thất gỗ.",
            "entities": {},
            "search_query": None
        }

classifier = ScopeClassifier()
