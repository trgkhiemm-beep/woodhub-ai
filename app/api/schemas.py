"""
PUBLIC CONTRACT giữa Frontend/Backend-proxy và AI Agent (version: agent-api v1).

Frontend chỉ phụ thuộc vào các DTO ở file này (xuất ra contracts/agent-api.openapi.json).
Không bao giờ trả class nội bộ (PendingAction, ToolResult...) trực tiếp.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger("woodhub.api")  # không log nội dung tin nhắn
CONTRACT_VERSION = "1.0"

ResponseType = Literal["answer", "clarification", "confirmation_required", "action_result", "error"]
BlockKind = Literal[
    "recommendation", "product_detail", "product_comparison", "inventory", "store_info", "branch_list",
    "workshop_list", "promotion_list", "policy", "knowledge", "taxonomy", "design_task", "candidates",
    "supplier_info", "order_status",
]


def _accepts(name: str, camel: str) -> dict[str, Any]:
    """Backend (Spring) gửi camelCase (sessionId, confirmationCode): request nhận CẢ HAI cách đặt tên;
    response giữ nguyên snake_case. OpenAPI ghi alias ở `x-aliases` (JSON Schema không có khái niệm alias)."""
    return {"validation_alias": AliasChoices(name, camel), "json_schema_extra": {"x-aliases": [camel]}}


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, examples=[10.7769])
    lng: float = Field(ge=-180, le=180, examples=[106.7009])

    @property
    def is_real(self) -> bool:
        """(-90,-180) là giá trị mẫu Swagger sinh từ minimum của schema, (0,0) là mặc định của client — không phải GPS thật."""
        return not (abs(self.lat) >= 90 or abs(self.lng) >= 180 or (self.lat == 0 and self.lng == 0))


def _coord(value: Any, limit: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and -limit <= v <= limit else None


def real_location(lat: Any, lng: Any) -> tuple[float, float] | None:
    """GPS thật hoặc None. Thiếu một trong hai, không phải số, ngoài [-90,90]/[-180,180], giá trị mẫu (-90,-180)/(0,0)
    → None: Agent không tự tạo hay đoán vị trí, và vị trí hỏng không làm hỏng cả lượt chat (vị trí là tùy chọn)."""
    la, ln = _coord(lat, 90), _coord(lng, 180)
    if la is None or ln is None or not Location(lat=la, lng=ln).is_real:
        return None
    return la, ln


def _pick(data: dict, *keys: str) -> Any:
    return next((data[k] for k in keys if data.get(k) is not None), None)


_LAT_KEYS, _LNG_KEYS = ("lat", "latitude"), ("lng", "lon", "longitude")


class ChatRequest(BaseModel):
    """Nhận cả snake_case và camelCase: `session_id`|`sessionId`, `client_message_id`|`clientMessageId`.
    Nội dung: `message` hoặc `content` (Backend `SendAiMessageRequest{content, lat, lng}`) hoặc `query` (định dạng /chat cũ).
    Vị trí: `lat`/`lng` ở cấp ngoài (Backend `SendAiMessageRequest`, `AdminAiChatRequest`) hoặc `location{lat,lng}`;
    nhận thêm `latitude`/`longitude`. Vị trí thiếu/không hợp lệ/giá trị mẫu (-90,-180), (0,0) bị bỏ qua (không lỗi 422)
    — Agent không giả định vị trí. Chỉ tìm xưởng gần cần vị trí."""
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"content": "tìm xưởng gần tôi", "lat": 10.7769, "lng": 106.7009},
        {"message": "tìm bàn học dưới 3 triệu", "session_id": None},
    ]})

    @model_validator(mode="before")
    @classmethod
    def _normalize_location(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        loc = data.get("location") if isinstance(data.get("location"), dict) else {}
        lat, lng = _pick(loc, *_LAT_KEYS), _pick(loc, *_LNG_KEYS)
        if lat is None or lng is None:
            lat, lng = _pick(data, *_LAT_KEYS), _pick(data, *_LNG_KEYS)
        real = real_location(lat, lng)
        if real is None and (lat is not None or lng is not None):
            logger.info("location_ignored lat=%r lng=%r", lat, lng)
        # Spring có thể serialize trường rỗng thành null (vd "message": null cạnh "content") → bỏ để alias kế tiếp được dùng.
        data = {k: v for k, v in data.items()
                if k not in ("latitude", "lon", "longitude") and not (k in ("message", "content", "query") and v is None)}
        return {**data, "lat": real[0] if real else None, "lng": real[1] if real else None,
                "location": {"lat": real[0], "lng": real[1]} if real else None}

    message: str = Field(min_length=1, max_length=10000,
                         description="Tin nhắn người dùng (giới hạn thực tế theo MAX_MESSAGE_CHARS). Alias: `content`, `query`.",
                         validation_alias=AliasChoices("message", "content", "query"),
                         json_schema_extra={"x-aliases": ["content", "query"]})
    lat: float | None = Field(default=None, ge=-90, le=90, examples=[10.7769],
                              description="Vĩ độ GPS thiết bị (tùy chọn). Alias: `latitude`.")
    lng: float | None = Field(default=None, ge=-180, le=180, examples=[106.7009],
                              description="Kinh độ GPS thiết bị (tùy chọn). Alias: `lon`, `longitude`.")
    session_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_\-:.]+$",
                                   description="Bỏ trống để server tạo phiên mới. Alias: `sessionId`.",
                                   **_accepts("session_id", "sessionId"))
    client_message_id: str | None = Field(default=None, max_length=100,
                                          description="Id phía client để chống gửi trùng. Alias: `clientMessageId`.",
                                          **_accepts("client_message_id", "clientMessageId"))
    location: Location | None = Field(default=None, description="Vị trí thiết bị (chỉ dùng cho tìm xưởng gần).")

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Tin nhắn không được để trống.")
        return v


class ConfirmRequest(BaseModel):
    """Giữ cho tương thích Backend (/api/admin/ai-agent/actions/{id}/confirm). Agent CHỈ ĐỌC nên không còn action:
    luôn trả AgentResponse type=error, error.code=ACTION_NOT_FOUND. Nhận `confirmationCode`, `sessionId`."""
    confirmation_code: str = Field(min_length=4, max_length=12, description="Alias: `confirmationCode`.",
                                   **_accepts("confirmation_code", "confirmationCode"))
    session_id: str | None = Field(default=None, max_length=100, description="Alias: `sessionId`.",
                                   **_accepts("session_id", "sessionId"))


class SourceOut(BaseModel):
    system: Literal["backend"]
    resource: str
    freshness: Literal["realtime", "reference", "semantic"]
    fetched_at: datetime
    record_id: str | None = None
    version: str | None = None
    verified: bool = Field(default=True, description="True: đọc trực tiếp từ source of truth trong lượt này.")


class Block(BaseModel):
    """Dữ liệu có cấu trúc kèm câu trả lời. Hình dạng `data` theo `kind` — các kind mới có schema riêng:
    `supplier_info` → SupplierInfoBlockData, `order_status` → list[OrderStatusBlockData]."""
    kind: BlockKind
    data: Any


# ---- Schema tài liệu cho data của block (không dùng để validate response; xuất vào OpenAPI components).
class SupplierStoreBlockData(BaseModel):
    id: str
    name: str | None = None
    district: str | None = None
    city: str | None = None
    kind: str | None = None
    supplier_id: str | None = None


class SupplierInfoBlockData(BaseModel):
    """Block `supplier_info`: hồ sơ CÔNG KHAI của nhà cung cấp (Backend /api/suppliers/{id}/public + /stores)."""
    id: str
    name: str
    type: str | None = Field(default=None, description="retailer | workshop")
    description: str | None = None
    phone: str | None = None
    email: str | None = None
    stores: list[SupplierStoreBlockData] = Field(default_factory=list, description="Chỉ quận/thành phố (Backend công khai).")
    product: str | None = Field(default=None, description="Tên sản phẩm dùng để xác định nhà cung cấp (nếu có).")
    fields: list[Literal["hotline", "email", "address", "opening_hours"]] = Field(
        default_factory=list, description="Trường khách hỏi; rỗng = mặc định điện thoại, email, khu vực.")
    topic: Literal["shipping", "return", "warranty", "payment", "terms", "privacy"] | None = Field(
        default=None, description="Khách hỏi chính sách của nhà cung cấp; Backend chưa có dữ liệu chính sách.")


class OrderStatusChangeBlockData(BaseModel):
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None
    created_at: str | None = None


class OrderStatusBlockData(BaseModel):
    """Phần tử của block `order_status` (data là danh sách): đơn đặt làm do Backend trả theo token Backend chuyển tiếp."""
    id: str
    order_number: str | None = None
    status: str = Field(description="Giá trị nguyên văn từ Backend (custom_orders.status).")
    workshop_name: str | None = None
    total_amount: float | None = None
    lead_time_days: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    history: list[OrderStatusChangeBlockData] = Field(default_factory=list)


BLOCK_DATA_SCHEMAS = (SupplierInfoBlockData, OrderStatusBlockData)


class FieldChangeOut(BaseModel):
    field: str
    label: str
    before: Any = None
    after: Any = None


class ConfirmationOut(BaseModel):
    required: bool
    level: Literal["standard", "strong"]
    code: str | None = Field(default=None, description="Mã người dùng phải gửi lại để xác nhận (chỉ trả cho chủ action).")
    expires_at: datetime
    confirm_endpoint: str
    cancel_endpoint: str
    chat_phrase: str = Field(description="Câu người dùng có thể gõ trong chat để xác nhận, vd 'xác nhận ABC123'.")


class ActionOut(BaseModel):
    id: str
    tool: str
    operation: Literal["UPDATE", "SENSITIVE_UPDATE", "ACTION"]
    state: Literal["pending_confirmation", "confirmed", "executing", "verified", "completed", "unverified",
                   "failed", "cancelled", "expired"]
    summary: str
    target: dict[str, str]
    changes: list[FieldChangeOut]
    warnings: list[str] = Field(default_factory=list)
    confirmation: ConfirmationOut | None = None
    verified: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ErrorOut(BaseModel):
    code: str
    message: str


class MetaOut(BaseModel):
    contract_version: str = CONTRACT_VERSION
    profile: Literal["customer", "management"]
    role: Literal["guest", "customer", "supplier", "admin"] = Field(
        default="customer", description="Luôn 'customer': Agent không xác thực/phân quyền người dùng (Backend làm). Giữ cho tương thích.")
    planner: Literal["rules", "llm"] = Field(description="NLU: 'llm' = LLM phân loại ý + code trích xuất; 'rules' = dự phòng.")
    tools_used: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list, description="Các ý người dùng mà agent nhận diện trong lượt này.")
    data_source: Literal["backend"] = "backend"


class AgentResponse(BaseModel):
    type: ResponseType
    message: str
    session_id: str
    request_id: str
    blocks: list[Block] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    action: ActionOut | None = None
    error: ErrorOut | None = None
    meta: MetaOut


class ErrorResponse(BaseModel):
    """Envelope lỗi HTTP (4xx/5xx) — khác với AgentResponse(type='error') là lỗi trong hội thoại."""
    status: Literal["error"] = "error"
    code: str
    message: str
    request_id: str | None = None
