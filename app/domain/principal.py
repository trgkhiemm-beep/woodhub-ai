"""Danh tính người gọi — chỉ được tạo từ trusted authentication context, không bao giờ từ nội dung tin nhắn."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    GUEST = "guest"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    ADMIN = "admin"

    @classmethod
    def from_backend(cls, value: str | None) -> "Role":
        """Map role của Backend (customer|supplier|admin). Giá trị lạ → CUSTOMER (quyền thấp nhất đã đăng nhập)."""
        try:
            role = cls((value or "").strip().lower())
        except ValueError:
            return cls.CUSTOMER
        return cls.CUSTOMER if role == cls.GUEST else role


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    role: Role
    email: str | None = None
    display_name: str | None = None
    # Access token để gọi Backend thay mặt người dùng. Không log, không trả ra ngoài.
    access_token: str | None = field(default=None, repr=False, compare=False)

    @property
    def is_authenticated(self) -> bool:
        return self.role != Role.GUEST and self.user_id is not None

    @property
    def audit_id(self) -> str:
        return self.user_id or "anonymous"

    @classmethod
    def guest(cls) -> "Principal":
        return cls(user_id=None, role=Role.GUEST)
