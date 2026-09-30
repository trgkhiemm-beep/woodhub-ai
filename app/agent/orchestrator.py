"""
AgentService — điều phối một lượt hội thoại.

REQUEST → (xác nhận/hủy action?) → PLAN (rules | LLM) → TOOL qua executor (validate + permission)
        → nếu mutation: WAITING_CONFIRMATION (dừng, không thực thi)
        → COMPOSE (chỉ từ dữ liệu tool) → RESPONSE (contract app/api/schemas.py)
"""
from __future__ import annotations

import logging

from app.agent import composer
from app.agent.actions import ActionError, ActionService
from app.agent.executor import ToolExecutor
from app.agent.llm import LLMPlanner, LLMUnavailable
from app.agent.planner import Plan, RulePlanner
from app.agent.session import SessionStore
from app.api.schemas import AgentResponse, ErrorOut, MetaOut
from app.audit import AuditLogger
from app.config import Settings
from app.domain.actions import PendingAction
from app.domain.principal import Principal
from app.domain.results import ToolResult, ToolStatus
from app.ports import Ports
from app.tools.base import AgentProfile, ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger("woodhub.agent")

NEEDS_MESSAGES = {
    "value": "Chương trình khuyến mãi giảm bao nhiêu (ví dụ 20% hoặc 500.000đ)?",
    "promotion": "Bạn muốn đổi trạng thái khuyến mãi nào? Vui lòng cho biết mã hoặc id khuyến mãi.",
    "sku": "Bạn cho mình mã SKU của sản phẩm cần thay đổi nhé (ví dụ OAK-01).",
    "price": "Giá mới là bao nhiêu (ví dụ 8.000.000đ hoặc 8 triệu)?",
    "description": "Nội dung mô tả mới là gì? Ví dụ: \"Cập nhật mô tả OAK-01: …\"",
    "hotline": "Số hotline mới là gì?",
    "email": "Email mới là gì?",
    "opening_hours": "Giờ mở cửa mới là mấy giờ đến mấy giờ (ví dụ 8h-21h)?",
    "address": "Địa chỉ mới là gì?",
    "faq": "Hãy gửi theo mẫu: Thêm FAQ: hỏi: <câu hỏi> | trả lời: <câu trả lời>",
}


class AgentService:
    def __init__(self, *, settings: Settings, ports: Ports, registry: ToolRegistry, executor: ToolExecutor,
                 actions: ActionService, sessions: SessionStore, audit: AuditLogger, llm: LLMPlanner | None = None):
        self.settings = settings
        self.ports = ports
        self.registry = registry
        self.executor = executor
        self.actions = actions
        self.sessions = sessions
        self.audit = audit
        self.rules = RulePlanner()
        self.llm = llm

    # ------------------------------------------------------------------ public
    async def handle_turn(self, *, message: str, principal: Principal, profile: AgentProfile, request_id: str,
                          session_id: str | None = None, location: tuple[float, float] | None = None) -> AgentResponse:
        session = self.sessions.get_or_create(session_id, principal)
        ctx = ToolContext(principal=principal, ports=self.ports, settings=self.settings, request_id=request_id,
                          session_id=session.id, profile=profile, conversation=session.conversation, location=location)
        if len(message) > self.settings.MAX_MESSAGE_CHARS:
            return self._response(ctx, "error", f"Tin nhắn quá dài (tối đa {self.settings.MAX_MESSAGE_CHARS} ký tự).",
                                  error=ErrorOut(code="MESSAGE_TOO_LONG", message="Tin nhắn quá dài."))

        plan = self.rules.plan(message, session.conversation, has_location=location is not None)
        if "injection_suspected" in plan.flags:
            self.audit.log("security.injection_suspected", request_id=request_id, user_id=principal.audit_id,
                           role=principal.role.value, status="flagged", extra={"message_excerpt": message[:200]})

        if plan.confirm:
            return await self._confirm_from_chat(ctx, plan.confirm_code)
        if plan.cancel:
            return self._cancel_from_chat(ctx)

        planner_name = "rules"
        results: list[ToolResult] = []
        action: PendingAction | None = None
        llm_text: str | None = None
        tools_used: list[str] = []

        use_llm = self.llm is not None and plan.direct not in ("greeting", "out_of_scope") and not plan.calls_need_input()
        if use_llm:
            try:
                assert self.llm is not None
                out = await self.llm.run(message, ctx, self.registry.available(principal, profile))
                if out.results or out.action:
                    planner_name, results, action, llm_text, tools_used = "llm", out.results, out.action, out.text, out.tools_used
            except LLMUnavailable as exc:
                logger.warning("LLM unavailable, fallback to rules: %s", exc)

        if planner_name == "rules":
            if plan.direct:
                return self._direct(ctx, plan)
            for call in plan.calls[: self.settings.MAX_TOOL_CALLS_PER_TURN]:
                if "_needs" in call.args:
                    tool = self.registry.get(call.name)
                    if tool is not None:
                        decision = self.registry.check(tool, principal, profile)
                        if not decision.allowed:
                            results.append(ToolResult(tool=call.name, status=ToolStatus.DENIED, message=decision.reason))
                            self.audit.log("permission.denied", request_id=request_id, user_id=principal.audit_id,
                                           role=principal.role.value, status="denied", tool=call.name, error=decision.reason)
                            break
                    results.append(ToolResult(tool=call.name, status=ToolStatus.NEEDS_INPUT,
                                              message=NEEDS_MESSAGES.get(call.args["_needs"], composer.CLARIFY)))
                    break
                tools_used.append(call.name)
                outcome = await self.executor.run(call.name, call.args, ctx)
                results.append(outcome.result)
                if outcome.action is not None:
                    action = outcome.action
                    break

        if action is not None:
            return self._response(ctx, "confirmation_required", composer.confirmation_message(action),
                                  action=composer.action_view(action, include_code=True), planner=planner_name,
                                  tools_used=tools_used, injection="injection_suspected" in plan.flags)

        composed = composer.compose_results(results)
        text = "\n".join(composed.lines) or composer.CLARIFY
        if planner_name == "llm" and llm_text and composer.grounded(llm_text, results):
            text = llm_text
        rtype = composed.kind if composed.kind in ("clarification", "error") else "answer"
        if rtype == "error":
            err = next((r for r in results if r.status == ToolStatus.ERROR), None)
            return self._response(ctx, "error", text, blocks=composed.blocks, sources=composed.sources, planner=planner_name,
                                  tools_used=tools_used, error=ErrorOut(code=(err.error_code if err else None) or "UPSTREAM_ERROR", message=text))
        return self._response(ctx, rtype, text, blocks=composed.blocks, sources=composed.sources, planner=planner_name,
                              tools_used=tools_used, injection="injection_suspected" in plan.flags)

    async def confirm_action(self, *, action_id: str, code: str, principal: Principal, profile: AgentProfile,
                             request_id: str, session_id: str | None = None) -> AgentResponse:
        session = self.sessions.get_or_create(session_id, principal)
        ctx = ToolContext(principal=principal, ports=self.ports, settings=self.settings, request_id=request_id,
                          session_id=session.id, profile=profile, conversation=session.conversation)
        return await self._do_confirm(ctx, action_id, code)

    def cancel_action(self, *, action_id: str, principal: Principal, profile: AgentProfile, request_id: str,
                      session_id: str | None = None) -> AgentResponse:
        session = self.sessions.get_or_create(session_id, principal)
        ctx = ToolContext(principal=principal, ports=self.ports, settings=self.settings, request_id=request_id,
                          session_id=session.id, profile=profile, conversation=session.conversation)
        try:
            action = self.actions.cancel(action_id, principal, request_id)
        except ActionError as exc:
            return self._response(ctx, "error", exc.message, error=ErrorOut(code=exc.code, message=exc.message))
        return self._response(ctx, "action_result", composer.action_result_message(action),
                              action=composer.action_view(action, include_code=False))

    def get_action(self, *, action_id: str, principal: Principal) -> PendingAction:
        return self.actions.get(action_id, principal)

    # ------------------------------------------------------------------ internals
    async def _confirm_from_chat(self, ctx: ToolContext, code: str | None) -> AgentResponse:
        pending = self.actions.pending_in_session(ctx.principal, ctx.session_id)
        if not pending:
            return self._response(ctx, "clarification", "Hiện không có thay đổi nào đang chờ xác nhận.")
        if not code:
            codes = ", ".join(f"\"xác nhận {a.confirmation_code}\" ({a.summary})" for a in pending)
            return self._response(ctx, "clarification",
                                  f"Để tránh xác nhận nhầm, vui lòng gửi kèm mã xác nhận: {codes}.")
        target = next((a for a in pending if a.confirmation_code == code), None)
        if target is None:
            if len(pending) == 1:
                target = pending[0]  # để ActionService đếm số lần nhập sai mã
            else:
                return self._response(ctx, "error", "Mã xác nhận không khớp với thay đổi nào đang chờ.",
                                      error=ErrorOut(code="CONFIRMATION_MISMATCH", message="Mã xác nhận không đúng."))
        return await self._do_confirm(ctx, target.id, code)

    async def _do_confirm(self, ctx: ToolContext, action_id: str, code: str) -> AgentResponse:
        try:
            action = await self.actions.confirm(action_id, code, ctx)
        except ActionError as exc:
            view = composer.action_view(exc.action, include_code=False) if exc.action else None
            return self._response(ctx, "error", exc.message, action=view, error=ErrorOut(code=exc.code, message=exc.message))
        return self._response(ctx, "action_result", composer.action_result_message(action),
                              action=composer.action_view(action, include_code=False))

    def _cancel_from_chat(self, ctx: ToolContext) -> AgentResponse:
        pending = self.actions.pending_in_session(ctx.principal, ctx.session_id)
        if not pending:
            return self._response(ctx, "clarification", "Hiện không có thay đổi nào đang chờ để hủy.")
        cancelled = [self.actions.cancel(a.id, ctx.principal, ctx.request_id) for a in pending]
        last = cancelled[-1]
        msg = composer.action_result_message(last) if len(cancelled) == 1 else f"Đã hủy {len(cancelled)} thay đổi đang chờ xác nhận."
        return self._response(ctx, "action_result", msg, action=composer.action_view(last, include_code=False))

    def _direct(self, ctx: ToolContext, plan: Plan) -> AgentResponse:
        msg = {"greeting": composer.GREETING, "out_of_scope": composer.OUT_OF_SCOPE, "clarify": composer.CLARIFY,
               "unsupported_cart": composer.UNSUPPORTED_CART}.get(plan.direct or "clarify", composer.CLARIFY)
        if plan.note == "ambiguous_product":
            msg = "Bạn đang hỏi sản phẩm nào? Vui lòng cho mình tên hoặc mã sản phẩm."
        rtype = "answer" if plan.direct in ("greeting", "out_of_scope", "unsupported_cart") else "clarification"
        return self._response(ctx, rtype, msg, injection="injection_suspected" in plan.flags)

    def _response(self, ctx: ToolContext, rtype: str, message: str, *, blocks=None, sources=None, action=None,
                  error: ErrorOut | None = None, planner: str = "rules", tools_used: list[str] | None = None,
                  injection: bool = False) -> AgentResponse:
        if injection and rtype != "error":
            message = f"{message}\n{composer.INJECTION_NOTE}"
        return AgentResponse(
            type=rtype, message=message, session_id=ctx.session_id, request_id=ctx.request_id,
            blocks=blocks or [], sources=sources or [], action=action, error=error,
            meta=MetaOut(profile=ctx.profile.value, role=ctx.principal.role.value, planner=planner,
                         tools_used=tools_used or []),
        )
