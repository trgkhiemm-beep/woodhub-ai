import re
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.services.business_engine import business_engine
from app.services.groq_service import GroqService

GREETING_LIST = ["chào shop", "shop ơi", "xin chào", "hello", "chào bạn", "hi", "alo", "có ai không", "chào"]
GREETING_RESPONSE = "chào bạn tôi là trợ lý của woodhub, bạn có nhu cầu tìm kiếm hoặc tham khảo sản phẩm nội thất nào cứ việc nhắn tin cho tôi biết nhé"

logger = logging.getLogger("woodhub.chat")
router = APIRouter()
ai_service = GroqService()

OUT_OF_SCOPE_KEYWORDS = ["chính trị", "tổng thống", "bầu cử", "thời tiết", "dự báo", "bóng đá", "world cup", "thể thao", "lập trình", "code", "python", "javascript"]
OUT_OF_SCOPE_MESSAGE = "Xin lỗi bạn, mình là trợ lý bán hàng của WoodHub, mình chỉ hỗ trợ các thông tin liên quan đến sản phẩm nội thất, tìm kiếm sản phẩm, giỏ hàng và đơn hàng của WoodHub."
NO_COMPARISON_PRODUCTS_MESSAGE = "Hiện tại WoodHub chưa có sản phẩm của bạn trong kho, bạn có muốn tham khảo hoặc so sánh sản phẩm khác không?"
NO_LOCATION_MESSAGE = "WoodHub chưa nhận được định vị của bạn. Bạn vui lòng bật định vị trên thiết bị hoặc chia sẻ vị trí để mình tìm showroom/xưởng gần bạn nhất nhé!"

def is_out_of_scope(query: str) -> bool:
    q = query.lower()
    return any(keyword in q for keyword in OUT_OF_SCOPE_KEYWORDS)

def sse_event(event_type: str, **payload) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"

def get_intent_and_data(req: ChatRequest, intent_data: dict) -> dict:
    """Xử lý Logic lấy dữ liệu đồng bộ (Chạy trong threadpool)"""
    q = req.query.lower()
    result = {
        "data": None, 
        "suppliers": None, 
        "is_comparison_intent": False, 
        "is_location_intent": False,
        "is_auto_suggest_location": False,
        "is_fallback": False
    }

    if any(k in q for k in ["so sánh", "khác gì", "đối chiếu", "như thế nào với"]): result["is_comparison_intent"] = True
    if any(k in q for k in ["ở đâu", "cửa hàng", "địa chỉ", "chi nhánh", "showroom", "gần đây", "tìm xưởng"]): result["is_location_intent"] = True

    # 1. GIỎ HÀNG
    if any(k in q for k in ["giỏ hàng", "xem giỏ", "thêm vào", "xóa khỏi"]):
        result["data"] = business_engine.view_cart(req.session_id)

    # 2. CUSTOM 3D
    elif (any(k in q for k in ["cm", "kích thước", "tính", "đặt làm", "đóng theo yêu cầu", "custom"]) and re.findall(r"\d+", q)):
        numbers = re.findall(r"\d+", q)
        if len(numbers) < 3:
            result["data"] = {"status": "error", "message": "Vui lòng cung cấp đủ 3 kích thước (dài x rộng x cao), ví dụ: 100x50x30cm để mình tính giá gia công."}
        else:
            wood = next((w for w in ["óc chó", "tần bì", "thông", "sồi"] if w in q), "sồi")
            result["data"] = business_engine.estimate_custom_3d(wood, float(numbers[0]), float(numbers[1]), float(numbers[2]))
            if req.lat is not None and req.lng is not None:
                result["suppliers"] = business_engine.find_stores(keyword=req.query, lat=req.lat, lng=req.lng)
                result["is_auto_suggest_location"] = True

    # 3. CHỈ TÌM CỬA HÀNG/XƯỞNG
    elif result["is_location_intent"] and not intent_data.get("keyword"):
        if req.lat is not None and req.lng is not None:
            result["suppliers"] = business_engine.find_stores(keyword=req.query, lat=req.lat, lng=req.lng)
            
    # 4. TÌM KIẾM SẢN PHẨM & KẾT HỢP XƯỞNG
    else:
        # Sử dụng dữ liệu đã bóc tách từ LLM để truy vấn
        kw = intent_data.get("keyword") or req.query
        p_res = business_engine.search_product(kw, intent_data.get("price_min"), intent_data.get("price_max"))
        
        result["data"] = p_res.get("data", [])
        result["is_fallback"] = p_res.get("is_fallback", False)
        
        # Nếu khách cũng có ý định tìm xưởng (vd: "tìm xưởng có bán bàn dưới 5tr")
        if result["is_location_intent"] and req.lat is not None and req.lng is not None:
            result["suppliers"] = business_engine.find_stores(keyword=kw, lat=req.lat, lng=req.lng)

    return result

