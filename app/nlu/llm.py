"""
LLM NLU có kiểm soát (AWS Bedrock Converse).

LLM CHỈ trả: danh sách intent + đoạn câu tương ứng (span) + ngôn ngữ. Không trả giá trị entity.
Mọi giá trị (mã, tiền, số người, tham chiếu…) được trích xuất lại bằng code từ span → LLM không thể
bịa dữ liệu hay tham số. Output bị validate chặt (JSON, enum, span phải nằm trong câu gốc).

Thử nghiệm với Gemma 3 4B (model đang cấu hình): JSON luôn hợp lệ, ~1 giây/lượt, nhưng tự bịa entity
và bỏ qua native tool use → đó là lý do chọn thiết kế "LLM phân loại, code trích xuất".
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol

from app.nlu.lexicon import fold
from app.nlu.schema import Intent

logger = logging.getLogger("woodhub.nlu.llm")


class LLMUnavailable(Exception):
    pass


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str) -> str: ...


class BedrockConverseClient:
    def __init__(self, *, model_id: str, region: str, access_key: str | None, secret_key: str | None,
                 timeout: float, max_tokens: int):
        import boto3
        from botocore.config import Config

        self._model_id = model_id
        self._max_tokens = max_tokens
        self._timeout = timeout
        self.stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self._client = boto3.client(
            "bedrock-runtime", region_name=region, aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=Config(read_timeout=timeout, connect_timeout=5, retries={"max_attempts": 2, "mode": "standard"}),
        )

    async def complete(self, *, system: str, user: str) -> str:
        kwargs = {"modelId": self._model_id, "system": [{"text": system}],
                  "messages": [{"role": "user", "content": [{"text": user}]}],
                  "inferenceConfig": {"maxTokens": self._max_tokens, "temperature": 0}}
        try:
            self.stats["calls"] += 1
            resp = await asyncio.wait_for(asyncio.to_thread(self._client.converse, **kwargs), self._timeout + 5)
            usage = resp.get("usage") or {}
            self.stats["input_tokens"] += int(usage.get("inputTokens", 0))
            self.stats["output_tokens"] += int(usage.get("outputTokens", 0))
            return "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
        except Exception as exc:  # ClientError, timeout, network, format
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc


# Prompt ngắn có chủ đích (token đầu vào mỗi lượt): LLM chỉ được gọi khi bộ luật deterministic không chắc chắn.
# Prompt ngắn có chủ đích (token đầu vào mỗi lượt): LLM chỉ được gọi khi bộ luật deterministic không chắc chắn.
INTENT_GUIDE = """greeting | supplier_info: liên hệ/địa chỉ nhà cung cấp, "shop này ở đâu" | branches: cửa hàng theo thành phố
policy: giao hàng/đổi trả/bảo hành | guide_faq: hướng dẫn dùng web/app | taxonomy: danh mục/chất liệu | workshop: xưởng gần
design_task: task 3D | order_status: đơn hàng của tôi | promotion | product_search: tìm sản phẩm
recommend: tư vấn theo nhu cầu, rẻ hơn/nhỏ hơn | product_detail: giá/thông tin 1 sản phẩm | inventory: còn hàng | compare
change_request: yêu cầu SỬA/XÓA/TẠO dữ liệu | cart | out_of_scope: không liên quan WoodHub | unclear"""

SYSTEM_PROMPT = """Phân loại ý định cho trợ lý khách hàng của sàn nội thất WoodHub (nhiều nhà cung cấp). CHỈ trả JSON. Câu có thể không dấu/teencode/tiếng Anh/nhiều ý.
Intent: {guide}
JSON: {{"intents":[{{"intent":"<intent>","span":"<đoạn nguyên văn>"}}],"language":"vi|en|mixed"}}
Ví dụ: "giá KTV01 và bảo hành bao lâu" -> {{"intents":[{{"intent":"product_detail","span":"giá KTV01"}},{{"intent":"policy","span":"bảo hành bao lâu"}}],"language":"vi"}}"""

_ALIASES = {"price": Intent.PRODUCT_DETAIL, "product_price": Intent.PRODUCT_DETAIL, "detail": Intent.PRODUCT_DETAIL,
            "search": Intent.PRODUCT_SEARCH, "recommendation": Intent.RECOMMEND, "stock": Intent.INVENTORY,
            "faq": Intent.GUIDE_FAQ, "guide": Intent.GUIDE_FAQ, "store_info": Intent.SUPPLIER_INFO,
            "store": Intent.SUPPLIER_INFO, "supplier": Intent.SUPPLIER_INFO, "branch": Intent.BRANCHES,
            "order": Intent.ORDER_STATUS, "update_price": Intent.CHANGE_REQUEST, "update_description": Intent.CHANGE_REQUEST,
            "adjust_inventory": Intent.CHANGE_REQUEST, "create_promotion": Intent.CHANGE_REQUEST}


def parse_llm_output(text: str, message: str) -> tuple[list[tuple[Intent, str]], str]:
    """Validate chặt output của LLM. Ném ValueError nếu không dùng được."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json")
    data = json.loads(text[start:end + 1])
    items = data.get("intents")
    if not isinstance(items, list) or not items:
        raise ValueError("no intents")
    folded_msg = fold(message)
    out: list[tuple[Intent, str]] = []
    for it in items[:4]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("intent", "")).strip().lower()
        intent = _ALIASES.get(name)
        if intent is None:
            try:
                intent = Intent(name)
            except ValueError:
                continue
        span = str(it.get("span") or "").strip()
        # span phải là đoạn trong câu gốc; nếu không, dùng cả câu (không tin nội dung LLM tự viết)
        if not span or fold(span) not in folded_msg:
            span = message
        out.append((intent, span))
    if not out:
        raise ValueError("no valid intents")
    lang = data.get("language") if data.get("language") in ("vi", "en", "mixed") else "vi"
    return out, lang


class LLMIntentClassifier:
    def __init__(self, client: LLMClient):
        self._client = client
        self._system = SYSTEM_PROMPT.format(guide=INTENT_GUIDE)

    async def classify(self, message: str, context: str | None) -> tuple[list[tuple[Intent, str]], str, dict[str, Any]]:
        user = (f"NGỮ CẢNH:\n{context}\n\n" if context else "") + f"CÂU NGƯỜI DÙNG: {message}"
        text = await self._client.complete(system=self._system, user=user)
        try:
            frames, lang = parse_llm_output(text, message)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"invalid NLU output: {exc}; raw={text[:200]!r}") from exc
        return frames, lang, {"raw": text[:1000]}


def sanitize_context_line(text: str, limit: int = 120) -> str:
    """Ngữ cảnh gửi LLM: chỉ tên sản phẩm/câu hỏi ngắn, bỏ ký tự điều khiển (giảm injection gián tiếp)."""
    return re.sub(r"[\r\n\t{}\"`]", " ", text or "")[:limit]
