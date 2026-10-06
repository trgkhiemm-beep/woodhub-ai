"""
Ngữ cảnh người gọi (do Backend chuyển tiếp).

Agent KHÔNG xác thực và KHÔNG phân quyền người dùng — đó là việc của WoodHub Backend.
`access_token` (nếu Backend gửi kèm `Authorization: Bearer …`) chỉ được mang theo NGUYÊN TRẠNG để gọi lại Backend
cho các API dữ liệu riêng (đơn hàng, task 3D, xưởng gần): không giải mã, không kiểm tra chữ ký, không đọc role.
Backend tự quyết định trả dữ liệu hay từ chối.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    # Không log, không trả ra ngoài, không giải mã.
    access_token: str | None = field(default=None, repr=False, compare=False)

    @property
    def audit_id(self) -> str:
        return "backend-forwarded" if self.access_token else "anonymous"

    @classmethod
    def guest(cls) -> "Principal":
        return cls()
