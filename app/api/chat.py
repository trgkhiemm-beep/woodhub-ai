import re
import json
import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.schemas.chat import ChatRequest
from app.services.input_normalizer import normalize_input
from app.services.classifier import classifier
from app.services.business_engine import business_engine
from app.services.bedrock_service import BedrockService
from app.services.meshy_service import MeshyService

logger = logging.getLogger("woodhub.chat")
router = APIRouter()

ai_service = BedrockService()
meshy_service = MeshyService()

# In-memory session context memory for product reference resolution
# Session memory maps session_id -> { "last_product": dict, "last_query": str }
session_memory: Dict[str, Dict[str, Any]] = {}

GREETING_LIST = ["chào shop", "shop ơi", "xin chào", "hello", "chào bạn", "hi", "alo", "có ai không", "chào"]
GREETING_RESPONSE = "Chào bạn! Tôi là trợ lý AI chính thức của WoodHub. Tôi có thể hỗ trợ bạn tìm kiếm và tư vấn thông tin về các sản phẩm nội thất gỗ hiện có trong hệ thống."

CUSTOM_3D_KEYWORDS = ["cm", "kích thước", "tính", "đặt làm", "đóng theo yêu cầu", "custom"]
WOOD_TYPES = ["óc chó", "tần bì", "thông", "sồi", "cao su"]


def sse_event(event_type: str, **payload) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"


def stream_static_message(message: str, data_payload: dict = None):
    """
    Helper trả về StreamingResponse chứa 1 thông điệp cố định và đóng kết nối SSE.
    """
    async def _generator():
        yield sse_event("chunk", content=message)
        if data_payload:
            yield f"data: {json.dumps(data_payload, ensure_ascii=False)}\n\n"
        yield sse_event("done")

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )


async def generate_chat_stream(query: str, context: dict, session_id: str):
    """
    Hàm sinh stream phản hồi chính từ LLM dựa hoàn toàn trên thông tin trong Database context.
    """
    products = context.get("data", [])
    
    # Bắn gói dữ liệu sản phẩm trước hoặc trong quá trình stream để frontend render UI card
    result_data = {"type": "debug_data", "payload": products}
    if context.get("suppliers"):
        result_data = {"type": "mixed_data", "products": products, "stores": context["suppliers"]}

    try:
        async for chunk in ai_service.generate_grounded_response_stream(query, context):
            yield sse_event("chunk", content=chunk)
    except Exception as e:
        logger.error(f"Lỗi khi sinh response stream: {e}")
        yield sse_event("chunk", content=settings.DATABASE_ERROR_MESSAGE)
        yield sse_event("done")
        return

    # Bắn dữ liệu payload để UI hiển thị sản phẩm
    json_payload = json.dumps(result_data, ensure_ascii=False)
    yield f"data: {json_payload}\n\n"
    yield sse_event("done")