async def sse_stream(query: str, context: dict):
    # Lớp 1: Đánh chặn câu chào rác
    clean_query = re.sub(r'[.,!?]+$', '', query.lower().strip())
    if clean_query in GREETING_LIST:
        yield f"data: {{\"type\": \"chunk\", \"content\": \"{GREETING_RESPONSE}\"}}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
        return

    # Khởi tạo data payload từ context đã được chuẩn bị sẵn, KHÔNG GỌI LẠI DB NỮA
    data_type = "debug_data"
    payload = context.get("data", [])

    if context.get("suppliers"):
        if isinstance(payload, list) and payload:
             data_type = "mixed_data"
             result_data = {"type": data_type, "products": payload, "stores": context["suppliers"]}
        elif isinstance(payload, dict) and "estimated_price" in payload:
             data_type = "mixed_data"
             result_data = {"type": data_type, "custom_3d": payload, "stores": context["suppliers"]}
        else:
             data_type = "store_data"
             result_data = {"type": data_type, "payload": context["suppliers"]}
    else:
        result_data = {"type": data_type, "payload": payload}

    # Phản hồi stream text từ AI
    try:
        async for chunk in ai_service.generate_response_stream(query, context):
            yield f"data: {{\"type\": \"chunk\", \"content\": \"{chunk}\"}}\n\n"
    except Exception as e:
        yield f"data: {{\"type\": \"error\", \"message\": \"{str(e)}\"}}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
        return

    # Bắn gói dữ liệu sản phẩm xuống frontend
    json_payload = json.dumps(result_data, ensure_ascii=False)
    yield f"data: {json_payload}\n\n"
    yield "data: {\"type\": \"done\"}\n\n"

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    if is_out_of_scope(request.query):
        async def scope_stream():
            yield sse_event("chunk", content=OUT_OF_SCOPE_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(scope_stream(), media_type="text/event-stream")

    try:
        # Bóc tách ý định bằng AI (Async) trước tiên
        intent_data = await ai_service.extract_search_intent(request.query)
        # Ném vào Threadpool để gọi DB (Sync)
        full_result = await run_in_threadpool(get_intent_and_data, request, intent_data)
    except Exception:
        logger.exception("Lỗi khi truy vấn dữ liệu")
        raise HTTPException(status_code=503, detail="Hệ thống dữ liệu tạm thời không khả dụng.")

    if full_result.get("is_location_intent") and (request.lat is None or request.lng is None):
        async def no_location_stream():
            yield sse_event("chunk", content=NO_LOCATION_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(no_location_stream(), media_type="text/event-stream")

    data = full_result["data"]
    if full_result.get("is_comparison_intent") and (not data or data == [] or data == {}):
        async def no_product_stream():
            yield sse_event("chunk", content=NO_COMPARISON_PRODUCTS_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(no_product_stream(), media_type="text/event-stream")

    # Xây dựng context tổng thể để AI và Frontend sử dụng
    context = {
        "data": data,
        "suppliers": full_result.get("suppliers"),
        "is_comparison": full_result.get("is_comparison_intent"),
        "is_auto_suggest_location": full_result.get("is_auto_suggest_location"),
        "is_fallback": full_result.get("is_fallback")
    }

    return StreamingResponse(
        sse_stream(request.query, context),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )