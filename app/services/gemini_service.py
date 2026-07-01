# app/services/gemini_service.py
import google.generativeai as genai
from app.core.database import supabase
from app.core.config import settings
from app.services.prompts import WOODHUB_SYSTEM_PROMPT
from app.tools.product_tools import (
    get_product_details_by_name, 
    redirect_to_payment_page, 
    add_to_cart, 
    view_cart, 
    estimate_custom_3d_product
)
from google.api_core import exceptions

class GeminiService:
    def __init__(self):
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise RuntimeError("Thiếu GEMINI_API_KEY")
        
        genai.configure(api_key=api_key.strip(), transport='rest')
        
        self.model = genai.GenerativeModel(
            model_name="gemini-2.5-flash", 
            generation_config={"temperature": 0.3},
            system_instruction=WOODHUB_SYSTEM_PROMPT,
            tools=[get_product_details_by_name, redirect_to_payment_page, add_to_cart, view_cart, estimate_custom_3d_product]
        )

    def _init_chat_session(self, session_id: str):
        """Lấy lịch sử và khởi tạo phiên chat với tool được bật"""
        history_res = supabase.table("chat_histories").select("role, content").eq("session_id", session_id).order("created_at", desc=False).execute()
        
        formatted_history = []
        for msg in (history_res.data or []):
            role = "model" if msg["role"] in ["assistant", "ai"] else "user"
            formatted_history.append({"role": role, "parts": [msg["content"]]})
            
        return self.model.start_chat(history=formatted_history, enable_automatic_function_calling=True)

    def _save_chat_history(self, session_id: str, user_message: str, ai_reply: str):
        try:
            supabase.table("chat_histories").insert([
                {"session_id": session_id, "role": "user", "content": user_message},
                {"session_id": session_id, "role": "model", "content": ai_reply}
            ]).execute()
        except Exception as e:
            print(f"Lỗi lưu history: {str(e)}")

    def generate_response(self, session_id: str, user_message: str, system_prompt_override: str = None) -> str:
        """Hàm trả về kết quả trọn vẹn một lần (Không streaming)"""
        try:
            chat = self._init_chat_session(session_id)
            
            contextual_message = f"[System: SessionID={session_id}]\n"
            if system_prompt_override:
                contextual_message += f"[RAG DATA]:\n{system_prompt_override}\n\n"
            contextual_message += f"Khách hàng: {user_message}"
            
            # Gửi tin nhắn và nhận kết quả đầy đủ
            response = chat.send_message(contextual_message)
            ai_reply = response.text
            
            self._save_chat_history(session_id, user_message, ai_reply)
            return ai_reply
            
        except exceptions.ResourceExhausted:
            return "Hệ thống đang quá tải, vui lòng thử lại sau."
        except Exception as e:
            return f"Đã xảy ra lỗi: {str(e)}"
        
    async def generate_response_stream(self, session_id: str, user_message: str, system_prompt_override: str = None):
        """
        Phiên bản giả lập stream: Gọi hàm xử lý chính và trả về toàn bộ kết quả một lần.
        Dùng để tương thích với API chat.py mà không cần sửa code API.
        """
        full_response = self.generate_response(session_id, user_message, system_prompt_override)
        yield full_response
        
# Khởi tạo instance
gemini_service = GeminiService()