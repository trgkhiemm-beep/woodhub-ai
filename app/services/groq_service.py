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
        """Xây dựng prompt hệ thống với luật chặn cứng từ ngữ màu mè và điều hướng danh mục Cửa hàng"""
        system_prompt = (
            "Bạn là trợ lý ảo bán hàng chuyên nghiệp của WoodHub. "
            "Nhiệm vụ của bạn là đọc dữ liệu ngữ cảnh (context) từ hệ thống để trả lời khách hàng."
        )

        # CẬP NHẬT LUẬT NGHIÊM NGẶT KHI TRUY VẤN CÓ SẢN PHẨM 
        product_data = context.get("data")
        if product_data and isinstance(product_data, list) and len(product_data) > 0:
            system_prompt += (
                "\n[YÊU CẦU TỐI THƯỢNG]: Hệ thống đã tìm thấy sản phẩm thực tế trong database. "
                "Bạn KHÔNG ĐƯỢC PHÉP tự bịa thêm sản phẩm, KHÔNG ĐƯỢC liệt kê lại tên hay giá tiền bằng văn bản text. "
                "Bạn CHỈ ĐƯỢC PHÉP trả về duy nhất đoạn văn bản sau đây, không thêm bớt bất kỳ từ nào, giữ đúng định dạng xuống dòng: "
                "'\nMình gợi ý vài mẫu phù hợp nhé:\n\n*(Lưu ý: Các sản phẩm trên chưa phải tất cả sản phẩm mà WoodHub có, bạn có thể tham khảo thêm tại mục Cửa hàng)*'"
            )
        elif product_data == []:
            system_prompt += (
                "\n[YÊU CẦU]: Hệ thống báo kho hàng trống ([]). Hãy thông báo ngắn gọn lịch sự "
                "rằng WoodHub hiện chưa có dòng sản phẩm này trong kho, tuyệt đối không tự bịa thông tin sản phẩm."
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
        try:
            messages = self._build_prompt(query, context)
            stream = await self.client.chat.completions.create(
                messages=messages,
                model=self.model_name,
                temperature=0.1,  # Đảm bảo AI tuân thủ tuyệt đối cấu trúc văn bản mẫu
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Lỗi luồng stream Groq: {e}")
            yield "Hệ thống AI đang bận, vui lòng thử lại sau!"