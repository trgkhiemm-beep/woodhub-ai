# app/services/groq_service.py
import logging
from groq import AsyncGroq
from app.core.config import settings

logger = logging.getLogger("woodhub.groq")

class GroqService:
    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model_name = settings.GROQ_MODEL

    def _build_prompt(self, query: str, context: dict) -> list:
        """Tạo prompt hệ thống chuẩn bị gửi đi"""
        system_prompt = (
            "Bạn là trợ lý bán hàng AI của cửa hàng nội thất WoodHub. "
            "Hãy trả lời khách hàng một cách thân thiện, ngắn gọn và chuyên nghiệp. "
            f"Dưới đây là dữ liệu bạn truy xuất được từ hệ thống để trả lời khách: {context}"
        )

        # RÀNG BUỘC CHO INTENT SO SÁNH
        if context.get("is_comparison") is True:
            system_prompt += (
                "\n[YÊU CẦU NGHIÊM NGẶT]: Khách hàng đang muốn so sánh sản phẩm. "
                "Hãy đọc dữ liệu sản phẩm WoodHub hiện có trong context và đối chiếu với câu hỏi. "
                "Hãy trả lời NGẮN GỌN NHẤT CÓ THỂ, tập trung làm nổi bật ưu điểm/lợi ích vượt trội "
                "của sản phẩm WoodHub để kích thích khách hàng mua hàng và chốt đơn ngay. "
                "Tuyệt đối không giải thích dài dòng dông dài, đi thẳng vào cốt lõi."
            )

        # RÀNG BUỘC CHO TÍNH NĂNG TỰ ĐỘNG GỢI Ý SHOWROOM/XƯỞNG SAU KHI XEM ĐỒ
        if context.get("is_auto_suggest_location") is True and context.get("suppliers") is not None:
            system_prompt += (
                "\n[YÊU CẦU ĐIỀU HƯỚNG O2O]: Sau khi bạn tư vấn thông tin sản phẩm từ dữ liệu hệ thống xong, "
                "hãy nhìn vào danh sách xưởng/showroom gần nhất trong mục 'suppliers'. "
                "Hãy khéo léo chèn thêm 1 câu ngắn gọn ở cuối cùng để giới thiệu showroom/xưởng gần khách hàng nhất "
                "và mời họ qua trải nghiệm sản phẩm trực tiếp (Ví dụ: 'Sản phẩm này hiện đang có sẵn tại chi nhánh X cách bạn chỉ Y km, mời bạn ghé xem qua nhé!')."
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]

    async def generate_response_stream(self, query: str, context: dict):
        """Hàm stream trả về từng chữ (Dùng cho Chatbot)"""
        try:
            messages = self._build_prompt(query, context)
            
            stream = await self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.3,
                stream=True,
            )
            
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"Lỗi Groq Service (Stream): {e}")
            yield "Xin lỗi, hệ thống AI đang bảo trì. Vui lòng thử lại sau!"

    async def generate_response_full(self, query: str, context: dict) -> str:
        """Hàm trả về 1 cục văn bản"""
        try:
            messages = self._build_prompt(query, context)
            response = await self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.3,
                stream=False,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Lỗi Groq Service (Full): {e}")
            return "Xin lỗi, hệ thống AI đang bảo trì. Vui lòng thử lại sau!"