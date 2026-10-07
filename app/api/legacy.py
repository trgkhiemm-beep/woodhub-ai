"""
DEPRECATED — tương thích ngược cho POST /chat (định dạng request/SSE cũ).

Không có logic riêng: chuyển thẳng vào Customer Agent, chỉ đổi hình dạng dữ liệu vào/ra.
Giữ lại vì Frontend hiện tại và luồng Backend /api/ai-chat/... (chưa rõ contract, BLOCKER B-2) có thể đang gọi.
Payload sản phẩm giữ các key id/name/description/price/status (khớp chat_messages.suggested_products của Backend).
Gỡ bỏ khi mọi client chuyển sang /v1/agent/chat.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_container, rate_limited_caller, request_id
from app.api.schemas import AgentResponse, Location
from app.container import Container
from app.domain.principal import Principal
from app.tools.base import AgentProfile

router = APIRouter(tags=["legacy"])

IMAGE_TO_3D_MESSAGE = ("Tính năng tạo mẫu 3D từ ảnh nay được xử lý trong Phòng thiết kế 3D của WoodHub "
                       "(cần đăng nhập, có hạn mức lượt dùng). Bạn vui lòng tải ảnh lên tại đó nhé.")


class LegacyChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    session_id: str = Field(min_length=1, max_length=100)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    image_url: str | None = None


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _products(resp: AgentResponse) -> list[dict]:
    items: list[dict] = []
    for b in resp.blocks:
        if b.kind == "recommendation":
            items += [{"id": p["id"], "name": p["name"], "description": None, "price": p.get("price"),
                       "status": "active", "image_url": p.get("image_url")} for p in b.data["items"]]
        elif b.kind == "product_detail":
            pr = b.data.get("price_range") or [None]
            items.append({"id": b.data["id"], "name": b.data["name"], "description": b.data.get("description"),
                          "price": pr[0], "status": b.data.get("status"),
                          "image_url": (b.data.get("image_urls") or [None])[0]})
    return items


async def _legacy_stream(resp: AgentResponse) -> AsyncIterator[str]:
    for i in range(0, len(resp.message), 80):
        yield _event({"type": "chunk", "content": resp.message[i:i + 80]})
    products = _products(resp)
    stores = [x for b in resp.blocks if b.kind in ("branch_list", "workshop_list") for x in b.data]
    if products and stores:
        yield _event({"type": "mixed_data", "products": products, "stores": stores})
    elif products:
        yield _event({"type": "debug_data", "payload": products})
    yield _event({"type": "done"})


@router.post("/chat", deprecated=True)
async def legacy_chat(req: LegacyChatRequest, principal: Principal = Depends(rate_limited_caller),
                      rid: str = Depends(request_id), c: Container = Depends(get_container)) -> StreamingResponse:
    if req.image_url:
        async def only_message() -> AsyncIterator[str]:
            yield _event({"type": "chunk", "content": IMAGE_TO_3D_MESSAGE})
            yield _event({"type": "done"})
        return StreamingResponse(only_message(), media_type="text/event-stream")
    loc = (req.lat, req.lng) if req.lat is not None and req.lng is not None else None
    if loc and not Location(lat=loc[0], lng=loc[1]).is_real:
        loc = None  # giá trị mẫu Swagger (-90,-180) / (0,0): không phải GPS thật
    resp = await c.agent.handle_turn(message=req.query, principal=principal, profile=AgentProfile.CUSTOMER,
                                     request_id=rid, session_id=req.session_id, location=loc)
    return StreamingResponse(_legacy_stream(resp), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-Id": rid})
