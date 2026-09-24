import re
import unicodedata

def remove_vietnamese_diacritics(text: str) -> str:
    """
    Chuyển đổi tiếng Việt có dấu thành không dấu.
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

def normalize_input(text: str) -> dict:
    """
    Chuẩn hóa đầu vào của người dùng.
    Trả về dict chứa:
    - original: Chuỗi gốc
    - cleaned: Chuỗi đã trim và viết thường
    - unaccented: Chuỗi không dấu đã viết thường
    """
    original = text or ""
    trimmed = original.strip()
    # Loại bỏ dấu câu thừa (giữ lại khoảng trắng)
    cleaned = re.sub(r'[.,!?;\:\"]+', ' ', trimmed.lower())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    unaccented = remove_vietnamese_diacritics(cleaned)
    
    return {
        "original": original,
        "cleaned": cleaned,
        "unaccented": unaccented
    }

def is_gibberish(cleaned_text: str) -> bool:
    """
    Kiểm tra xem câu thoại có phải chuỗi rác/không có nghĩa hay không (ví dụ: 'xyz abc 123').
    """
    if not cleaned_text:
        return True
    
    # Chuỗi ngắn quá hoặc chứa các từ rác ngẫu nhiên không có nguyên âm
    words = cleaned_text.split()
    if len(words) == 1 and len(cleaned_text) > 4 and not re.search(r'[aeiouyàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ]', cleaned_text):
        return True

    # Check pattern như 'xyz abc 123', 'asdfgh', '123456'
    if re.fullmatch(r'^(?:[a-z]{3,}\s+)*[a-z]{3,}\s+\d+$', cleaned_text):
        # Ví dụ xyz abc 123
        vowels = re.findall(r'[aeiouy]', cleaned_text)
        if len(vowels) < 2:
            return True

    return False