@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    session_id = request.session_id
    query_text = request.query

    # 0. Xử lý yêu cầu dựng ảnh 3D qua Meshy (Nếu request gửi kèm image_url)
    if request.image_url:
        try:
            task_id = await meshy_service.create_image_to_3d_task(request.image_url)
            async def image_3d_stream():
                yield sse_event("chunk", content="WoodHub đã nhận ảnh! AI đang nặn mẫu 3D, bạn vui lòng đợi trong giây lát...")
                yield sse_event("data", type="3d_generating", task_id=task_id, progress=0)
                yield sse_event("done")
            return StreamingResponse(image_3d_stream(), media_type="text/event-stream")
        except Exception:
            logger.exception("Lỗi khi khởi tạo tác vụ Meshy 3D từ ảnh")
            return stream_static_message("Xin lỗi bạn, hiện mình chưa thể khởi tạo mô hình 3D từ ảnh này. Bạn thử gửi lại giúp mình nhé.")

    # 1. INPUT NORMALIZATION
    norm = normalize_input(query_text)
    cleaned_query = norm["cleaned"]

    # Đánh chặn câu chào rác
    if cleaned_query in GREETING_LIST:
        return stream_static_message(GREETING_RESPONSE)

    # 2. SCOPE / INTENT CLASSIFICATION
    # Kiểm tra xem session trước đó có sản phẩm đang thảo luận hay không
    has_session_product = session_id in session_memory and bool(session_memory[session_id].get("last_product"))
    
    classification = await classifier.classify(query_text, context_has_product=has_session_product)
    scope = classification.get("scope")

    # Xử lý các trạng thái phân loại theo đúng Decision Tree (Phần 25):
    
    # (A) OUT_OF_SCOPE
    if scope == "OUT_OF_SCOPE":
        return stream_static_message(settings.OUT_OF_SCOPE_MESSAGE)

    # (B) UNKNOWN
    if scope == "UNKNOWN":
        return stream_static_message(settings.UNKNOWN_MESSAGE)

    # (C) AMBIGUOUS
    if scope == "AMBIGUOUS":
        # Nếu có sản phẩm trong session_memory, gắn sản phẩm đó vào context
        if has_session_product:
            referenced_product = session_memory[session_id]["last_product"]
            search_res = {"status": "success", "data": [referenced_product]}
        else:
            return stream_static_message(settings.AMBIGUOUS_CLARIFICATION_MESSAGE)

    # (D) IN_SCOPE
    if scope == "IN_SCOPE" or has_session_product:
        # Xử lý nhánh tính giá Custom 3D
        if any(k in cleaned_query for k in CUSTOM_3D_KEYWORDS) and re.findall(r"\d+", cleaned_query):
            numbers = re.findall(r"\d+", cleaned_query)
            if len(numbers) < 3:
                return stream_static_message("Vui lòng cung cấp đủ 3 kích thước (dài x rộng x cao), ví dụ: 100x50x30cm để mình tính giá gia công.")
            wood = next((w for w in WOOD_TYPES if w in cleaned_query), "sồi")
            custom_data = business_engine.estimate_custom_3d(wood, float(numbers[0]), float(numbers[1]), float(numbers[2]))
            return stream_static_message(custom_data.get("message", "Đã tính toán giá custom 3D."))

        # Xử lý nhánh thao tác Giỏ hàng
        if any(k in cleaned_query for k in ["giỏ hàng", "gio hang", "xem giỏ", "thêm vào giỏ"]):
            cart_data = business_engine.view_cart(session_id)
            if cart_data.get("status") == "empty":
                return stream_static_message("Giỏ hàng của bạn hiện tại đang trống.")
            elif cart_data.get("status") == "success":
                msg = f"Giỏ hàng của bạn gồm {len(cart_data['items'])} sản phẩm, tổng giá trị là {int(cart_data['total']):,.0f} VNĐ.".replace(",", ".")
                return stream_static_message(msg, data_payload={"type": "cart_data", "payload": cart_data})

        # 3. DATABASE SEARCH (Thực thi truy vấn cơ sở dữ liệu)
        search_query_info = classification.get("search_query") or {}
        kw = search_query_info.get("keyword") or query_text
        min_p = search_query_info.get("min_price")
        max_p = search_query_info.get("max_price")
        cat = search_query_info.get("category")
        mat = search_query_info.get("material") or search_query_info.get("wood_type")
        color_param = search_query_info.get("color")

        # Gọi Database (Ném vào threadpool)
        search_res = await run_in_threadpool(
            business_engine.search_product,
            keyword=kw,
            min_price=min_p,
            max_price=max_p,
            category=cat,
            material=mat,
            color=color_param
        )

        # 4. CHECK DATABASE RESULT (Phần 11 & 25)

        # (Case B) DATABASE SEARCH FAILURE (Lỗi truy vấn kết nối DB)
        if search_res.get("status") == "error":
            return stream_static_message(settings.DATABASE_ERROR_MESSAGE)

        # (Case A) PRODUCT NOT FOUND (Không tìm thấy sản phẩm trong DB)
        products = search_res.get("data", [])
        if search_res.get("status") == "empty" or not products:
            return stream_static_message(settings.MISSING_PRODUCT_MESSAGE)

        # 5. CHECK REQUESTED ATTRIBUTES (Phần 12)
        # Nếu người dùng hỏi thuộc tính cụ thể (ví dụ: màu sắc, kích thước) nhưng DB không có thông tin
        requested_attr = search_query_info.get("requested_attribute")
        if requested_attr:
            first_product = products[0]
            if not business_engine.check_attribute_availability(first_product, requested_attr):
                return stream_static_message(settings.ATTRIBUTE_NOT_FOUND_MESSAGE)

        # Lưu sản phẩm vào memory của session để hỗ trợ các câu hỏi tham chiếu tiếp theo (Phần 19)
        session_memory[session_id] = {
            "last_product": products[0],
            "last_query": query_text
        }

        # 6. GENERATE ANSWER ONLY FROM DATABASE (Gửi context xuống cho LLM stream)
        context = {
            "data": products,
            "query": query_text
        }

        # Lấy thêm thông tin cửa hàng nếu người dùng hỏi vị trí
        if any(k in cleaned_query for k in ["ở đâu", "cửa hàng", "địa chỉ", "chi nhánh", "showroom", "gần đây"]) and request.lat and request.lng:
            stores = business_engine.find_stores(keyword=kw, lat=request.lat, lng=request.lng)
            if stores:
                context["suppliers"] = stores

        return StreamingResponse(
            generate_chat_stream(query_text, context, session_id),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
        )


@router.get("/api/3d/status/{task_id}")
async def get_3d_status(task_id: str):
    status_info = await meshy_service.check_task_status(task_id)

    if status_info.get("status") == "SUCCEEDED":
        return {
            "type": "3d_ready",
            "data": {
                "estimate_price": "1.200.000 VNĐ",
                "glb_url": status_info.get("model_urls", {}).get("glb"),
                "message": "Đây là bản phác thảo 3D từ ảnh của bạn. Để tinh chỉnh chi tiết, vui lòng vào phòng thiết kế 3D.",
                "cta": "Vào phòng Thiết kế 3D",
                "cta_url": f"/design-studio?model={task_id}"
            }
        }
    elif status_info.get("status") == "FAILED":
        return {"type": "error", "message": "Quá trình tạo mô hình 3D thất bại."}

    return {"type": "3d_generating", "progress": status_info.get("progress", 0)}