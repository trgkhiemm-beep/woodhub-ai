import re
import unicodedata
from typing import Dict, Any, List, Tuple, Optional

# --- 1. DIACRITIC REMOVAL UTILITY ---
def remove_vietnamese_diacritics(text: str) -> str:
    """
    Chuyển đổi tiếng Việt có dấu thành không dấu chuẩn xác.
    Ví dụ: 'Bàn ăn gỗ sồi' -> 'ban an go soi'
    """
    if not text:
        return ""
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text)
    text = re.sub(r'[ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ]', 'A', text)
    text = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', text)
    text = re.sub(r'[ÈÉẸẺẼÊỀẾỆỂỄ]', 'E', text)
    text = re.sub(r'[ìíịỉĩ]', 'i', text)
    text = re.sub(r'[ÌÍỊỈĨ]', 'I', text)
    text = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', text)
    text = re.sub(r'[ÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ]', 'O', text)
    text = re.sub(r'[ùúụủũưừứựửữ]', 'u', text)
    text = re.sub(r'[ÙÚỤỦŨƯỪỨỰỬỮ]', 'U', text)
    text = re.sub(r'[ỳýỵỷỹ]', 'y', text)
    text = re.sub(r'[ỲÝỴỶỸ]', 'Y', text)
    text = re.sub(r'[đ]', 'd', text)
    text = re.sub(r'[Đ]', 'D', text)
    return text


# --- 2. NOISE PATTERNS & TEENCODE MAPPINGS ---

# Question particles (thường đứng cuối câu hoặc sau động từ)
QUESTION_PARTICLES = {
    "k", "ko", "kh", "khong", "hok", "hk", "hem", "hong", "hông", "k0", "kjo", "hơm", "hổng"
}

# Conversational fillers & emoticons to strip during token analysis
CONVERSATIONAL_FILLERS = {
    "ah", "ạ", "ơi", "oi", "shop", "shop ơi", "shop oi", "nha", "nhé", "nhe", "ha", "hen", "nè", "ne",
    "ad", "admin", "nhe shop", "nha shop", "ạ shop", "a shop", "với", "voi", "giúp", "giup", "mình với",
    "cho e", "cho em", "cho anh", "cho c", "cho chị"
}

EMOTICON_PATTERNS = [
    r':\)+', r':\(+', r':\-?\)+', r':\-?\(+', r'\^\^+', r':v', r':3', r':D', r'xD', r'<3',
    r'hihi', r'haha', r'hehe', r'huhu', r'keke', r'hic'
]

# Furniture domain vocabulary for typo and teencode expansion
FURNITURE_CANONICAL_TERMS = {
    # Bàn & các loại bàn
    "ban trang diem": "bàn trang điểm",
    "ban trag diem": "bàn trang điểm",
    "ban trg diem": "bàn trang điểm",
    "ban td": "bàn trang điểm",
    "ban make up": "bàn trang điểm",
    "ban makeup": "bàn trang điểm",
    "cai ban make up": "bàn trang điểm",
    "cai ban makeup": "bàn trang điểm",
    "ban an": "bàn ăn",
    "ban hoc": "bàn học",
    "ban lam viec": "bàn làm việc",
    "ban tra": "bàn trà",
    "ban sofa": "bàn sofa",
    "ban phong khach": "bàn phòng khách",
    "ban phong khahc": "bàn phòng khách",
    "ban go": "bàn gỗ",
    
    # Tủ & các loại tủ
    "tu quan ao": "tủ quần áo",
    "tu quan aoo": "tủ quần áo",
    "tu qao": "tủ quần áo",
    "tu q ao": "tủ quần áo",
    "tu sach": "tủ sách",
    "tu go": "tủ gỗ",
    "tu dau giuong": "tủ đầu giường",
    "tu giay": "tủ giày",
    
    # Giường
    "giuong ngu": "giường ngủ",
    "giuog ngu": "giường ngủ",
    "giuong go": "giường gỗ",
    
    # Kệ
    "ke tivi": "kệ tivi",
    "ke tv": "kệ tivi",
    "ke sach": "kệ sách",
    "ke go": "kệ gỗ",
    
    # Ghế & Sofa
    "ghe an": "ghế ăn",
    "ghe sofa": "ghế sofa",
    "ghe go": "ghế gỗ",
    "sofa phong khach": "sofa phòng khách",
    
    # Loại gỗ
    "go soi": "gỗ sồi",
    "go oc cho": "gỗ óc chó",
    "go occho": "gỗ óc chó",
    "go tan bi": "gỗ tần bì",
    "go thong": "gỗ thông",
    "go cao su": "gỗ cao su",
    "go caosu": "gỗ cao su",
    "go cong nghiep": "gỗ công nghiệp",
    "go mdf": "gỗ mdf",
    
    # Không gian
    "phong khach": "phòng khách",
    "phong khahc": "phòng khách",
    "phong ngu": "phòng ngủ",
    "phong an": "phòng ăn"
}

# Context-dependent word replacements
SINGLE_WORD_TYPOS = {
    "trag": "trang",
    "khahc": "khach",
    "aoo": "ao",
    "diemm": "diem",
    "phog": "phong",
    "giuog": "giuong",
    "sofaa": "sofa",
    "guog": "guong",
    "tuu": "tu",
    "sanpham": "san pham",
    "sp": "san pham",
    "ib": "inbox",
    "mik": "minh",
    "dc": "duoc",
    "đc": "duoc"
}


# --- 3. NORMALIZATION PIPELINE STAGES ---

def normalize_repeated_chars(text: str) -> str:
    """
    Giảm các ký tự lặp kéo dài nhưng giữ nguyên tính hợp lệ.
    Ví dụ: 'cooooo' -> 'co', 'khongggg' -> 'khong', 'khôôông' -> 'không', 'ban trang diemm' -> 'ban trang diem'
    """
    if not text:
        return ""
    
    # Rút gọn 3 ký tự liên tiếp giống nhau thành 1 ký tự
    text = re.sub(r'([a-zA-Zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ])\1{2,}', r'\1', text, flags=re.IGNORECASE)
    
    # Xử lý các từ lặp đuôi phổ biến (như kkk -> k, ooo -> o, nnn -> n, mmm -> m)
    tokens = text.split()
    normalized_tokens = []
    for t in tokens:
        # Nếu token kết thúc bằng 2 ký tự lặp không tự nhiên (ví dụ: diemm -> diem, gooo -> go, koo -> ko)
        t_cleaned = re.sub(r'([bcdfghjklmnpqrstvwxyz])\1+$', r'\1', t, flags=re.IGNORECASE)
        t_cleaned = re.sub(r'([aeiouyàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ])\1+$', r'\1', t_cleaned, flags=re.IGNORECASE)
        normalized_tokens.append(t_cleaned)
        
    return " ".join(normalized_tokens)


def strip_emoticons_and_noise(text: str) -> str:
    """Loại bỏ emoticons và dấu câu lặp rác."""
    if not text:
        return ""
    res = text
    for pat in EMOTICON_PATTERNS:
        res = re.sub(pat, ' ', res, flags=re.IGNORECASE)
    
    # Thay thế dấu câu đặc biệt bằng khoảng trắng
    res = re.sub(r'[.,!?;\:\"\'\(\)\[\]\{\}\-_+=~`@#$%^&*\/\\|<>]+', ' ', res)
    return re.sub(r'\s+', ' ', res).strip()


def resolve_teencode_and_typos(cleaned_unaccented: str) -> Tuple[str, List[str]]:
    """
    Giải quyết teencode, viết tắt và typo dựa trên ngữ cảnh chuỗi không dấu.
    Trả về (normalized_string, detected_features)
    """
    words = cleaned_unaccented.split()
    if not words:
        return "", []

    features = []
    resolved_words = []
    n = len(words)
    i = 0

    while i < n:
        w = words[i]
        
        # 1. Check bigram / trigram teencode (ví dụ: 'ban td', 'tu qao', 'sp ban go', 'co bn trag diem')
        matched_phrase = False
        for phrase_len in (3, 2):
            if i + phrase_len <= n:
                candidate = " ".join(words[i:i+phrase_len])
                if candidate in FURNITURE_CANONICAL_TERMS:
                    resolved = remove_vietnamese_diacritics(FURNITURE_CANONICAL_TERMS[candidate])
                    resolved_words.append(resolved)
                    features.append(f"canonical_phrase:{candidate}->{resolved}")
                    i += phrase_len
                    matched_phrase = True
                    break
        if matched_phrase:
            continue

        # 2. Xử lý "bn" dựa trên ngữ cảnh
        if w == "bn":
            # Nếu từ tiếp theo là "trag", "trang", "an", "hoc", "go", "lam viec", "nay" trong ngữ cảnh nội thất -> "ban"
            if i + 1 < n and words[i+1] in ["trag", "trang", "trg", "td", "an", "hoc", "go", "lam", "sofa", "tra", "khach", "khahc"]:
                resolved_words.append("ban")
                features.append("contextual:bn->ban")
                i += 1
                continue
            # Nếu đứng đầu câu hỏi "bn co..." -> "ban" (bạn)
            elif i == 0 and n > 1 and words[1] in ["co", "oi", "ban"]:
                resolved_words.append("ban")
                features.append("contextual:bn->ban_prn")
                i += 1
                continue
            else:
                resolved_words.append("ban")
                i += 1
                continue

        # 3. Xử lý "td" đứng sau "ban" -> "trang diem"
        if w == "td":
            if resolved_words and resolved_words[-1] == "ban":
                resolved_words.append("trang diem")
                features.append("contextual:td->trang_diem")
                i += 1
                continue

        # 4. Xử lý "qao" / "q ao" -> "quan ao"
        if w == "qao":
            resolved_words.append("quan ao")
            features.append("teencode:qao->quan_ao")
            i += 1
            continue

        # 5. Xử lý từ hỏi không/ko ở cuối câu hoặc sau động từ
        if w in QUESTION_PARTICLES:
            # Nếu đứng cuối hoặc đứng sau vị ngữ/bổ ngữ
            resolved_words.append("khong")
            features.append(f"particle:{w}->khong")
            i += 1
            continue

        # 6. Single word typo dictionary
        if w in SINGLE_WORD_TYPOS:
            resolved = SINGLE_WORD_TYPOS[w]
            resolved_words.append(resolved)
            features.append(f"typo:{w}->{resolved}")
            i += 1
            continue

        resolved_words.append(w)
        i += 1

    normalized_str = " ".join(resolved_words)
    normalized_str = re.sub(r'\s+', ' ', normalized_str).strip()
    return normalized_str, features


