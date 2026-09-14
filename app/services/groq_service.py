# app/services/groq_service.py
import logging
import json
from groq import AsyncGroq
from app.core.config import settings

logger = logging.getLogger("woodhub.groq")

class GroqService:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model_name = settings.GROQ_MODEL

    async def extract_search_intent(self, query: str) -> dict:
        system_prompt = (
            "Bạn là trợ lý bóc tách dữ liệu JSON. Đọc tin nhắn và trích xuất thông tin tìm kiếm.\n"
            "Quy tắc quy đổi:\n"
            "- Trả về số nguyên (VNĐ). VD: 5 triệu -> 5000000, 500k -> 500000.\n"
            "- 'dưới X' -> price_min: 0, price_max: X.\n"
            "- 'trên X' -> price_min: X, price_max: null.\n"
            "- 'từ X đến Y' -> price_min: X, price_max: Y.\n"
            "- 'khoảng/tầm X' -> price_min: X*0.9, price_max: X*1.1.\n"
            "- Bắt buộc trả về JSON format: {\"keyword\": \"tên sản phẩm\", \"price_min\": số/null, \"price_max\": số/null}"
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ],
                temperature=0.0
            )
            result_json = json.loads(response.choices[0].message.content)
            return {
                "keyword": result_json.get("keyword", ""),
                "price_min": result_json.get("price_min"),
                "price_max": result_json.get("price_max")
            }
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất intent AI: {e}")
            return {"keyword": query, "price_min": None, "price_max": None}

    def _build_prompt(self, query: str, context: dict) -> list:
        system_prompt = (
            "Bạn là trợ lý ảo bán hàng chuyên nghiệp của WoodHub. "
            "Nhiệm vụ của bạn là đọc dữ liệu ngữ cảnh (context) từ hệ thống để trả lời khách hàng."
        )

        product_data = context.get("data")
        is_fallback = context.get("is_fallback", False)
        
        if product_data == []:
            system_prompt = (
                "BẠN LÀ TRỢ LÝ KHÔNG ĐƯỢC PHÉP HÀM Ý CÓ HÀNG TRONG KHO. "
                "Hệ thống báo kho hàng hiện tại hoàn toàn trống cho từ khóa này. "
                "Bắt buộc thông báo ngắn gọn, lịch sự rằng WoodHub chưa có sản phẩm này trong kho."
            )
        elif is_fallback:
            system_prompt += (
                "\n[QUAN TRỌNG]: Hệ thống không tìm thấy sản phẩm trong mức giá khách yêu cầu. "
                "Tuy nhiên, hệ thống đã tự động tìm các sản phẩm có giá gần nhất. "
                "Hãy khéo léo giới thiệu các mẫu này một cách ngắn gọn, giải thích rằng không có hàng đúng giá nhưng có mẫu tương tự."
            )

        if context.get("is_auto_suggest_location") is True and context.get("suppliers"):
            system_prompt += (
                "\n[YÊU CẦU]: Khách muốn đóng đồ custom. Sau khi thông báo giá ước tính, hãy chèn thêm 1 câu "
                "ngắn gọn mời họ đặt đơn sản xuất tại xưởng gần nhất được liệt kê trong mục 'suppliers'."
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]

    async def generate_response_stream(self, query: str, context: dict):
        product_data = context.get("data")
        # Chỉ đánh chặn sớm khi có dữ liệu thật VÀ KHÔNG PHẢI là dữ liệu Fallback (cần AI giải thích)
        if product_data and isinstance(product_data, list) and len(product_data) > 0 and not context.get("is_fallback"):
            yield "Mình gợi ý vài mẫu phù hợp nhé:"
            return

        try:
            messages = self._build_prompt(query, context)
            stream = await self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.0,  
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Lỗi luồng stream Groq: {e}")
            yield "WoodHub hiện chưa có dòng sản phẩm này trong kho, bạn tham khảo mẫu khác nhé!"