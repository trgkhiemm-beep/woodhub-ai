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

NO_COMPARISON_PRODUCTS_MESSAGE = (
    "Hiện tại WoodHub chưa có sản phẩm của bạn trong kho, "
    "bạn có muốn tham khảo hoặc so sánh sản phẩm khác không?"
)

NO_LOCATION_MESSAGE = (
    "WoodHub chưa nhận được định vị của bạn. Bạn vui lòng bật định vị trên thiết bị "
    "hoặc chia sẻ vị trí để mình tìm showroom/xưởng gần bạn nhất nhé!"
)

def is_out_of_scope(query: str) -> bool:
    """Chặn sớm các câu hỏi rõ ràng lạc đề."""
    q = query.lower()
    return any(keyword in q for keyword in OUT_OF_SCOPE_KEYWORDS)

def get_clean_keyword(query: str) -> str:
    """Loại bỏ các từ khóa nhiễu để có chuỗi tìm kiếm (keyword) sạch."""
    noise_words = [
        "giá", "bao nhiêu", "cho mình hỏi", "tư vấn", "là gì", 
        "tìm", "có", "không", "hỏi", "chi tiết", "của",
        "so sánh", "khác gì", "đối chiếu", "như thế nào với"
    ]
    clean_q = query.lower()
    for w in noise_words:
        clean_q = clean_q.replace(w, " ")
    return " ".join(clean_q.split())

def get_intent_and_data(req: ChatRequest) -> dict:
    """Phân loại ý định và truy vấn dữ liệu cần thiết."""
    q = req.query.lower()
    result = {
        "data": None, 
        "suppliers": None, 
        "is_comparison_intent": False, 
        "is_location_intent": False,
        "is_auto_suggest_location": False
    }
    clean_keyword = get_clean_keyword(req.query)

    # Đánh dấu Intent so sánh
    if any(k in q for k in ["so sánh", "khác gì", "đối chiếu", "như thế nào với"]):
        result["is_comparison_intent"] = True

    # Đánh dấu Intent KHÁCH CHỦ ĐỘNG hỏi vị trí showroom / xưởng
    if any(k in q for k in ["ở đâu", "cửa hàng", "địa chỉ", "chi nhánh", "showroom", "gần đây", "tìm xưởng"]):
        result["is_location_intent"] = True

    # 1. INTENT: GIỎ HÀNG
    if any(k in q for k in ["giỏ hàng", "xem giỏ", "thêm vào", "xóa khỏi"]):
        result["data"] = business_engine.view_cart(req.session_id)

    # 2. INTENT: TÍNH GIÁ ĐÓNG ĐỒ CUSTOM 3D -> GỢI Ý XƯỞNG PHÙ HỢP TẠI ĐÂY
    elif (any(k in q for k in ["cm", "kích thước", "tính", "đặt làm", "đóng theo yêu cầu", "custom"]) 
          and re.findall(r"\d+", q)):
        numbers = re.findall(r"\d+", q)
        if len(numbers) < 3:
            result["data"] = {
                "status": "error",
                "message": "Vui lòng cung cấp đủ 3 kích thước (dài x rộng x cao), ví dụ: 100x50x30cm để mình tính giá gia công.",
            }
        else:
            wood = "sồi"
            for w_type in ["óc chó", "tần bì", "thông", "sồi"]:
                if w_type in q:
                    wood = w_type
                    break
            # Tính toán giá đóng đồ custom dựa trên kích thước
            result["data"] = business_engine.estimate_custom_3d(
                wood, float(numbers[0]), float(numbers[1]), float(numbers[2])
            )
            
            # ĐỀ XUẤT XƯỞNG PHÙ HỢP CHO ĐƠN CUSTOM: Nếu có định vị, lấy thông tin xưởng sản xuất gần nhất
            if req.lat is not None and req.lng is not None:
                result["suppliers"] = business_engine.find_stores(keyword=req.query, lat=req.lat, lng=req.lng)
                result["is_auto_suggest_location"] = True

    # 3. INTENT: KHÁCH CHỦ ĐỘNG HỎI CỬA HÀNG (Chỉ gọi DB khi có tọa độ thực tế)
    elif result["is_location_intent"] and req.lat is not None and req.lng is not None:
        result["suppliers"] = business_engine.find_suppliers_nearby(req.lat, req.lng)
            
    # 4. INTENT MẶC ĐỊNH: TÌM SẢN PHẨM THƯỜNG (Hoàn toàn tách biệt, không tự động chèn địa chỉ xưởng nữa)
    else:
        result["data"] = business_engine.search_product(clean_keyword)

    return result

