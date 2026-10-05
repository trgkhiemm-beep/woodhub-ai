"""
Xác thực JWT do WoodHub Backend phát hành (Spring `JwtTokenProvider`), verify TẠI CHỖ — không gọi mạng.

- HS256, khóa = bytes UTF-8 của BACKEND_JWT_SECRET (Backend: `secret.getBytes(UTF_8)`).
- Bắt buộc: chữ ký hợp lệ, `exp` còn hạn, có `sub`. Không bao giờ decode mà bỏ qua chữ ký.
- Danh tính = `sub` (email), role = claim `role` (admin|supplier|customer). Role lạ/thiếu → customer (quyền thấp nhất).
- Không log token/secret.
"""
from __future__ import annotations

import jwt

from app.domain import errors
from app.domain.principal import Principal, Role


class JwtIdentityAdapter:
    source_system = "backend-jwt"

    def __init__(self, secret: str, *, algorithms: tuple[str, ...] = ("HS256",), leeway_seconds: int = 0):
        if not secret:
            raise ValueError("BACKEND_JWT_SECRET rỗng")
        self._key = secret.encode("utf-8")
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds

    async def resolve(self, access_token: str) -> Principal:
        try:
            claims = jwt.decode(access_token, self._key, algorithms=self._algorithms, leeway=self._leeway,
                                options={"require": ["sub", "exp"], "verify_signature": True, "verify_exp": True})
        except jwt.ExpiredSignatureError as exc:
            raise errors.Unauthenticated("Phiên đăng nhập đã hết hạn.", detail="jwt expired") from exc
        except jwt.InvalidTokenError as exc:  # chữ ký sai, sai thuật toán, thiếu claim, token hỏng
            raise errors.Unauthenticated("Token không hợp lệ.", detail=f"jwt invalid: {type(exc).__name__}") from exc
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise errors.Unauthenticated("Token không hợp lệ.", detail="jwt invalid: empty sub")
        raw_role = claims.get("role")
        role = Role.from_backend(raw_role.removeprefix("ROLE_").removeprefix("role_") if isinstance(raw_role, str) else None)
        return Principal(user_id=sub.strip(), role=role, email=sub.strip(), access_token=access_token)
