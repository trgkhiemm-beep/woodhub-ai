"""
Pending action store + ActionService (xác nhận → thực thi → xác minh → audit).

Bảo đảm:
  - Chỉ đúng người tạo action, đúng mã xác nhận, trong hạn mới thực thi được.
  - Mỗi action thực thi TỐI ĐA một lần (lock + state machine); xác nhận lặp lại trả về kết quả cũ.
  - Dữ liệu cũ (đã bị thay đổi từ lúc đề xuất) → không thực thi.
  - Không báo "thành công" nếu chưa xác minh được bằng cách đọc lại source of truth.

Store hiện là in-memory (một instance). Chạy nhiều instance cần repository dùng chung (Redis/DB) —
xem docs/ARCHITECTURE.md "Known limitations".
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from app.audit import AuditLogger
from app.config import Settings
from app.domain import errors
from app.domain.actions import (
    ALLOWED_TRANSITIONS, TERMINAL_STATES, ActionState, ConfirmationLevel, PendingAction,
)
from app.domain.principal import Principal
from app.tools.base import Proposal, ToolContext, ToolSpec
from app.tools.registry import ToolRegistry

logger = logging.getLogger("woodhub.actions")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_CODE_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ActionError(Exception):
    def __init__(self, code: str, message: str, action: PendingAction | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.action = action


class InMemoryActionRepository:
    def __init__(self, max_items: int = 10000):
        self._items: OrderedDict[str, PendingAction] = OrderedDict()
        self._attempts: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._max = max_items

    def add(self, action: PendingAction) -> None:
        self._items[action.id] = action
        self._locks[action.id] = asyncio.Lock()
        while len(self._items) > self._max:
            old_id, _ = self._items.popitem(last=False)
            self._locks.pop(old_id, None)
            self._attempts.pop(old_id, None)

    def get(self, action_id: str) -> PendingAction | None:
        return self._items.get(action_id)

    def save(self, action: PendingAction) -> None:
        self._items[action.id] = action

    def lock(self, action_id: str) -> asyncio.Lock:
        return self._locks.setdefault(action_id, asyncio.Lock())

    def failed_attempt(self, action_id: str) -> int:
        self._attempts[action_id] = self._attempts.get(action_id, 0) + 1
        return self._attempts[action_id]

    def pending_for(self, user_id: str, session_id: str | None = None) -> list[PendingAction]:
        return [a for a in self._items.values() if a.owner_user_id == user_id and a.state == ActionState.PENDING_CONFIRMATION
                and (session_id is None or a.session_id == session_id)]


class ActionService:
    def __init__(self, repo: InMemoryActionRepository, registry: ToolRegistry, audit: AuditLogger, settings: Settings):
        self.repo = repo
        self.registry = registry
        self.audit = audit
        self.settings = settings

    # ------------------------------------------------------------------ propose
    def propose(self, spec: ToolSpec, proposal: Proposal, ctx: ToolContext) -> PendingAction:
        principal = ctx.principal
        self._expire_stale(principal.audit_id)
        if len(self.repo.pending_for(principal.audit_id)) >= self.settings.MAX_PENDING_ACTIONS_PER_USER:
            raise ActionError("TOO_MANY_PENDING", "Bạn đang có quá nhiều thay đổi chờ xác nhận. Hãy xác nhận hoặc hủy bớt.")
        level = ConfirmationLevel.STRONG if proposal.escalate_to_strong else spec.confirmation
        ttl = self.settings.STRONG_ACTION_TTL_SECONDS if level == ConfirmationLevel.STRONG else self.settings.ACTION_TTL_SECONDS
        now = _now()
        action = PendingAction(
            id=str(uuid.uuid4()), tool=spec.name, operation=spec.operation.value, state=ActionState.PENDING_CONFIRMATION,
            confirmation_level=level, confirmation_code="".join(secrets.choice(_CODE_ALPHABET) for _ in range(6)),
            owner_user_id=principal.audit_id, owner_role=principal.role.value, session_id=ctx.session_id,
            request_id=ctx.request_id, target_type=proposal.target_type, target_id=proposal.target_id,
            target_label=proposal.target_label, summary=proposal.summary, changes=proposal.changes,
            warnings=proposal.warnings, params=proposal.params, snapshot=proposal.snapshot,
            created_at=now, expires_at=now + timedelta(seconds=ttl), updated_at=now,
        )
        self.repo.add(action)
        self._audit("action.proposed", action, principal, ctx.request_id, status=action.state.value)
        return action

    # ------------------------------------------------------------------ confirm
    async def confirm(self, action_id: str, code: str, ctx: ToolContext) -> PendingAction:
        principal = ctx.principal
        action = self._owned(action_id, principal)
        async with self.repo.lock(action_id):
            action = self.repo.get(action_id)
            assert action is not None
            if action.state in TERMINAL_STATES or action.state == ActionState.VERIFIED:
                return action  # idempotent: xác nhận lặp lại trả kết quả đã có, không thực thi lần 2
            if action.state != ActionState.PENDING_CONFIRMATION:
                raise ActionError("ACTION_IN_PROGRESS", "Thay đổi đang được xử lý.", action)
            if _now() >= action.expires_at:
                self._transition(action, ActionState.EXPIRED)
                self._audit("action.expired", action, principal, ctx.request_id, status=action.state.value)
                raise ActionError("ACTION_EXPIRED", "Yêu cầu xác nhận đã hết hạn. Vui lòng tạo lại yêu cầu.", action)
            if not hmac.compare_digest(action.confirmation_code.upper(), (code or "").strip().upper()):
                attempts = self.repo.failed_attempt(action_id)
                if attempts >= MAX_CODE_ATTEMPTS:
                    self._transition(action, ActionState.CANCELLED, error_code="TOO_MANY_ATTEMPTS")
                    self._audit("action.cancelled", action, principal, ctx.request_id, status=action.state.value,
                                error="too_many_confirmation_attempts")
                    raise ActionError("ACTION_CANCELLED", "Nhập sai mã quá nhiều lần, yêu cầu đã bị hủy.", action)
                raise ActionError("CONFIRMATION_MISMATCH", "Mã xác nhận không đúng.", action)

            spec = self.registry.get(action.tool)
            if spec is None or spec.mutation is None:
                raise ActionError("UNKNOWN_TOOL", "Thao tác không còn được hỗ trợ.", action)
            decision = self.registry.check(spec, principal, ctx.profile)
            if not decision.allowed:  # quyền có thể đã thay đổi kể từ lúc đề xuất
                self._transition(action, ActionState.CANCELLED, error_code="PERMISSION_REVOKED")
                self._audit("action.cancelled", action, principal, ctx.request_id, status=action.state.value, error="permission_revoked")
                raise ActionError("FORBIDDEN", decision.reason or "Không đủ quyền.", action)

            self._transition(action, ActionState.CONFIRMED)
            self._audit("action.confirmed", action, principal, ctx.request_id, status=action.state.value, confirmation="code_match")
            return await self._execute(spec, action, ctx)

    async def _execute(self, spec: ToolSpec, action: PendingAction, ctx: ToolContext) -> PendingAction:
        assert spec.mutation is not None
        try:
            await spec.mutation.check_fresh(action, ctx)
        except errors.PortError as exc:
            code = "STALE_DATA" if isinstance(exc, errors.Conflict) else exc.code
            self._transition(action, ActionState.FAILED, error_code=code, error_message=exc.message)
            self._audit("action.failed", action, ctx.principal, ctx.request_id, status=action.state.value, error=code)
            return action

        self._transition(action, ActionState.EXECUTING)
        try:
            result = await spec.mutation.execute(action, ctx)
        except (errors.UpstreamTimeout, errors.MalformedResponse) as exc:
            # Không biết Backend đã ghi hay chưa → KHÔNG được báo thành công, KHÔNG tự thử lại.
            self._transition(action, ActionState.UNVERIFIED, error_code=exc.code,
                             error_message="Không xác định được thay đổi đã được ghi hay chưa. Vui lòng kiểm tra lại trước khi thử lại.")
            self._audit("action.unverified", action, ctx.principal, ctx.request_id, status=action.state.value, error=exc.code)
            return action
        except errors.PortError as exc:
            self._transition(action, ActionState.FAILED, error_code=exc.code, error_message=exc.message)
            self._audit("action.failed", action, ctx.principal, ctx.request_id, status=action.state.value, error=exc.code)
            return action

        action.result = result
        try:
            verified = await spec.mutation.verify(action, result, ctx)
        except errors.PortError as exc:
            logger.warning("verify error action=%s code=%s", action.id, exc.code)
            verified = False
        if verified:
            self._transition(action, ActionState.VERIFIED)
            self._transition(action, ActionState.COMPLETED)
            self._audit("action.completed", action, ctx.principal, ctx.request_id, status=action.state.value)
        else:
            self._transition(action, ActionState.UNVERIFIED, error_code="VERIFICATION_FAILED",
                             error_message="Đã gửi thay đổi nhưng chưa xác minh được kết quả trên hệ thống.")
            self._audit("action.unverified", action, ctx.principal, ctx.request_id, status=action.state.value, error="verification_failed")
        return action

    # ------------------------------------------------------------------ cancel / get
    def cancel(self, action_id: str, principal: Principal, request_id: str) -> PendingAction:
        action = self._owned(action_id, principal)
        if action.state == ActionState.PENDING_CONFIRMATION:
            self._transition(action, ActionState.CANCELLED)
            self._audit("action.cancelled", action, principal, request_id, status=action.state.value, confirmation="user_cancelled")
        return action

    def get(self, action_id: str, principal: Principal) -> PendingAction:
        action = self._owned(action_id, principal)
        if action.state == ActionState.PENDING_CONFIRMATION and _now() >= action.expires_at:
            self._transition(action, ActionState.EXPIRED)
        return action

    def pending_in_session(self, principal: Principal, session_id: str) -> list[PendingAction]:
        self._expire_stale(principal.audit_id)
        return self.repo.pending_for(principal.audit_id, session_id)

    # ------------------------------------------------------------------ helpers
    def _owned(self, action_id: str, principal: Principal) -> PendingAction:
        action = self.repo.get(action_id)
        # Không tiết lộ action của người khác có tồn tại hay không.
        if action is None or action.owner_user_id != principal.audit_id or not principal.is_authenticated:
            raise ActionError("ACTION_NOT_FOUND", "Không tìm thấy yêu cầu thay đổi.")
        return action

    def _expire_stale(self, user_id: str) -> None:
        now = _now()
        for a in self.repo.pending_for(user_id):
            if now >= a.expires_at:
                self._transition(a, ActionState.EXPIRED)

    def _transition(self, action: PendingAction, new: ActionState, *, error_code: str | None = None,
                    error_message: str | None = None) -> None:
        if new not in ALLOWED_TRANSITIONS.get(action.state, frozenset()):
            raise ActionError("INVALID_TRANSITION", f"Không thể chuyển {action.state.value} → {new.value}", action)
        action.state = new
        action.updated_at = _now()
        if error_code:
            action.error_code = error_code
        if error_message:
            action.error_message = error_message
        self.repo.save(action)

    def _audit(self, event: str, action: PendingAction, principal: Principal, request_id: str, *, status: str,
               confirmation: str | None = None, error: str | None = None) -> None:
        self.audit.log(
            event, request_id=request_id, user_id=principal.audit_id, role=principal.role.value, status=status,
            action=action.operation, action_id=action.id, tool=action.tool,
            target={"type": action.target_type, "id": action.target_id, "label": action.target_label},
            before={c.field: c.before for c in action.changes}, after={c.field: c.after for c in action.changes},
            confirmation=confirmation or action.confirmation_level.value, error=error or action.error_code,
            extra={"proposal_request_id": action.request_id, "session_id": action.session_id},
        )
