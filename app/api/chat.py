import re
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.services.business_engine import business_engine
from app.services.groq_service import GroqService

logger = logging.getLogger("woodhub.chat")

router = APIRouter()

# Khởi tạo dịch vụ AI (Groq)
ai_service = GroqService()

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
    """Chặn sớm các câu hỏi rõ ràng lạc đề."""
    q = query.lower()
    return any(keyword in q for keyword in OUT_OF_SCOPE_KEYWORDS)

def get_clean_keyword(query: str) -> str:
    """Loại bỏ các từ khóa nhiễu để có chuỗi tìm kiếm (keyword) sạch."""
    noise_words = [
        "giá", "bao nhiêu", "cho mình hỏi", "tư vấn", "là gì", 
        "tìm", "có", "không", "hỏi", "chi tiết", "của"
    ]
    clean_q = query.lower()
    for w in noise_words:
        clean_q = clean_q.replace(w, " ")
    return " ".join(clean_q.split())

def get_intent_and_data(req: ChatRequest) -> dict:
    """Phân loại ý định và truy vấn dữ liệu cần thiết."""
    q = req.query.lower()
    result = {"data": None, "suppliers": None}
    clean_keyword = get_clean_keyword(req.query)

    # 1. INTENT: GIỎ HÀNG
    if any(k in q for k in ["giỏ hàng", "xem giỏ", "thêm vào", "xóa khỏi"]):
        result["data"] = business_engine.view_cart(req.session_id)

    # 2. INTENT: TÍNH GIÁ ĐÓNG ĐỒ 3D
    elif any(k in q for k in ["cm", "kích thước", "tính"]) and re.findall(r"\d+", q):
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

    # 3. INTENT: TÌM CỬA HÀNG / ĐỊA CHỈ
    elif any(k in q for k in ["ở đâu", "cửa hàng", "địa chỉ", "chi nhánh", "showroom", "gần đây"]):
        if req.lat is not None and req.lng is not None:
            result["suppliers"] = business_engine.find_suppliers_nearby(req.lat, req.lng)
        else:
            result["suppliers"] = {
                "status": "error", 
                "message": "Không nhận diện được vị trí của bạn để tìm cửa hàng gần nhất."
            }
            
    # 4. INTENT MẶC ĐỊNH: TÌM SẢN PHẨM
    else:
        result["data"] = business_engine.search_product(clean_keyword)

    return result

def sse_event(event_type: str, **payload) -> str:
    """Đóng gói sự kiện theo chuẩn SSE."""
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"

async def sse_stream(query: str, context: dict):
    """Wrap luồng từ GroqService thành các SSE event."""
    try:
        # SỬA LỖI TẠI ĐÂY: Dùng ai_service thay vì ollama_service
        async for chunk in ai_service.generate_response_stream(query, context):
            yield sse_event("chunk", content=chunk)
        yield sse_event("done")
    except Exception:
        logger.exception("Lỗi khi stream phản hồi cho query: %s", query)
        yield sse_event("error", message="Hệ thống đang gặp sự cố, vui lòng thử lại sau.")

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    if is_out_of_scope(request.query):
        async def scope_stream():
            yield sse_event("chunk", content=OUT_OF_SCOPE_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(scope_stream(), media_type="text/event-stream")

    try:
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
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )