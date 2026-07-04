import json
import logging
import httpx

from ollama import AsyncClient
from app.services.prompts import WOODHUB_SYSTEM_PROMPT
from app.core.config import settings  # Import settings từ file config của bạn

logger = logging.getLogger("woodhub.ollama")

class OllamaService:
    def __init__(
        self,
        # Lấy giá trị mặc định trực tiếp từ config thay vì hardcode
        model_name: str = settings.OLLAMA_MODEL,
        request_timeout: float = settings.OLLAMA_TIMEOUT, 
        num_predict: int = settings.OLLAMA_NUM_PREDICT,
        temperature: float = settings.OLLAMA_TEMPERATURE,
        base_url: str = settings.OLLAMA_BASE_URL,
    ):
        self.model_name = model_name
        self.num_predict = num_predict
        self.temperature = temperature
        
        # Thiết lập timeout chuẩn của httpx (hỗ trợ cho cả connect, read, write)
        timeout_config = httpx.Timeout(request_timeout)
        
        # Truyền host và timeout vào AsyncClient
        self.client = AsyncClient(host=base_url, timeout=timeout_config)

    async def generate_response_stream(self, user_query: str, db_result: dict):
        try:
            # JSON thay vì str(dict): tiết kiệm token, đúng định dạng model quen thuộc.
            context = json.dumps(db_result, ensure_ascii=False, default=str)
            final_prompt = WOODHUB_SYSTEM_PROMPT.format(context=context)

            stream = await self.client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": final_prompt},
                    {"role": "user", "content": user_query},
                ],
                stream=True,
                options={
                    "num_predict": self.num_predict,
                    "temperature": self.temperature,
                },
            )

            async for chunk in stream:
                content = chunk.get("message", {}).get("content")
                if content:
                    yield content

        except httpx.ReadTimeout:
            # Bắt riêng lỗi Timeout để báo cho user biết hệ thống đang quá tải
            logger.error(f"Ollama Timeout ({self.model_name}) sau {settings.OLLAMA_TIMEOUT}s cho query: {user_query}")
            yield "\n[Hệ thống AI đang khởi động hoặc quá tải, vui lòng thử lại sau vài giây.]"
            
        except Exception:
            logger.exception("Lỗi khi gọi Ollama cho query: %s", user_query)
            yield "\n[Hệ thống đang gặp sự cố, vui lòng thử lại sau.]"