def is_gibberish(cleaned_text: str) -> bool:
    """
    Kiểm tra xem câu thoại có phải chuỗi rác/không có nghĩa hay không (ví dụ: 'xyz abc 123', 'asd qwe zzz').
    """
    if not cleaned_text:
        return True
    
    text = cleaned_text.strip().lower()
    words = text.split()
    
    # Loại bỏ số
    non_numeric_words = [w for w in words if not w.isdigit()]
    if not non_numeric_words:
        return True

    # Check pattern như 'xyz abc 123', 'asd qwe zzz', 'jshdjs 123'
    vowels_accented = 'aeiouyàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ'
    
    # Đếm số từ không có nguyên âm hoặc có phụ âm bất thường liên tiếp
    invalid_words_count = 0
    for w in non_numeric_words:
        if len(w) >= 3 and not any(c in vowels_accented for c in w):
            invalid_words_count += 1
        elif re.search(r'[bcdfghjklmnpqrstvwxz]{4,}', w):
            invalid_words_count += 1

    if len(non_numeric_words) > 0 and (invalid_words_count / len(non_numeric_words)) >= 0.5:
        return True

    # Check random keyboard mashing patterns
    if re.fullmatch(r'^(?:[a-z]{3,}\s+)*[a-z]{3,}(?:\s+\d+)*$', text):
        all_vowels = [c for c in text if c in vowels_accented]
        if len(all_vowels) < 2 and len(text) > 6:
            return True

    return False


# --- 4. MAIN NORMALIZATION INTERFACE ---

def normalize_vietnamese_chat(raw_text: str) -> Dict[str, Any]:
    """
    Pipeline chuẩn hóa đầu vào toàn diện cho Chatbot tiếng Việt:
    RAW MESSAGE
        ↓
    Whitespace normalization
        ↓
    Case normalization
        ↓
    Repeated-character normalization
        ↓
    Punctuation & emoticon normalization
        ↓
    Vietnamese no-diacritic tolerant processing
        ↓
    Typo & teencode interpretation
        ↓
    Semantic token extraction
        ↓
    Gibberish / Confidence assessment
    """
    raw = raw_text or ""
    trimmed = raw.strip()
    
    # 1. Repeated-char & basic case
    de_repeated = normalize_repeated_chars(trimmed)
    
    # 2. Emoticon & noise stripping
    cleaned_with_accents = strip_emoticons_and_noise(de_repeated.lower())
    
    # 3. Unaccented version
    unaccented_raw = remove_vietnamese_diacritics(cleaned_with_accents)
    
    # 4. Resolve teencode & typos
    normalized_unaccented, features = resolve_teencode_and_typos(unaccented_raw)
    
    # 5. Check gibberish
    gibberish = is_gibberish(normalized_unaccented)
    
    # 6. Assess confidence status: CONFIDENT | UNCERTAIN | IMPOSSIBLE
    if gibberish:
        status = "IMPOSSIBLE"
        confidence = 0.99
    elif len(normalized_unaccented.split()) == 0:
        status = "IMPOSSIBLE"
        confidence = 0.99
    else:
        status = "CONFIDENT"
        confidence = 0.95

    return {
        "raw_input": raw,
        "cleaned_input": cleaned_with_accents,
        "unaccented_input": unaccented_raw,
        "normalized_input": normalized_unaccented,
        "features": features,
        "status": status,
        "is_gibberish": gibberish,
        "confidence": confidence
    }


# Giữ hàm normalize_input cũ để tương thích ngược với các module khác
def normalize_input(text: str) -> Dict[str, Any]:
    res = normalize_vietnamese_chat(text)
    return {
        "original": res["raw_input"],
        "cleaned": res["cleaned_input"],
        "unaccented": res["normalized_input"],
        "raw_unaccented": res["unaccented_input"],
        "status": res["status"],
        "is_gibberish": res["is_gibberish"]
    }
