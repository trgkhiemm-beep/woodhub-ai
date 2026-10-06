"""
ToolExecutor — cửa duy nhất để chạy tool (dù do rule planner hay LLM chọn).

Pipeline: tool tồn tại? → validate input (Pydantic, extra=forbid) → timeout → handler (CHỈ ĐỌC).
Registry không chứa tool ghi dữ liệu nên không có đường nào để agent thay đổi dữ liệu.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.audit import AuditLogger
from app.domain import errors
from app.domain.results import ToolResult, ToolStatus
from app.tools.base import ToolContext
from app.tools.common import error_result
from app.tools.registry import ToolRegistry

logger = logging.getLogger("woodhub.executor")
TOOL_TIMEOUT_SECONDS = 25.0


@dataclass
class ExecOutcome:
    result: ToolResult


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(x) for x in first.get("loc", ()) if x != "__root__")
    kind = first.get("type", "")
    if kind == "extra_forbidden":
        return f"Tham số không được phép: {loc}."
    if kind == "missing":
        return f"Thiếu thông tin bắt buộc: {loc}."
    if kind.startswith(("greater_than", "less_than", "string_too", "too_", "string_pattern")):
        return f"Giá trị không hợp lệ cho {loc}."
    msg = str(first.get("msg", "Dữ liệu không hợp lệ.")).removeprefix("Value error, ")
    return f"{msg} ({loc})" if loc else msg


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, audit: AuditLogger):
        self.registry = registry
        self.audit = audit

    async def run(self, name: str, raw_args: dict[str, Any] | None, ctx: ToolContext) -> ExecOutcome:
        started = time.monotonic()
        spec = self.registry.get(name)
        if spec is None:
            logger.warning("unknown tool requested: %s", name)
            return ExecOutcome(ToolResult(tool=name, status=ToolStatus.INVALID, message="Chức năng không tồn tại.",
                                          error_code="UNKNOWN_TOOL"))
        try:
            args = spec.input_model.model_validate(raw_args or {})
        except ValidationError as exc:
            return ExecOutcome(ToolResult(tool=name, status=ToolStatus.INVALID, message=_validation_message(exc),
                                          error_code="VALIDATION_ERROR"))

        try:
            return ExecOutcome(await asyncio.wait_for(spec.read_handler(args, ctx), TOOL_TIMEOUT_SECONDS))
        except errors.PortError as exc:
            return ExecOutcome(error_result(name, exc))
        except asyncio.TimeoutError:
            return ExecOutcome(ToolResult(tool=name, status=ToolStatus.ERROR, message="Xử lý quá thời gian, vui lòng thử lại.",
                                          error_code="TOOL_TIMEOUT"))
        finally:
            logger.info("tool=%s elapsed_ms=%d request_id=%s", name,
                        (time.monotonic() - started) * 1000, ctx.request_id)
