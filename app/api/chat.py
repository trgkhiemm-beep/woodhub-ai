# app/api/chat.py
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.models.chat import ChatRequest
from app.core.database import supabase
from app.services.gemini_service import gemini_service

router = APIRouter()

def check_and_update_usage(session_id: str, is_guest: bool) -> int:
    """Kiểm tra xem session_id đã vượt quá lượt hỏi quy định chưa"""
    limit_max = 5 if is_guest else 50 # Khách 5 câu, Thành viên 50 câu
    
    # Truy vấn số lượt đã hỏi từ Supabase
    res = supabase.table("user_usage_limits").select("request_count").eq("session_id", session_id).execute()
    
    if not res.data:
        # Nếu chưa từng hỏi, tạo mới dòng lưu vết
        supabase.table("user_usage_limits").insert({"session_id": session_id, "request_count": 1}).execute()
        return 1
    
    current_count = res.data[0]["request_count"]
    
    # Nếu vượt quá giới hạn, chặn luôn
    if current_count >= limit_max:
        raise HTTPException(
            status_code=429, 
            detail=f"Bạn đã dùng hết {limit_max} lượt hỏi miễn phí cho phiên này. Vui lòng đăng nhập hoặc nâng cấp tài khoản!"
        )
        
    # Nếu còn lượt, tăng số lượt lên 1
    new_count = current_count + 1
    supabase.table("user_usage_limits").update({"request_count": new_count}).eq("session_id", session_id).execute()
    return new_count

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    print("\n====== KIỂM TRA DỮ LIỆU TỪ FRONTEND ======")
    print(f"-> Người dùng gõ: {request.user_prompt}")
    print(f"-> Session ID: {request.session_id}")
    session_id = request.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Thiếu session_id")
        
    # 1. Nhận diện người dùng dựa vào tiền tố của session_id
    # Quy ước: Nếu Frontend truyền session_id bắt đầu bằng "guest_", coi như chưa đăng nhập
    is_guest = session_id.startswith("guest_")
    
    # 2. Kiểm tra giới hạn số câu hỏi
    check_and_update_usage(session_id, is_guest)
        
    # 3. Tạo một hàm Generator để stream dữ liệu
    async def event_generator():
        try:
            # Nếu là khách, gửi thông báo cấu trúc chặn chức năng nâng cao (ép AI chỉ dùng Search)
            intent_type = "GUEST_SEARCH" if is_guest else "FULL_ACCESS"
            yield "data: " + json.dumps({"intent": intent_type, "status": "streaming_started"}) + "\n\n"
            
            # Sửa prompt linh hoạt dựa trên quyền hạn người dùng
            prompt_modifier = ""
            if is_guest:
                prompt_modifier = "\n[LƯU Ý HỆ THỐNG]: Người dùng này chưa đăng nhập (GUEST). BẠN TUYỆT ĐỐI KHÔNG ĐƯỢC gọi các hàm liên quan đến Giỏ hàng (add_to_cart, view_cart) hay Thanh toán. Nếu họ yêu cầu mua hàng, hãy lịch sự bảo họ đăng nhập trước."
            
            # Gọi Stream từ Gemini
            async for chunk in gemini_service.generate_response_stream(
                session_id=session_id,
                user_message=request.user_prompt + prompt_modifier
            ):
                yield f"data: {json.dumps({'answer_chunk': chunk}, ensure_ascii=False)}\n\n"
                
            yield "data: " + json.dumps({"status": "completed"}, ensure_ascii=False) + "\n\n"
            
        except Exception as e:
            yield "data: " + json.dumps({"error": str(e)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")