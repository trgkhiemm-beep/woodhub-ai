"""
AgentService — điều phối một lượt hội thoại.

REQUEST → NLU (LLM có kiểm soát + trích xuất deterministic) → với MỖI ý:
  Planner (ngữ cảnh, làm rõ) → Tool qua executor (permission → validate) → cập nhật memory
  mutation: WAITING_CONFIRMATION (dừng, không thực thi)
→ COMPOSE (chỉ từ dữ liệu tool) → RESPONSE (contract app/api/schemas.py)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent import composer
from app.agent.actions import ActionError, ActionService
from app.agent.dialogue import DialogueState, ShownProduct
from app.agent.executor import ToolExecutor
from app.agent.planner import Planner, Step
from app.agent.session import SessionStore
from app.api.schemas import AgentResponse, ErrorOut, MetaOut
from app.audit import AuditLogger
from app.config import Settings
from app.domain import errors
from app.domain.actions import PendingAction
from app.domain.principal import Principal
from app.domain.results import ToolResult, ToolStatus
from app.nlu.engine import NLUEngine
from app.nlu.schema import Intent, NLUResult
from app.ports import Ports
from app.tools.advisor import parse_dimensions
from app.tools.base import AgentProfile, ToolContext
from app.tools.registry import ToolRegistry

logger = logging.getLogger("woodhub.agent")


@dataclass
class TurnTrace:
    """Dấu vết nội bộ của một lượt (cho evaluation/observability) — không trả cho client."""
    nlu: NLUResult | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    latency_ms: float = 0.0


class AgentService:
    def __init__(self, *, settings: Settings, ports: Ports, registry: ToolRegistry, executor: ToolExecutor,
                 actions: ActionService, sessions: SessionStore, audit: AuditLogger, nlu: NLUEngine):
        self.settings = settings
        self.ports = ports
        self.registry = registry
        self.executor = executor
        self.actions = actions
        self.sessions = sessions
        self.audit = audit
        self.nlu = nlu
        self.planner = Planner()
        self._lexicon_loaded = False

    # ------------------------------------------------------------------ public
    async def handle_turn(self, *, message: str, principal: Principal, profile: AgentProfile, request_id: str,
                          session_id: str | None = None, location: tuple[float, float] | None = None,
                          trace: TurnTrace | None = None) -> AgentResponse:
        started = time.monotonic()
        trace = trace or TurnTrace()
        session = self.sessions.get_or_create(session_id, principal)
        state: DialogueState = session.conversation
        ctx = ToolContext(principal=principal, ports=self.ports, settings=self.settings, request_id=request_id,
                          session_id=session.id, profile=profile, conversation=state, location=location)
        if len(message) > self.settings.MAX_MESSAGE_CHARS:
            return self._response(ctx, "error", f"Tin nhắn quá dài (tối đa {self.settings.MAX_MESSAGE_CHARS} ký tự).",
                                  error=ErrorOut(code="MESSAGE_TOO_LONG", message="Tin nhắn quá dài."))
        await self._load_lexicon(principal)
        nlu = await self.nlu.parse(message, context=state.llm_context(), has_context=state.has_context)
        trace.nlu = nlu
        if nlu.injection_suspected:
            self.audit.log("security.injection_suspected", request_id=request_id, user_id=principal.audit_id,
                           role=principal.role.value, status="flagged", extra={"message_excerpt": message[:200]})

        primary = nlu.primary.intent
        if primary == Intent.CONFIRM:
            resp = await self._confirm_from_chat(ctx, nlu.confirm_code)
            return self._finish(state, message, resp, trace, started)
        if primary == Intent.CANCEL:
            return self._finish(state, message, self._cancel_from_chat(ctx), trace, started)

        segments: list[str] = []
        blocks, sources, tools_used = [], [], []
        results: list[ToolResult] = []
        action: PendingAction | None = None
        clarified = False
        for frame in nlu.frames[: self.settings.MAX_TOOL_CALLS_PER_TURN]:
            step = self.planner.plan(frame, state)
            trace.steps.append({"intent": frame.intent.value, "kind": step.kind, "tool": step.tool, "args": dict(step.args),
                                "entities": frame.entities.model_dump(exclude_defaults=True)})
            if step.kind == "reply":
                segments.append(step.message or "")
                continue
            if step.kind == "clarify":
                denied = self._denied_for(step.tool, ctx) if step.tool else None
                if denied is not None:
                    results.append(denied)
                    segments.append(denied.message or "")
                    continue
                clarified = True
                segments.append(step.message or composer.CLARIFY)
                continue
            frame_results, action = await self._run_step(step, ctx, state, tools_used, trace)
            results += frame_results
            shown = frame_results
            if step.note and any(r.tool == "recommend_products" for r in frame_results):
                # "rẻ hơn/nhỏ hơn": chỉ trả danh sách mới, không lặp lại sản phẩm tham chiếu
                shown = [r for r in frame_results if r.tool != "get_product"]
            composed = composer.compose_results(shown, asked=frame.entities.asked)
            segments.append("\n".join(composed.lines))
            blocks += composed.blocks
            sources += composed.sources
            if composed.kind == "clarification":
                clarified = True
            if action is not None:
                break

        if action is not None:
            resp = self._response(ctx, "confirmation_required", composer.confirmation_message(action),
                                  action=composer.action_view(action, include_code=True), tools_used=tools_used, nlu=nlu)
            return self._finish(state, message, resp, trace, started)

        text = "\n\n".join(s for s in segments if s) or composer.CLARIFY
        ok = any(r.status == ToolStatus.OK for r in results)
        errs = [r for r in results if r.status == ToolStatus.ERROR]
        if errs and not ok:
            resp = self._response(ctx, "error", text, blocks=blocks, sources=sources, tools_used=tools_used, nlu=nlu,
                                  error=ErrorOut(code=errs[0].error_code or "UPSTREAM_ERROR", message=errs[0].message or text))
        else:
            rtype = "clarification" if clarified and not ok else "answer"
            resp = self._response(ctx, rtype, text, blocks=blocks, sources=sources, tools_used=tools_used, nlu=nlu)
        return self._finish(state, message, resp, trace, started)

    async def confirm_action(self, *, action_id: str, code: str, principal: Principal, profile: AgentProfile,
                             request_id: str, session_id: str | None = None) -> AgentResponse:
        ctx = self._ctx(principal, profile, request_id, session_id)
        return await self._do_confirm(ctx, action_id, code)

    def cancel_action(self, *, action_id: str, principal: Principal, profile: AgentProfile, request_id: str,
                      session_id: str | None = None) -> AgentResponse:
        ctx = self._ctx(principal, profile, request_id, session_id)
        try:
            action = self.actions.cancel(action_id, principal, request_id)
        except ActionError as exc:
            return self._response(ctx, "error", exc.message, error=ErrorOut(code=exc.code, message=exc.message))
        return self._response(ctx, "action_result", composer.action_result_message(action),
                              action=composer.action_view(action, include_code=False))

    def get_action(self, *, action_id: str, principal: Principal) -> PendingAction:
        return self.actions.get(action_id, principal)

    # ------------------------------------------------------------------ steps
    async def _run_step(self, step: Step, ctx: ToolContext, state: DialogueState, tools_used: list[str],
                        trace: TurnTrace) -> tuple[list[ToolResult], PendingAction | None]:
        results: list[ToolResult] = []
        if step.needs_reference_detail:
            # Bước 1: đọc sản phẩm tham chiếu thật (giá/kích thước) → Bước 2: tìm mẫu rẻ hơn/nhỏ hơn…
            out = await self.executor.run("get_product", step.args, ctx)
            tools_used.append("get_product")
            trace.results.append(out.result)
            self._update_state(state, out.result)
            if not out.result.ok:
                return [out.result], None
            ref = state.active
            assert ref is not None and step.note is not None
            if step.note == "cheaper":
                results.append(out.result)  # hiển thị giá hiện tại của sản phẩm đang hỏi
            step = Planner.relative_step(step.note, ref, state.constraints, state)
            trace.steps.append({"intent": "relative_followup", "kind": "tool", "tool": step.tool, "args": dict(step.args)})
        assert step.tool is not None
        out = await self.executor.run(step.tool, step.args, ctx)
        tools_used.append(step.tool)
        trace.results.append(out.result)
        self._update_state(state, out.result)
        results.append(out.result)
        return results, out.action

    @staticmethod
    def _update_state(state: DialogueState, r: ToolResult) -> None:
        if r.tool == "get_inventory" and isinstance(r.data, dict) and r.data.get("product_id"):
            if not (state.active and state.active.id == r.data["product_id"]):
                known = next((p for p in state.shown if p.id == r.data["product_id"]), None)
                state.set_active(known or ShownProduct(id=r.data["product_id"], name=r.data["product"]))
            return
        if r.status != ToolStatus.OK or r.data is None:
            return
        if r.tool == "recommend_products":
            items = [ShownProduct(id=i["id"], name=i["name"], price=i.get("price"), area_cm2=i.get("area_cm2"),
                                  category=i.get("category")) for i in r.data["items"]]
            state.set_shown(items)
            if len(items) > 1:
                state.active = None
        elif r.tool == "get_product":
            d = r.data
            pr = d.get("price_range") or [None]
            dims = parse_dimensions(next((v.get("dimensions") for v in d.get("variants", []) if v.get("dimensions")), None))
            state.set_active(ShownProduct(id=d["id"], name=d["name"], price=pr[0], area_cm2=dims.area, category=d.get("category")))
        elif r.tool == "compare_products":
            state.set_shown([ShownProduct(id=x["product_id"], name=x["name"], price=x.get("price"), category=x.get("category"))
                             for x in r.data["rows"]])
            state.active = None

    def _denied_for(self, tool: str, ctx: ToolContext) -> ToolResult | None:
        spec = self.registry.get(tool)
        if spec is None:
            return None
        decision = self.registry.check(spec, ctx.principal, ctx.profile)
        if decision.allowed:
            return None
        self.audit.log("permission.denied", request_id=ctx.request_id, user_id=ctx.principal.audit_id,
                       role=ctx.principal.role.value, status="denied", tool=tool, error=decision.reason)
        return ToolResult(tool=tool, status=ToolStatus.DENIED, message=decision.reason, error_code="FORBIDDEN")

    async def _load_lexicon(self, principal: Principal) -> None:
        """Nạp tên danh mục/chất liệu THẬT vào từ vựng NLU (một lần; lỗi thì dùng từ vựng mặc định)."""
        if self._lexicon_loaded:
            return
        try:
            cats = await self.ports.catalog.list_categories(principal)
            mats = await self.ports.catalog.list_materials(principal)
            self.nlu.lexicon.extend_from_catalog([c.name for c in cats], [m.name for m in mats])
            self._lexicon_loaded = True
        except errors.PortError as exc:
            logger.warning("Không nạp được từ vựng catalog: %s", exc.code)

    # ------------------------------------------------------------------ confirmation
    async def _confirm_from_chat(self, ctx: ToolContext, code: str | None) -> AgentResponse:
        pending = self.actions.pending_in_session(ctx.principal, ctx.session_id)
        if not pending:
            return self._response(ctx, "clarification", "Hiện không có thay đổi nào đang chờ xác nhận.")
        if not code:
            codes = ", ".join(f"\"xác nhận {a.confirmation_code}\" ({a.summary})" for a in pending)
            return self._response(ctx, "clarification", f"Để tránh xác nhận nhầm, vui lòng gửi kèm mã xác nhận: {codes}.")
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

    # ------------------------------------------------------------------ helpers
    def _ctx(self, principal: Principal, profile: AgentProfile, request_id: str, session_id: str | None) -> ToolContext:
        session = self.sessions.get_or_create(session_id, principal)
        return ToolContext(principal=principal, ports=self.ports, settings=self.settings, request_id=request_id,
                           session_id=session.id, profile=profile, conversation=session.conversation)

    @staticmethod
    def _finish(state: DialogueState, message: str, resp: AgentResponse, trace: TurnTrace, started: float) -> AgentResponse:
        state.last_assistant = resp.message[:300]
        state.turns.append(("user", message[:200]))
        state.turns.append(("assistant", resp.message[:200]))
        trace.latency_ms = (time.monotonic() - started) * 1000
        return resp

    def _response(self, ctx: ToolContext, rtype: str, message: str, *, blocks=None, sources=None, action=None,
                  error: ErrorOut | None = None, tools_used: list[str] | None = None, nlu: NLUResult | None = None) -> AgentResponse:
        return AgentResponse(
            type=rtype, message=message, session_id=ctx.session_id, request_id=ctx.request_id,
            blocks=blocks or [], sources=sources or [], action=action, error=error,
            meta=MetaOut(profile=ctx.profile.value, role=ctx.principal.role.value,
                         planner="llm" if nlu is not None and nlu.source == "llm+rules" else "rules",
                         tools_used=tools_used or [],
                         intents=[f.intent.value for f in nlu.frames] if nlu is not None else []),
        )
