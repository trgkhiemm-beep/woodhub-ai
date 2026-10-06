"""Tool registry — mọi tool đều CHỈ ĐỌC và dùng chung cho mọi người gọi.

Agent không phân quyền người dùng: dữ liệu riêng (đơn hàng, task 3D, xưởng gần) do Backend quyết định trả hay từ chối
khi Agent gọi lại với token Backend chuyển tiếp.
"""
from __future__ import annotations

from app.tools.advisor import ADVISOR_TOOLS
from app.tools.base import OperationType, ToolSpec
from app.tools.read_tools import READ_TOOLS

# Tên tool không bao giờ được đăng ký (ghi dữ liệu, SQL tùy ý, lệnh hệ thống, HTTP tùy ý).
FORBIDDEN_TOOL_NAMES = frozenset({"execute_sql", "update_anything", "run_arbitrary_command", "run_command",
                                  "http_request", "confirm_action", "delete_anything", "update_product_price",
                                  "update_product_description", "adjust_inventory", "update_store_info", "upsert_faq",
                                  "create_promotion", "set_promotion_status", "upsert_category", "upsert_material"})
READ_ONLY_OPERATIONS = frozenset({OperationType.READ, OperationType.SEARCH, OperationType.REALTIME})


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec]):
        names = [s.name for s in specs]
        if len(names) != len(set(names)):
            raise ValueError("Tên tool bị trùng.")
        bad = FORBIDDEN_TOOL_NAMES & set(names)
        if bad:
            raise ValueError(f"Tool bị cấm: {bad}")
        writes = [s.name for s in specs if s.operation not in READ_ONLY_OPERATIONS]
        if writes:
            raise ValueError(f"Agent chỉ đọc — tool không hợp lệ: {writes}")
        self._specs = {s.name: s for s in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._specs.values())


def build_registry() -> ToolRegistry:
    return ToolRegistry(READ_TOOLS + ADVISOR_TOOLS)
