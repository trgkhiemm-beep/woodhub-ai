"""
Audit log cho mọi mutation và sự kiện bảo mật.

Sink hiện tại: file JSONL (append-only) + logger "woodhub.audit". Backend chưa có Audit API
(GAP B.9) — khi có, thêm sink gửi về Backend mà không đổi call site.
Không bao giờ ghi password / token / API key / secret.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("woodhub.audit")

_SENSITIVE_KEYS = ("password", "token", "secret", "api_key", "apikey", "authorization", "access_key", "credential")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(s in str(k).lower() for s in _SENSITIVE_KEYS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


class AuditSink(Protocol):
    def write(self, record: dict[str, Any]) -> None: ...


class JsonlAuditSink:
    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + os.linesep)


class MemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class AuditLogger:
    def __init__(self, sinks: list[AuditSink]):
        self._sinks = sinks

    def log(self, event: str, *, request_id: str, user_id: str, role: str, status: str, action: str | None = None,
            action_id: str | None = None, tool: str | None = None, target: dict[str, Any] | None = None,
            before: Any = None, after: Any = None, confirmation: str | None = None, error: str | None = None,
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
        record = redact({
            "timestamp": datetime.now(timezone.utc).isoformat(), "event": event, "request_id": request_id,
            "user_id": user_id, "role": role, "action": action, "action_id": action_id, "tool": tool,
            "target": target, "before": before, "after": after, "status": status, "confirmation": confirmation,
            "error": error, **(extra or {}),
        })
        logger.info("audit %s %s", event, json.dumps({k: record[k] for k in ("action_id", "tool", "status", "user_id")}, default=str))
        for sink in self._sinks:
            try:
                sink.write(record)
            except Exception:  # audit không được làm hỏng request, nhưng phải báo lỗi to
                logger.exception("Ghi audit thất bại (sink=%s)", type(sink).__name__)
        return record