def sse_event(event_type: str, **payload) -> str:
    """Đóng gói sự kiện theo chuẩn SSE."""
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"

def is_store_intent(query: str):
    keywords = ["tìm xưởng", "xưởng", "cửa hàng", "showroom", "gần đây"]
    return any(k in query.lower() for k in keywords)

async def sse_stream(query: str, context: dict):
    # 1. Làm sạch câu lệnh
    clean_query = re.sub(r'[.,!?]+$', '', query.lower().strip())

    # 2. Đánh chặn chào hỏi (Ưu tiên cao nhất)
    if clean_query in GREETING_LIST:
        yield f"data: {{\"type\": \"chunk\", \"content\": \"{GREETING_RESPONSE}\"}}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
        return

    # 3. Xác định ý định & Chuẩn bị dữ liệu (Trước khi stream AI)
    is_store = is_store_intent(query)
    has_product_search = context.get("has_product_search", False)
    user_lat = context.get("lat")
    user_lng = context.get("lng")
    
    # Lấy dữ liệu sẵn sàng để AI có thể "biết" và phản hồi
    result_data = None
    data_type = "debug_data" # mặc định

    if is_store and not has_product_search:
        # LUỒNG 1: CHỈ TÌM XƯỞNG
        stores = business_engine.find_stores(keyword=query, lat=user_lat, lng=user_lng)
        result_data = {"type": "store_data", "payload": stores}
        data_type = "store_data"
    elif is_store and has_product_search:
        # LUỒNG 2: HỖN HỢP
        products = business_engine.search_product(query)
        stores = business_engine.find_stores(keyword=query, lat=user_lat, lng=user_lng)
        result_data = {"type": "mixed_data", "products": products, "stores": stores}
        data_type = "mixed_data"
    else:
        # TÌM SẢN PHẨM THÔNG THƯỜNG
        products = business_engine.search_product(query)
        result_data = {"type": "debug_data", "payload": products}
        data_type = "debug_data"

    # 4. Stream Phản hồi AI (AI nhận dữ liệu qua context để trả lời tự nhiên)
    context["current_results"] = result_data # Truyền vào để AI biết kết quả
    try:
        async for chunk in ai_service.generate_response_stream(query, context):
            yield f"data: {{\"type\": \"chunk\", \"content\": \"{chunk}\"}}\n\n"
    except Exception as e:
        yield f"data: {{\"type\": \"error\", \"message\": \"{str(e)}\"}}\n\n"
        # Dù lỗi vẫn cần đóng stream
        yield "data: {\"type\": \"done\"}\n\n"
        return

    # 5. Gửi dữ liệu (Sau khi AI trả lời xong)
    json_payload = json.dumps(result_data, ensure_ascii=False)
    yield f"data: {{\"type\": \"{data_type}\", \"payload\": {json_payload}}}\n\n"

    # 6. Cuối cùng mới báo hoàn thành
    yield "data: {\"type\": \"done\"}\n\n"
    
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

    # TIẾT KIỆM TOKEN: Khách CHỦ ĐỘNG hỏi vị trí nhưng thiết bị ko bật định vị -> Ngắt luôn
    if full_result.get("is_location_intent") and (request.lat is None or request.lng is None):
        async def no_location_stream():
            yield sse_event("chunk", content=NO_LOCATION_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(no_location_stream(), media_type="text/event-stream")

    data = full_result["data"]

    # TIẾT KIỆM TOKEN: Khách muốn so sánh nhưng DB sản phẩm rỗng -> Ngắt luôn
    if full_result.get("is_comparison_intent") and (not data or data == [] or data == {}):
        async def no_product_stream():
            yield sse_event("chunk", content=NO_COMPARISON_PRODUCTS_MESSAGE)
            yield sse_event("done")
        return StreamingResponse(no_product_stream(), media_type="text/event-stream")

    # Đóng gói ngữ cảnh gửi sang AI Service
    if isinstance(data, dict) and "estimated_price" in data:
        context = {
            "estimated_price": data.get("estimated_price"),
            "message": data.get("message"),
            "suppliers": full_result.get("suppliers"),
            "is_comparison": full_result.get("is_comparison_intent"),
            "is_auto_suggest_location": full_result.get("is_auto_suggest_location")
        }
    else:
        context = {
            "data": data,
            "suppliers": full_result.get("suppliers"),
            "is_comparison": full_result.get("is_comparison_intent"),
            "is_auto_suggest_location": full_result.get("is_auto_suggest_location")
        }

    return StreamingResponse(
        sse_stream(request.query, context),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )