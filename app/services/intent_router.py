import json
import logging
from groq import AsyncGroq
from app.core.config import settings

logger = logging.getLogger("woodhub.router")

ROUTER_PROMPT = """Bạn là trợ lý phân loại câu hỏi cho cửa hàng nội thất WoodHub.
Nhiệm vụ của bạn là đọc câu hỏi của khách hàng và trả về CHỈ MỘT chuỗi JSON (không giải thích gì thêm).

Định dạng bắt buộc:
{"intent": "TÊN_NHÓM", "keyword": "từ khóa tìm kiếm chính"}

Danh sách TÊN_NHÓM (intent) được phép dùng:
1. PRODUCT_INFO: Khách hỏi về sản phẩm (giá cả, chất liệu, kích thước, so sánh...). 
   VD: "giá bàn ăn gỗ sồi bao nhiêu" -> keyword: "bàn ăn gỗ sồi".
2. STORE_LOCATION: Khách hỏi địa chỉ, chi nhánh, showroom. 
   VD: "cửa hàng ở Hà Nội" -> keyword: "Hà Nội".
3. CART_MANAGEMENT: Khách thao tác giỏ hàng (thêm, xóa, xem giỏ).
   VD: "thêm ghế sofa vào giỏ" -> keyword: "ghế sofa".
4. OUT_OF_SCOPE: Khách hỏi chuyện ngoài lề (thời tiết, làm thơ, chính trị...).
   VD: "thời tiết hôm nay" -> keyword: "".
"""

class IntentRouter:
    def __init__(self):
        # Khởi tạo client Groq
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model_name = settings.GROQ_MODEL

    async def analyze(self, user_query: str) -> dict:
        try:
            # Gọi Groq API
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": ROUTER_PROMPT},
                    {"role": "user", "content": user_query},
                ],
                # Ép Groq trả về JSON
                response_format={"type": "json_object"},
                temperature=0.0 # Để AI trả về kết quả nhất quán
            )
            
            # Lấy chuỗi JSON từ phản hồi của Groq
            result_text = response.choices[0].message.content
            return json.loads(result_text)
            
        except Exception as e:
            logger.error(f"Lỗi khi Router phân loại: {e}")
            # Nếu lỗi, mặc định là hỏi sản phẩm và lấy nguyên câu hỏi làm từ khóa
            return {"intent": "PRODUCT_INFO", "keyword": user_query}