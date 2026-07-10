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
        """Xây dựng prompt hệ thống cho các trường hợp kho trống hoặc đóng đồ custom"""
        system_prompt = (
            "Bạn là trợ lý ảo bán hàng chuyên nghiệp của WoodHub. "
            "Nhiệm vụ của bạn là đọc dữ liệu ngữ cảnh (context) từ hệ thống để trả lời khách hàng."
        )

        product_data = context.get("data")
        
        # Xử lý nghiêm ngặt khi kho hàng trống ([]) để AI không tự bịa sản phẩm
        if product_data == []:
            system_prompt = (
                "BẠN LÀ TRỢ LÝ KHÔNG ĐƯỢC PHÉP HÀM Ý CÓ HÀNG TRONG KHO. "
                "Hệ thống báo kho hàng hiện tại hoàn toàn trống cho từ khóa này. "
                "Bạn BẮT BUỘC phải thông báo ngắn gọn, lịch sự rằng WoodHub hiện chưa có dòng sản phẩm này trong kho. "
                "Tuyệt đối không tự bịa tên sản phẩm, chất liệu hay kích thước."
            )

        # RÀNG BUỘC CHO TÍNH NĂNG ĐIỀU HƯỚNG ĐẶT ĐƠN CUSTOM
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
        # 1. CHIẾN LƯỢC CHẶN ĐẦU (SHORT-CIRCUIT): 
        # Nếu có sản phẩm thật từ DB, trả về câu dẫn cứng lập tức, bỏ qua việc gọi API Groq nhằm chống ảo tưởng 100%
        product_data = context.get("data")
        if product_data and isinstance(product_data, list) and len(product_data) > 0:
            yield "Mình gợi ý vài mẫu phù hợp nhé:"
            return

        # 2. Trường hợp kho trống hoặc các Intent khác (Custom 3D, hỏi showroom...) mới chuyển sang AI xử lý
        try:
            messages = self._build_prompt(query, context)
            stream = await self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.0,  # Hạ kịch sàn về 0.0 để triệt tiêu hoàn toàn tính sáng tạo tự do
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Lỗi luồng stream Groq: {e}")
            yield "WoodHub hiện chưa có dòng sản phẩm này trong kho, bạn tham khảo mẫu khác nhé!"