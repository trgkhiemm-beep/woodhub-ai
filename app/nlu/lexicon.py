"""
Từ vựng miền nội thất: tiếng Việt có dấu, không dấu, teencode, tiếng Anh → dạng chuẩn có dấu.

Chỉ chứa TỪ VỰNG (không phải dữ liệu cửa hàng). Danh mục/chất liệu THẬT được nạp thêm lúc chạy
từ Backend (Lexicon.extend_from_catalog) để khớp đúng tên trong catalog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp.vietnamese import remove_vietnamese_diacritics


def fold(text: str) -> str:
    """Chuẩn hóa để so khớp: thường, bỏ dấu, gộp khoảng trắng."""
    return re.sub(r"\s+", " ", remove_vietnamese_diacritics((text or "").lower())).strip()


# Cụm dài được ưu tiên hơn cụm ngắn khi khớp.
CATEGORY_TERMS: dict[str, str] = {
    "ban an": "bàn ăn", "ban hoc": "bàn học", "ban lam viec": "bàn làm việc", "ban may tinh": "bàn máy tính",
    "ban trang diem": "bàn trang điểm", "ban tra": "bàn trà", "ban sofa": "bàn sofa", "ban nuoc": "bàn trà",
    "ban make up": "bàn trang điểm", "ban makeup": "bàn trang điểm", "ban td": "bàn trang điểm",
    "bo ban an": "bàn ăn", "ghe an": "ghế ăn", "ghe sofa": "sofa", "tu quan ao": "tủ quần áo", "tu ao": "tủ quần áo",
    "tu sach": "tủ sách", "tu giay": "tủ giày", "ke tivi": "kệ tivi", "ke tv": "kệ tivi", "ke sach": "kệ sách",
    "giuong ngu": "giường", "giuong": "giường", "ban": "bàn", "ghe": "ghế", "tu": "tủ", "ke": "kệ", "sofa": "sofa",
    "truong ky": "giường", "ke de sach": "kệ sách", "ke dung sach": "kệ sách", "tu de quan ao": "tủ quần áo",
    "tu dung quan ao": "tủ quần áo", "ban de may tinh": "bàn máy tính", "ke de tivi": "kệ tivi", "ke de tv": "kệ tivi",
    # English
    "dining table": "bàn ăn", "desk": "bàn làm việc", "study desk": "bàn học", "computer desk": "bàn máy tính",
    "coffee table": "bàn trà", "dressing table": "bàn trang điểm", "table": "bàn", "chair": "ghế",
    "dining chair": "ghế ăn", "wardrobe": "tủ quần áo", "cabinet": "tủ", "bookshelf": "kệ sách", "shelf": "kệ",
    "tv stand": "kệ tivi", "bed": "giường", "couch": "sofa",
}
MATERIAL_TERMS: dict[str, str] = {
    "go soi": "gỗ sồi", "soi": "gỗ sồi", "oak": "gỗ sồi", "go oc cho": "gỗ óc chó", "oc cho": "gỗ óc chó", "walnut": "gỗ óc chó",
    "go thong": "gỗ thông", "pine": "gỗ thông", "go cao su": "gỗ cao su", "cao su": "gỗ cao su", "rubberwood": "gỗ cao su",
    "go tan bi": "gỗ tần bì", "tan bi": "gỗ tần bì", "ash": "gỗ tần bì", "go cong nghiep": "gỗ công nghiệp",
    "mdf": "MDF", "mfc": "MFC", "melamine": "melamine", "go tu nhien": "gỗ tự nhiên", "solid wood": "gỗ tự nhiên",
    "khung sat": "khung sắt", "khung thep": "khung thép", "metal": "kim loại",
}
COLOR_TERMS: dict[str, str] = {
    "trang": "trắng", "white": "trắng", "den": "đen", "black": "đen", "nau": "nâu", "brown": "nâu", "xam": "xám",
    "grey": "xám", "gray": "xám", "vang": "vàng", "tu nhien": "tự nhiên", "natural": "tự nhiên", "van go": "vân gỗ",
    "oc cho": "óc chó", "xoan dao": "xoan đào", "nau do": "nâu đỏ", "canh gian": "cánh gián",
}
ROOM_TERMS: dict[str, str] = {
    "phong khach": "phòng khách", "living room": "phòng khách", "phong ngu": "phòng ngủ", "bedroom": "phòng ngủ",
    "phong an": "phòng ăn", "dining room": "phòng ăn", "phong lam viec": "phòng làm việc", "office": "phòng làm việc",
    "phong tre em": "phòng trẻ em", "kids room": "phòng trẻ em",
}
STYLE_TERMS: dict[str, str] = {
    "hien dai": "hiện đại", "modern": "hiện đại", "co dien": "cổ điển", "classic": "cổ điển", "toi gian": "tối giản",
    "minimalist": "tối giản", "scandinavian": "scandinavian", "scandi": "scandinavian", "bac au": "scandinavian",
    "vintage": "vintage", "indochine": "indochine", "dong duong": "indochine",
}
COMPACT_TERMS = ("nho gon", "nho", "gon", "mini", "compact", "small", "tiet kiem dien tich", "can ho nho", "phong nho")
LARGE_TERMS = ("to", "lon", "rong", "rong rai", "big", "large", "cho nhieu nguoi")
CITIES = {"ha noi": "Hà Nội", "hanoi": "Hà Nội", "ho chi minh": "Hồ Chí Minh", "hcm": "Hồ Chí Minh",
          "sai gon": "Hồ Chí Minh", "saigon": "Hồ Chí Minh", "tphcm": "Hồ Chí Minh", "da nang": "Đà Nẵng",
          "can tho": "Cần Thơ", "hai phong": "Hải Phòng", "binh duong": "Bình Dương", "dong nai": "Đồng Nai"}
POLICY_TERMS: list[tuple[str, str]] = [
    ("return", r"doi tra|tra hang|hoan tien|doi hang|return|refund"),
    ("warranty", r"bao hanh|warranty|guarantee"),
    ("shipping", r"giao hang|van chuyen|\bship\b|phi ship|delivery|shipping|giao tan noi|lap dat"),
    ("payment", r"thanh toan|tra gop|chuyen khoan|\bcod\b|payment|installment"),
    ("terms", r"dieu khoan|terms"), ("privacy", r"bao mat|quyen rieng tu|du lieu ca nhan|privacy"),
]


@dataclass
class Lexicon:
    categories: dict[str, str] = field(default_factory=lambda: dict(CATEGORY_TERMS))
    materials: dict[str, str] = field(default_factory=lambda: dict(MATERIAL_TERMS))
    colors: dict[str, str] = field(default_factory=lambda: dict(COLOR_TERMS))
    rooms: dict[str, str] = field(default_factory=lambda: dict(ROOM_TERMS))
    styles: dict[str, str] = field(default_factory=lambda: dict(STYLE_TERMS))
    suppliers: dict[str, str] = field(default_factory=dict)  # tên nhà cung cấp THẬT (nạp từ Backend)

    def extend_from_catalog(self, categories: list[str], materials: list[str], suppliers: list[str] = ()) -> None:
        """Thêm tên danh mục/chất liệu/nhà cung cấp THẬT từ Backend (vd 'Gỗ công nghiệp Melamine')."""
        for name in categories:
            self.categories.setdefault(fold(name), name.lower())
        for name in materials:
            self.materials.setdefault(fold(name), name)
        for name in suppliers:
            if len(fold(name)) >= 3:
                self.suppliers.setdefault(fold(name), name)

    @staticmethod
    def _longest(text: str, table: dict[str, str], exclude: set[tuple[int, int]] | None = None) -> tuple[str, tuple[int, int]] | None:
        best: tuple[str, tuple[int, int]] | None = None
        best_len = 0
        for key, canon in table.items():
            plural = "(?:s|es)?" if key.isascii() and key[-1:].isalpha() and " " not in key[-3:] else ""
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(key)}{plural}(?![a-z0-9])", text):
                span = (m.start(), m.end())
                if exclude and any(not (span[1] <= a or span[0] >= b) for a, b in exclude):
                    continue
                if len(key) > best_len:
                    best, best_len = (canon, span), len(key)
        return best

    def match(self, folded: str, kind: str, exclude: set[tuple[int, int]] | None = None) -> tuple[str, tuple[int, int]] | None:
        return self._longest(folded, getattr(self, kind), exclude)
