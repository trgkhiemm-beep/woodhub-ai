"""
Agent API v1 (public contract — xem docs/FRONTEND_INTEGRATION.md, contracts/agent-api.openapi.json).

/v1/agent/chat          trợ lý khách hàng, CHỈ ĐỌC
/v1/agent/manage/chat   cùng trợ lý (Backend /api/admin/ai-agent/chat gọi tới) — giữ route và schema cho tương thích
/v1/agent/actions/{id}  giữ cho tương thích Backend; agent chỉ đọc nên không còn action để xem/xác nhận/hủy

Agent KHÔNG xác thực/phân quyền người dùng: Backend quyết định ai được gọi các endpoint này (vd chỉ admin mới tới
được /api/admin/ai-agent/*). Bảo vệ server-to-server tùy chọn: header X-Agent-Api-Key (AGENT_SERVICE_API_KEY).
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.deps import caller, get_container, rate_limited_caller, request_id
from app.api.schemas import ActionOut, AgentResponse, ChatRequest, ConfirmRequest, ErrorResponse
from app.container import Container
from app.domain.messages import ACTIONS_DISABLED
from app.domain.principal import Principal
from app.tools.base import AgentProfile

router = APIRouter(prefix="/v1/agent", tags=["agent"])
# 401 chỉ còn cho khóa server-to-server (SERVICE_UNAUTHORIZED) khi được bật — không bao giờ vì người dùng chưa đăng nhập.
ERRORS = {401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}}


async def _turn(req: ChatRequest, principal: Principal, profile: AgentProfile, rid: str, c: Container) -> AgentResponse:
    loc = (req.location.lat, req.location.lng) if req.location else None
    return await c.agent.handle_turn(message=req.message, principal=principal, profile=profile, request_id=rid,
                                     session_id=req.session_id, location=loc)


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def stream_response(resp: AgentResponse) -> AsyncIterator[str]:
    """Luồng SSE chuẩn: meta → delta* → block* → done | error."""
    yield sse("meta", {"session_id": resp.session_id, "request_id": resp.request_id, "type": resp.type,
                       "meta": resp.meta.model_dump(mode="json")})
    for i in range(0, len(resp.message), 80):
        yield sse("delta", {"text": resp.message[i:i + 80]})
    for block in resp.blocks:
        yield sse("block", block.model_dump(mode="json"))
    if resp.sources:
        yield sse("sources", [s.model_dump(mode="json") for s in resp.sources])
    if resp.error:
        yield sse("error", resp.error.model_dump(mode="json"))
    yield sse("done", {"type": resp.type})


def _stream(resp: AgentResponse) -> StreamingResponse:
    return StreamingResponse(stream_response(resp), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-Id": resp.request_id})


@router.post("/chat", response_model=AgentResponse, responses=ERRORS)
async def customer_chat(req: ChatRequest, principal: Principal = Depends(rate_limited_caller),
                        rid: str = Depends(request_id), c: Container = Depends(get_container)) -> AgentResponse:
    return await _turn(req, principal, AgentProfile.CUSTOMER, rid, c)


@router.post("/chat/stream", responses=ERRORS)
async def customer_chat_stream(req: ChatRequest, principal: Principal = Depends(rate_limited_caller),
                               rid: str = Depends(request_id), c: Container = Depends(get_container)) -> StreamingResponse:
    return _stream(await _turn(req, principal, AgentProfile.CUSTOMER, rid, c))


@router.post("/manage/chat", response_model=AgentResponse, responses=ERRORS)
async def manage_chat(req: ChatRequest, principal: Principal = Depends(rate_limited_caller),
                      rid: str = Depends(request_id), c: Container = Depends(get_container)) -> AgentResponse:
    return await _turn(req, principal, AgentProfile.MANAGEMENT, rid, c)


@router.post("/manage/chat/stream", responses=ERRORS)
async def manage_chat_stream(req: ChatRequest, principal: Principal = Depends(rate_limited_caller),
                             rid: str = Depends(request_id), c: Container = Depends(get_container)) -> StreamingResponse:
    return _stream(await _turn(req, principal, AgentProfile.MANAGEMENT, rid, c))


@router.get("/actions/{action_id}", response_model=ActionOut, responses={**ERRORS, 404: {"model": ErrorResponse}})
async def get_action(action_id: str, principal: Principal = Depends(caller)) -> ActionOut:
    raise HTTPException(status_code=404, detail={"code": "ACTION_NOT_FOUND", "message": ACTIONS_DISABLED})


@router.post("/actions/{action_id}/confirm", response_model=AgentResponse, responses=ERRORS)
async def confirm_action(action_id: str, body: ConfirmRequest, principal: Principal = Depends(rate_limited_caller),
                         rid: str = Depends(request_id), c: Container = Depends(get_container)) -> AgentResponse:
    return await c.agent.confirm_action(action_id=action_id, code=body.confirmation_code, principal=principal,
                                        profile=AgentProfile.MANAGEMENT, request_id=rid, session_id=body.session_id)


@router.post("/actions/{action_id}/cancel", response_model=AgentResponse, responses=ERRORS)
async def cancel_action(action_id: str, principal: Principal = Depends(rate_limited_caller),
                        rid: str = Depends(request_id), c: Container = Depends(get_container)) -> AgentResponse:
    return c.agent.cancel_action(action_id=action_id, principal=principal, profile=AgentProfile.MANAGEMENT, request_id=rid)


@router.get("/capabilities", tags=["agent"])
async def capabilities(profile: AgentProfile = AgentProfile.CUSTOMER, principal: Principal = Depends(caller),
                       c: Container = Depends(get_container)) -> dict:
    """Danh sách chức năng (tool chỉ đọc) của trợ lý — giống nhau cho mọi người gọi. Không chứa dữ liệu nhạy cảm."""
    return {"role": "customer", "profile": profile.value,
            "tools": [{"name": s.name, "description": s.description, "operation": s.operation.value, "risk": s.risk}
                      for s in c.registry.all()]}
