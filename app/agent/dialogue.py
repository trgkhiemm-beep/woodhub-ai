"""
Trạng thái hội thoại (memory) — chỉ lưu những gì cần để hiểu tham chiếu, KHÔNG lưu dữ liệu realtime
để trả lời lại (giá/tồn kho luôn đọc lại từ Backend) và không lưu token/secret.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.nlu.llm import sanitize_context_line
from app.tools.base import ConversationContext

RECOMMEND_KEYS = ("category", "material", "color", "style", "room", "use_case", "budget_min", "budget_max", "seats", "size",
                  "price_pref")


@dataclass
class ShownProduct:
    id: str
    name: str
    price: float | None = None
    area_cm2: float | None = None
    category: str | None = None


@dataclass
class DialogueState(ConversationContext):
    shown: list[ShownProduct] = field(default_factory=list)          # danh sách vừa hiển thị (cho "mẫu 2")
    active: ShownProduct | None = None                                # sản phẩm đang được nói tới ("cái này")
    constraints: dict[str, Any] = field(default_factory=dict)         # nhu cầu tích lũy cho tư vấn
    pending: str | None = None                                        # câu hỏi làm rõ đang chờ trả lời
    asked: set[str] = field(default_factory=set)                      # đã hỏi làm rõ (không hỏi lặp)
    last_assistant: str | None = None
    turns: deque = field(default_factory=lambda: deque(maxlen=6))

    # ---------------------------------------------------------------- tham chiếu
    def by_ordinal(self, n: int) -> ShownProduct | None:
        return self.shown[n - 1] if 1 <= n <= len(self.shown) else None

    def current(self) -> ShownProduct | None:
        if self.active:
            return self.active
        return self.shown[0] if len(self.shown) == 1 else None

    def set_active(self, p: ShownProduct) -> None:
        self.active = p
        self.last_product_id = p.id

    def set_shown(self, items: list[ShownProduct]) -> None:
        self.shown = items[:10]
        if len(items) == 1:
            self.set_active(items[0])

    # ---------------------------------------------------------------- ràng buộc tư vấn
    def merge_constraints(self, new: dict[str, Any]) -> dict[str, Any]:
        new = {k: v for k, v in new.items() if k in RECOMMEND_KEYS and v not in (None, "", [])}
        base = dict(self.constraints)
        if new.get("category") and base.get("category") and new["category"].split()[0] != base["category"].split()[0]:
            base = {}  # đổi sang loại nội thất khác → bỏ ràng buộc cũ
        base.update(new)
        return base

    # ---------------------------------------------------------------- ngữ cảnh gửi LLM (đã làm sạch)
    def llm_context(self) -> str | None:
        lines = []
        if self.last_assistant and not self.last_assistant.startswith("- "):
            # chỉ câu hỏi/làm rõ của trợ lý; danh sách sản phẩm đã có ở dòng dưới (tiết kiệm token)
            lines.append(f"Trợ lý vừa hỏi: {sanitize_context_line(self.last_assistant, 120)}")
        if self.shown:
            lines.append("Danh sách vừa hiển thị: " + "; ".join(f"{i}) {sanitize_context_line(p.name, 60)}"
                                                              for i, p in enumerate(self.shown[:5], 1)))
        if self.active:
            lines.append(f"Sản phẩm đang nói tới: {sanitize_context_line(self.active.name, 80)}")
        if self.pending:
            lines.append(f"Đang chờ người dùng trả lời: {self.pending}")
        return "\n".join(lines) or None

    @property
    def has_context(self) -> bool:
        return bool(self.active or self.shown or self.pending)
