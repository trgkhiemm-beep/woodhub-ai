"""Kết quả NLU có cấu trúc: message → intents (có thể nhiều) + entities + constraints + tham chiếu ngữ cảnh."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    GREETING = "greeting"
    SUPPLIER_INFO = "supplier_info"     # hotline/địa chỉ/liên hệ của NHÀ CUNG CẤP (theo sản phẩm/ngữ cảnh)
    BRANCHES = "branches"
    POLICY = "policy"
    GUIDE_FAQ = "guide_faq"
    TAXONOMY = "taxonomy"
    WORKSHOP = "workshop"
    DESIGN_TASK = "design_task"
    PROMOTION = "promotion"
    PRODUCT_SEARCH = "product_search"
    RECOMMEND = "recommend"
    PRODUCT_DETAIL = "product_detail"   # gồm hỏi giá
    INVENTORY = "inventory"
    COMPARE = "compare"
    ORDER_STATUS = "order_status"       # trạng thái đơn của khách (Backend quyết định quyền xem)
    CHANGE_REQUEST = "change_request"   # yêu cầu sửa/xóa/tạo dữ liệu → agent chỉ đọc, từ chối
    CART = "cart"
    OUT_OF_SCOPE = "out_of_scope"
    UNCLEAR = "unclear"


Relative = Literal["cheaper", "more_expensive", "smaller", "larger"]
SizePref = Literal["compact", "large"]


class Entities(BaseModel):
    product_codes: list[str] = Field(default_factory=list)
    product_name: str | None = None
    reference: Literal["current"] | None = None        # "cái này", "mẫu này", "nó"
    ordinal: int | None = Field(default=None, ge=1, le=20)  # "mẫu 2", "cái thứ 3"
    ordinals: list[int] = Field(default_factory=list)       # "so sánh mẫu 1 và mẫu 3"
    category: str | None = None                          # dạng có dấu, vd "bàn ăn"
    material: str | None = None
    color: str | None = None
    style: str | None = None
    room: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    amounts: list[float] = Field(default_factory=list)       # mọi số tiền xuất hiện (dùng điền slot)
    seats: int | None = Field(default=None, ge=1, le=30)
    size: SizePref | None = None
    relative: Relative | None = None
    price_pref: Literal["low", "high"] | None = None          # "giá rẻ", "cheap" / "cao cấp", "premium"
    use_case: str | None = None
    policy_type: str | None = None
    city: str | None = None
    store_fields: list[str] = Field(default_factory=list)
    taxonomy_kind: Literal["categories", "materials", "rooms", "styles"] | None = None
    task_id: str | None = None
    supplier_name: str | None = None                     # tên nhà cung cấp người dùng nhắc tới (khớp danh sách thật)
    supplier_ref: bool = False                           # "shop này", "nhà cung cấp này" → supplier của sản phẩm đang nói
    distinct_suppliers: bool = False                     # "từ các nhà cung cấp khác nhau"
    count: int | None = Field(default=None, ge=1, le=10)  # "cho tôi 3 bàn", "chọn giúp 3 mẫu" → số sản phẩm muốn xem
    in_stock: bool = False                               # "… còn hàng" khi tìm kiếm (điều kiện, không phải hỏi tồn kho)
    question: str | None = None                          # câu hỏi gốc cho knowledge search
    asked: list[Literal["price", "dimensions", "material", "color", "description"]] = Field(default_factory=list)  # trường được hỏi



class IntentFrame(BaseModel):
    intent: Intent
    entities: Entities = Field(default_factory=Entities)


class NLUResult(BaseModel):
    frames: list[IntentFrame]
    source: Literal["llm+rules", "rules"]
    language: Literal["vi", "en", "mixed"] = "vi"
    injection_suspected: bool = False
    llm_raw: dict[str, Any] | None = Field(default=None, exclude=True)
    dropped_llm_entities: list[str] = Field(default_factory=list)

    @property
    def primary(self) -> IntentFrame:
        return self.frames[0]
