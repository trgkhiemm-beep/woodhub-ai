"""
Mẫu nhận diện deterministic dùng chung: chào hỏi, dấu hiệu prompt-injection, yêu cầu THAY ĐỔI dữ liệu.

Agent chỉ đọc dữ liệu. Yêu cầu sửa/xóa/tạo dữ liệu (đổi giá, nhập kho, tạo khuyến mãi, SQL…) chỉ được NHẬN DIỆN
để trả lời rằng trợ lý không thay đổi dữ liệu — không có parser tham số, không có tool ghi.
"""
from __future__ import annotations

import re

from app.nlp.vietnamese import remove_vietnamese_diacritics

GREETINGS = {"chao", "xin chao", "chao shop", "shop oi", "hello", "hi", "alo", "chao ban", "co ai khong", "chao ad"}
INJECTION = [r"bo qua (moi |tat ca |cac )?(huong dan|quy tac|chi dan)", r"ignore (all |previous |the )*(instructions|rules)",
             r"system prompt", r"\bprompt he thong\b", r"(toi|minh|tao) la (admin|quan tri|chu (shop|cua hang))",
             r"\bban (bay gio )?la admin\b", r"developer mode", r"\bjailbreak\b", r"cap quyen admin"]

_DATA_FIELDS = (r"gia|mo ta|ton kho|so luong|danh muc|chat lieu|vat lieu|hotline|so dien thoai|email|dia chi|gio mo cua|"
                r"khuyen mai|voucher|ma giam|chuong trinh|campaign|faq|cau hoi thuong gap|ten san pham|san pham|bien the|sku")
CHANGE_REQUEST = [
    rf"\b(doi|sua|chinh|chinh sua|cap nhat|update|set|dat lai|thay)\b(?! tra\b).*\b({_DATA_FIELDS})\b",
    rf"\b(xoa|delete|remove)\b.*\b({_DATA_FIELDS})\b",
    r"\b(tao|them|lap|create|add)\b.*\b(khuyen mai|voucher|ma giam|campaign|chien dich|faq|cau hoi thuong gap|"
    r"danh muc|chat lieu|san pham moi|bien the)\b",
    r"\b(nhap them|nhap kho|tang ton|giam ton|dieu chinh ton|tru kho|cong kho)\b",
    r"\b(bat|tat|kich hoat|tam dung|ngung|mo lai)\b.*\b(khuyen mai|chuong trinh|campaign|voucher|ma giam)\b",
    r"\b(update|change|set|delete|remove|create|add)\b.*\b(price|stock|inventory|description|category|material|promotion|faq)\b",
    r"\b(insert into|delete from|drop table|update \w+ set|truncate)\b",
    r"^(an|go bo|go) (san pham|bien the|danh muc)\b",  # "ẩn/gỡ sản phẩm…" (không dùng 'an'/'go' trơn: trùng 'ăn', 'gỗ')
    r"\b(tang|giam|ha|nang|chinh) gia\b.*\b(len|xuong|thanh|ve|con)\b",  # "tăng giá TB06 lên 4 triệu"
    r"^\s*(xac nhan|confirm)\b[\s:]*[a-z0-9]{0,8}\s*$",  # xác nhận thay đổi: agent không còn tạo thay đổi nào
]
# "đổi trả", "thêm vào giỏ", "tạo mẫu 3D" là câu hỏi của khách, không phải yêu cầu sửa dữ liệu
_NOT_CHANGE = r"\b(doi tra|gio hang|vao gio|mau 3d|thiet ke 3d|tai khoan|mat khau|dang ky)\b"


def plain(text: str) -> str:
    return remove_vietnamese_diacritics((text or "").lower())


def is_change_request(text_plain: str) -> bool:
    if re.search(_NOT_CHANGE, text_plain):
        return False
    return any(re.search(rx, text_plain) for rx in CHANGE_REQUEST)


def injection_suspected(text_plain: str) -> bool:
    return any(re.search(rx, text_plain) for rx in INJECTION)
