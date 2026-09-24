import json
import boto3
import asyncio
import logging
from app.core.config import settings

logger = logging.getLogger("woodhub.bedrock")

class BedrockService:
    def __init__(self):
        self.region = settings.AWS_DEFAULT_REGION
        self.aws_access_key_id = settings.AWS_ACCESS_KEY_ID
        self.aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
        self.model_id = settings.BEDROCK_MODEL_ID
        
        self.client = None
        if self.aws_access_key_id and self.aws_secret_access_key:
            try:
                self.client = boto3.client(
                    service_name="bedrock-runtime",
                    region_name=self.region,
                    aws_access_key_id=self.aws_access_key_id,
                    aws_secret_access_key=self.aws_secret_access_key
                )
            except Exception as e:
                logger.error(f"Lỗi khi khởi tạo Bedrock client: {e}")

    async def classify_and_extract(self, query: str) -> dict:
        """
        Giao tiếp với AWS Bedrock để phân loại scope và bóc tách dữ liệu tìm kiếm.
        """
        if not self.client:
            logger.warning("Bedrock client chưa được cấu hình hoặc không có key.")
            return None

        prompt = f"""
Nhiệm vụ: Phân loại ý định và phạm vi câu hỏi cho hệ thống tư vấn nội thất gỗ WoodHub.
Câu hỏi người dùng: "{query}"

Trả về DUY NHẤT 1 JSON (không markdown, không giải thích) theo format:
{{
  "scope": "IN_SCOPE" | "OUT_OF_SCOPE" | "UNKNOWN" | "AMBIGUOUS",
  "intent": "PRODUCT_SEARCH" | "PRODUCT_PRICE" | "PRODUCT_SPEC" | "PRODUCT_COMPARISON" | "STORE_LOCATION" | "CART_ACTION" | "CUSTOM_3D" | null,
  "confidence": float_from_0_to_1,
  "needs_clarification": boolean,
  "search_query": {{
    "keyword": "từ_khóa_hoặc_null",
    "category": "danh_mục_hoặc_null",
    "material": "chất_liệu_hoặc_null",
    "wood_type": "loại_gỗ_hoặc_null",
    "min_price": số_hoặc_null,
    "max_price": số_hoặc_null,
    "color": "màu_hoặc_null",
    "requested_attribute": "tên_thuộc_tính_cụ_thể_được_hỏi_ví_dụ_color_hoặc_dimensions_hoặc_price_hoặc_null"
  }}
}}

Quy tắc phân loại:
1. IN_SCOPE: Liên quan trực tiếp tới nội thất gỗ (bàn, ghế, tủ, giường, sofa, kệ, phòng khách, phòng ngủ, chất liệu gỗ, giá cả, cửa hàng nội thất, giỏ hàng WoodHub).
2. OUT_OF_SCOPE: Câu hỏi thuộc lĩnh vực hoàn toàn khác (thời tiết, thể thao, lập trình, bitcoin, chính trị, nấu ăn, v.v.).
3. UNKNOWN: Chuỗi ký tự vô nghĩa, không thể hiểu được (ví dụ "xyz abc 123").
4. AMBIGUOUS: Hiểu được ý định hỏi thông tin sản phẩm nhưng thiếu đối tượng cụ thể (ví dụ: "cái này giá bao nhiêu?", "sản phẩm này có màu gì?") khi không có ngữ cảnh.
"""

        body = json.dumps({
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.0
        })

        try:
            response = await asyncio.to_thread(
                self.client.invoke_model,
                body=body,
                modelId=self.model_id
            )
            response_body = json.loads(response.get('body').read())
            
            result_text = ""
            content = response_body.get("content")
            choices = response_body.get("choices")
            
            if isinstance(content, list) and len(content) > 0 and isinstance(content[0], dict):
                result_text = content[0].get("text", "").strip()
            elif isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict):
                msg = choices[0].get("message", {})
                if isinstance(msg, dict):
                    result_text = msg.get("content", "").strip()
            elif "completion" in response_body:
                result_text = str(response_body.get("completion", "")).strip()

            if result_text and "{" in result_text and "}" in result_text:
                json_str = result_text[result_text.find("{"):result_text.rfind("}")+1]
                return json.loads(json_str)
            
            return None
        except Exception as e:
            logger.error(f"Lỗi classify_and_extract Bedrock: {e}")
            return None

    async def generate_grounded_response_stream(self, query: str, context: dict):
        """
        Sinh câu trả lời từ LLM DỰA HOÀN TOÀN TRÊN DATABASE CONTEXT ĐÃ CUNG CẤP.
        Tuyệt đối không suy đoán, không bịa thông tin ngoài Database.
        """
        system_prompt = (
            "Bạn là trợ lý bán hàng nội thất WoodHub. "
            "QUY TẮC TUYỆT ĐỐI:\n"
            "1. CHỈ sử dụng thông tin có trong DỮ LIỆU DATABASE (Context) bên dưới để trả lời.\n"
            "2. Tuyệt đối KHÔNG sử dụng kiến thức ngoài để bịa giá, bịa độ bền, bịa màu sắc, bịa kích thước hay tính năng sản phẩm.\n"
            "3. Trả lời ngắn gọn, đi thẳng vào câu hỏi của khách hàng, thân thiện và chính xác.\n"
            "4. Nếu thông tin không có trong Context, hãy khẳng định rõ thông tin chưa có trong hệ thống."
        )
        user_prompt = f"Câu hỏi của khách: {query}\nDỮ LIỆU DATABASE CONTEXT: {json.dumps(context, ensure_ascii=False)}"

        if not self.client:
            # Fallback nếu không có Bedrock client
            products = context.get("data", [])
            if isinstance(products, list) and len(products) > 0:
                p = products[0]
                price = p.get("price", "N/A")
                price_str = f"{int(price):,.0f} VNĐ".replace(",", ".") if isinstance(price, (int, float)) and price > 0 else "N/A"
                yield f"Sản phẩm **{p.get('name')}** hiện có trong hệ thống với giá **{price_str}**."
            else:
                yield settings.MISSING_PRODUCT_MESSAGE
            return

        body = json.dumps({
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": 400,
            "temperature": 0.0
        })

        try:
            response = await asyncio.to_thread(
                self.client.invoke_model_with_response_stream,
                body=body,
                modelId=self.model_id
            )
            stream = response.get('body')
            if stream:
                for event in stream:
                    chunk = event.get('chunk')
                    if chunk:
                        chunk_obj = json.loads(chunk.get('bytes').decode())
                        text_chunk = ""
                        if chunk_obj.get('type') == 'content_block_delta':
                            delta = chunk_obj.get('delta', {})
                            if isinstance(delta, dict):
                                text_chunk = delta.get('text', '')
                        elif chunk_obj.get('choices') and isinstance(chunk_obj['choices'], list):
                            delta = chunk_obj['choices'][0].get('delta', {})
                            if isinstance(delta, dict):
                                text_chunk = delta.get('content', '')

                        if text_chunk:
                            yield text_chunk
                            await asyncio.sleep(0.005)
        except Exception as e:
            logger.error(f"Lỗi generate_stream Bedrock: {e}")
            # Dynamic text extraction from context if streaming fails
            products = context.get("data", [])
            if isinstance(products, list) and len(products) > 0:
                p = products[0]
                price = p.get("price", 0)
                price_str = f"{int(price):,.0f} VNĐ".replace(",", ".") if price > 0 else "được liệt kê trong hệ thống"
                yield f"Sản phẩm {p.get('name')} hiện có giá {price_str}."
            else:
                yield settings.MISSING_PRODUCT_MESSAGE

