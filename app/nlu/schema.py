"""Kết quả NLU có cấu trúc: message → intents (có thể nhiều) + entities + constraints + tham chiếu ngữ cảnh."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    GREETING = "greeting"
    STORE_INFO = "store_info"
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
    # mutation (tham số luôn do parser deterministic trích xuất)
    UPDATE_PRICE = "update_price"
    UPDATE_DESCRIPTION = "update_description"
    ADJUST_INVENTORY = "adjust_inventory"
    UPDATE_STORE_INFO = "update_store_info"
    UPSERT_FAQ = "upsert_faq"
    CREATE_PROMOTION = "create_promotion"
    SET_PROMOTION_STATUS = "set_promotion_status"
    UPSERT_CATEGORY = "upsert_category"
    UPSERT_MATERIAL = "upsert_material"
    # điều khiển
    CONFIRM = "confirm"
    CANCEL = "cancel"
    CART = "cart"
    OUT_OF_SCOPE = "out_of_scope"
    UNCLEAR = "unclear"


MUTATION_INTENTS = frozenset({
    Intent.UPDATE_PRICE, Intent.UPDATE_DESCRIPTION, Intent.ADJUST_INVENTORY, Intent.UPDATE_STORE_INFO,
    Intent.UPSERT_FAQ, Intent.CREATE_PROMOTION, Intent.SET_PROMOTION_STATUS, Intent.UPSERT_CATEGORY, Intent.UPSERT_MATERIAL,
})

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
    promo_code: str | None = None
    question: str | None = None                          # câu hỏi gốc cho knowledge search
    asked: list[Literal["price", "dimensions", "material", "color", "description"]] = Field(default_factory=list)  # trường được hỏi



class IntentFrame(BaseModel):
    intent: Intent
    entities: Entities = Field(default_factory=Entities)
    tool_args: dict[str, Any] | None = None   # chỉ cho mutation: tham số từ parser deterministic


class NLUResult(BaseModel):
    frames: list[IntentFrame]
    source: Literal["llm+rules", "rules"]
    language: Literal["vi", "en", "mixed"] = "vi"
    injection_suspected: bool = False
    confirm_code: str | None = None
    llm_raw: dict[str, Any] | None = Field(default=None, exclude=True)
    dropped_llm_entities: list[str] = Field(default_factory=list)

    @property
    def primary(self) -> IntentFrame:
        return self.frames[0]
