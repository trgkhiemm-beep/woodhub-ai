"""Lỗi chuẩn hóa mà mọi adapter (Backend thật hoặc mock) phải ném ra. Agent core chỉ biết các lỗi này."""
from __future__ import annotations


class PortError(Exception):
    code = "PORT_ERROR"
    retryable = False

    def __init__(self, message: str = "", *, detail: str | None = None):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail  # chi tiết kỹ thuật: chỉ log, không trả cho người dùng


class NotFound(PortError):
    code = "NOT_FOUND"


class CapabilityUnavailable(PortError):
    """Nguồn dữ liệu chưa cung cấp năng lực này (vd Backend chưa có API). Không được bịa dữ liệu thay thế."""
    code = "CAPABILITY_UNAVAILABLE"


class Unauthenticated(PortError):
    code = "UNAUTHENTICATED"


class Forbidden(PortError):
    code = "FORBIDDEN"


class ValidationFailed(PortError):
    code = "VALIDATION_ERROR"


class Conflict(PortError):
    code = "CONFLICT"


class RateLimited(PortError):
    code = "RATE_LIMITED"
    retryable = True


class UpstreamUnavailable(PortError):
    code = "UPSTREAM_UNAVAILABLE"
    retryable = True


class UpstreamTimeout(UpstreamUnavailable):
    code = "UPSTREAM_TIMEOUT"


class MalformedResponse(PortError):
    code = "MALFORMED_RESPONSE"
