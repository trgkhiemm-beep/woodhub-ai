import json
import boto3
import asyncio
import logging
import os

logger = logging.getLogger("woodhub.bedrock")

class BedrockService:
    def __init__(self):
        self.region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.client = boto3.client(
            service_name="bedrock-runtime",
            region_name=self.region,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
        )
        self.model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

    async def extract_search_intent(self, query: str) -> dict:
        prompt = f"""
        Bạn là hệ thống bóc tách ý định cho cửa hàng nội thất WoodHub. Hãy phân tích câu nói: "{query}".
        Trả về DUY NHẤT một chuỗi JSON hợp lệ với định dạng: 
        {{"keyword": "tên sản phẩm hoặc null", "price_min": số_hoặc_null, "price_max": số_hoặc_null, "location_text": "địa danh_hoặc_null"}}
        Không kèm bất kỳ lời giải thích hay ký tự Markdown nào khác ngoài JSON.
        """
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 150,
            "messages": [{"role": "user", "content": prompt}]
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
            
            return {"keyword": query, "price_min": None, "price_max": None, "location_text": None}
        except Exception as e:
            logger.error(f"Lỗi extract_intent Bedrock: {e}")
            return {"keyword": query, "price_min": None, "price_max": None, "location_text": None}

    async def generate_response_stream(self, query: str, context: dict):
        system_prompt = (
            "Bạn là trợ lý AI thông minh của WoodHub - thương hiệu nội thất hàng đầu. "
            "Tư vấn thân thiện, ngắn gọn (tối đa 2-3 câu), đi thẳng vào nội dung chính. "
            "Dựa vào dữ liệu context được cung cấp để tư vấn chính xác."
        )
        user_prompt = f"Câu hỏi khách hàng: {query}\nDữ liệu hệ thống (Context): {json.dumps(context, ensure_ascii=False)}"

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 400,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}]
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
            yield "Xin lỗi bạn, hệ thống AI đang bảo trì trong giây lát. Bạn vui lòng thử lại sau nhé."
