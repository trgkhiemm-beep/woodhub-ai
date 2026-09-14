"""
app/schemas/chat.py

Pydantic models dùng chung cho request/response.

Tại sao tách riêng file này thay vì để chung trong chat.py?
-------------------------------------------------------------
1. FastAPI tự sinh OpenAPI schema (xem tại /docs hoặc /openapi.json).
   Nếu frontend dùng công cụ generate TypeScript type từ OpenAPI
   (ví dụ openapi-typescript, orval...), họ cần schema Request/Response
   rõ ràng, KHÔNG lẫn với logic xử lý route. Tách schema ra file riêng
   là quy ước phổ biến để giữ boundary rõ ràng giữa "hợp đồng dữ liệu"
   (data contract) và "logic nghiệp vụ".
2. Dễ tái sử dụng nếu sau này có thêm route khác (/cart, /orders)
   cần cùng schema ErrorResponse.
"""

from pydantic import BaseModel, field_validator


class ChatRequest(BaseModel):
    query: str
    session_id: str
    lat: float | None = None
    lng: float | None = None
    # Ảnh khách gửi lên để AI dựng mẫu 3D (nhánh CUSTOM 3D trong chat.py).
    # Optional vì phần lớn request là chat text thuần, không kèm ảnh.
    image_url: str | None = None

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Nội dung câu hỏi không được để trống.")
        return v

    @field_validator("session_id")
    @classmethod
    def session_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("session_id không được để trống.")
        return v

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, v: float | None) -> float | None:
        if v is not None and not (-90.0 <= v <= 90.0):
            raise ValueError("Latitude không hợp lệ (phải trong khoảng -90 đến 90).")
        return v

    @field_validator("lng")
    @classmethod
    def validate_lng(cls, v: float | None) -> float | None:
        if v is not None and not (-180.0 <= v <= 180.0):
            raise ValueError("Longitude không hợp lệ (phải trong khoảng -180 đến 180).")
        return v

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, v: str | None) -> str | None:
        # Chỉ validate sơ bộ (không rỗng, có scheme http/https).
        # KHÔNG kiểm tra ảnh có tồn tại/tải được hay không ở tầng schema —
        # đó là trách nhiệm của MeshyService khi thực sự gọi API, tách
        # đúng ranh giới: schema chỉ đảm bảo "đúng hình dạng", không đảm
        # bảo "đúng ngữ nghĩa/khả dụng".
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("image_url không hợp lệ, phải là đường dẫn http/https.")
        return v


class ErrorResponse(BaseModel):
    """
    Envelope lỗi THỐNG NHẤT cho mọi lỗi trả về từ API — dù là lỗi validate
    input (422), lỗi nghiệp vụ (503), hay lỗi hệ thống không lường trước (500).

    Lợi ích cho frontend: chỉ cần viết MỘT hàm parse lỗi duy nhất
    (ví dụ `handleApiError(response)`) dùng chung cho toàn bộ ứng dụng,
    thay vì phải đoán mò cấu trúc lỗi khác nhau tùy từng endpoint.
    """
    status: str = "error"
    code: str
    message: str