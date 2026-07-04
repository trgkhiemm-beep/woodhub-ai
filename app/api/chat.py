"""
app/api/chat.py

So với bản refactor trước, file này bổ sung:
1. Import ChatRequest từ app.schemas.chat thay vì định nghĩa lại
   (single source of truth cho data contract).
2. Khởi tạo OllamaService bằng giá trị từ settings (config.py) thay vì
   hardcode, để đổi model/timeout chỉ cần sửa .env.
3. QUAN TRỌNG NHẤT: chuyển streaming từ "text/plain" thô sang chuẩn
   Server-Sent Events (SSE, media_type="text/event-stream").

Tại sao cần SSE thay vì stream text thô?
-------------------------------------------
Với "text/plain" thô, frontend nhận được một luồng ký tự liên tục và
KHÔNG CÓ CÁCH NÀO phân biệt được: đâu là token của câu trả lời, khi nào
model đã trả lời xong, hay có lỗi xảy ra giữa chừng (ví dụ Ollama timeout
sau khi đã gửi vài chữ). Frontend chỉ có thể "đoán" bằng cách chờ stream
đóng lại.

SSE giải quyết việc này bằng cách đóng gói MỖI mẩu dữ liệu thành một
message có cấu trúc: mỗi dòng bắt đầu bằng "data: ", kết thúc bằng 2 dấu
xuống dòng "\n\n". Ở đây mình dùng payload JSON bên trong "data: " để có
thể phân loại rõ 3 loại event:
    - {"type": "chunk", "content": "..."}   -> 1 mẩu text của câu trả lời
    - {"type": "done"}                       -> đã trả lời xong
    - {"type": "error", "message": "..."}    -> có lỗi giữa chừng

Nhờ vậy, code React/Vue phía frontend có thể viết switch-case rõ ràng
theo "type", thay vì phải đoán ý nghĩa của một đoạn text thô.
"""

import re
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.services.business_engine import business_engine
from app.services.ollama_service import OllamaService

logger = logging.getLogger("woodhub.chat")

router = APIRouter()

# Khởi tạo service 1 lần duy nhất lúc import module, dùng giá trị từ
# settings thay vì hardcode -> đổi model/timeout chỉ cần sửa .env.
ollama_service = OllamaService(
    model_name=settings.OLLAMA_MODEL,
    request_timeout=settings.OLLAMA_TIMEOUT,
    num_predict=settings.OLLAMA_NUM_PREDICT,
    temperature=settings.OLLAMA_TEMPERATURE,
)

OUT_OF_SCOPE_KEYWORDS = [
    "chính trị", "tổng thống", "bầu cử",
    "thời tiết", "dự báo",
    "bóng đá", "world cup", "thể thao",
    "lập trình", "code", "python", "javascript",
]

OUT_OF_SCOPE_MESSAGE = (
    "Xin lỗi bạn, mình là trợ lý bán hàng của WoodHub, mình chỉ hỗ trợ các thông tin "
    "liên quan đến sản phẩm nội thất, tìm kiếm sản phẩm, giỏ hàng và đơn hàng của WoodHub."
)


def is_out_of_scope(query: str) -> bool:
    """Chặn sớm các câu hỏi rõ ràng lạc đề, không tốn round-trip tới LLM."""
    q = query.lower()
    return any(keyword in q for keyword in OUT_OF_SCOPE_KEYWORDS)


def get_intent_and_data(req: ChatRequest) -> dict:
    """
    Hàm này VẪN đồng bộ (vì business_engine dùng Supabase client đồng bộ),
    nên LUÔN được gọi qua run_in_threadpool ở route bên dưới, không bao
    giờ gọi trực tiếp trong hàm async def.
    """
    q = req.query.lower()
    result = {"data": None, "suppliers": None}

    if "giỏ hàng" in q or "xem giỏ" in q:
        result["data"] = business_engine.view_cart(req.session_id)

    elif ("cm" in q or "kích thước" in q or "tính" in q) and re.findall(r"\d+", q):
        numbers = re.findall(r"\d+", q)
        if len(numbers) < 3:
            result["data"] = {
                "status": "error",
                "message": "Vui lòng cung cấp đủ 3 kích thước (dài x rộng x cao), ví dụ: 100x50x30cm.",
            }
        else:
            wood = "sồi"
            for w_type in ["óc chó", "tần bì", "thông", "sồi"]:
                if w_type in q:
                    wood = w_type
                    break
            result["data"] = business_engine.estimate_custom_3d(
                wood, float(numbers[0]), float(numbers[1]), float(numbers[2])
            )

    else:
        result["data"] = business_engine.search_product(req.query)

    if req.lat is not None and req.lng is not None:
        result["suppliers"] = business_engine.find_suppliers_nearby(req.lat, req.lng)

    return result


def sse_event(event_type: str, **payload) -> str:
    """
    Đóng gói 1 sự kiện theo đúng chuẩn Server-Sent Events.

    Format chuẩn: 1 dòng bắt đầu bằng "data: ", kết thúc bằng "\n\n" để
    client biết đây là ranh giới của một message hoàn chỉnh. Payload bên
    trong là JSON để frontend luôn parse được một cấu trúc thống nhất,
    thay vì phải suy đoán ý nghĩa của text thô.
    """
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"


async def sse_stream(query: str, context: dict):
    """Wrap luồng text thô từ OllamaService thành các SSE event có cấu trúc."""
    try:
        async for chunk in ollama_service.generate_response_stream(query, context):
            yield sse_event("chunk", content=chunk)
        yield sse_event("done")
    except Exception:
        logger.exception("Lỗi khi stream phản hồi cho query: %s", query)
        yield sse_event("error", message="Hệ thống đang gặp sự cố, vui lòng thử lại sau.")


@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    # Chặn sớm câu hỏi ngoài phạm vi -> phản hồi tức thì, không gọi Supabase/Ollama.
    if is_out_of_scope(request.query):
        async def scope_stream():
            yield sse_event("chunk", content=OUT_OF_SCOPE_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(scope_stream(), media_type="text/event-stream")

    try:
        # Đẩy toàn bộ I/O đồng bộ (Supabase) ra khỏi event loop chính.
        full_result = await run_in_threadpool(get_intent_and_data, request)
    except Exception:
        logger.exception("Lỗi khi truy vấn business_engine cho session_id=%s", request.session_id)
        raise HTTPException(
            status_code=503,
            detail="Hệ thống dữ liệu tạm thời không khả dụng, vui lòng thử lại sau.",
        )

    data = full_result["data"]

    if isinstance(data, dict) and "estimated_price" in data:
        context = {
            "estimated_price": data.get("estimated_price"),
            "message": data.get("message"),
            "suppliers": full_result.get("suppliers"),
        }
    else:
        context = full_result

    return StreamingResponse(
        sse_stream(request.query, context),
        media_type="text/event-stream",
        # Header khuyến nghị cho SSE khi chạy sau reverse proxy (Nginx):
        # tắt buffering để chunk được đẩy ra ngay, không bị proxy giữ lại.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )