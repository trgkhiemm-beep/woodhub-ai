"""
Cấu hình trung tâm của WoodHub AI Agent.

Mọi biến môi trường được khai báo, kiểm tra kiểu và ràng buộc tại đây. Ứng dụng
fail-fast khi cấu hình không hợp lệ (vd production nhưng dùng dữ liệu mock).
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(str, Enum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class NLUMode(str, Enum):
    AUTO = "auto"    # dùng LLM nếu có BEDROCK_MODEL_ID + AWS key, ngược lại rules
    LLM = "llm"      # LLM phân loại ý (Bedrock Converse) + trích xuất deterministic; lỗi → rules
    RULES = "rules"  # chỉ bộ phân loại dự phòng


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: AppEnv = AppEnv.LOCAL
    PROJECT_NAME: str = "WoodHub AI Agent"
    LOG_LEVEL: str = "INFO"

    # --- Data / integration: WoodHub Backend là nguồn dữ liệu duy nhất (không có chế độ mock) ---
    BACKEND_BASE_URL: str | None = None
    BACKEND_TIMEOUT_SECONDS: float = Field(default=8.0, gt=0, le=60)
    BACKEND_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    SKU_SCAN_MAX_PRODUCTS: int = Field(default=40, ge=1, le=200)
    # Backend trên Render free ngủ sau ~15 phút rảnh (đánh thức mất ~1 phút). >0: ping GET /api/categories
    # định kỳ để giữ Backend thức (vd 600). 0 = tắt. Lưu ý: giữ thức 24/7 tiêu tốn giờ chạy free của Render.
    BACKEND_KEEPALIVE_SECONDS: int = Field(default=0, ge=0, le=3600)

    # --- Bảo vệ server-to-server (Backend → Agent). KHÔNG phải xác thực người dùng (việc của Backend). ---
    # Đặt giá trị bí mật dài, ngẫu nhiên → mọi request /v1/agent/* phải có header X-Agent-Api-Key khớp. Trống = tắt.
    AGENT_SERVICE_API_KEY: SecretStr | None = None

    # --- Agent ---
    NLU_MODE: NLUMode = NLUMode.AUTO
    MAX_MESSAGE_CHARS: int = Field(default=2000, ge=50, le=10000)
    MAX_TOOL_CALLS_PER_TURN: int = Field(default=6, ge=1, le=20)
    SESSION_TTL_SECONDS: int = Field(default=1800, ge=60)
    MAX_SESSIONS: int = Field(default=10000, ge=10)
    # Chống lạm dụng dịch vụ theo IP client; sau Backend mọi request có thể cùng IP → ngưỡng cao (quota người dùng do Backend).
    RATE_LIMIT_PER_MINUTE: int = Field(default=600, ge=1)

    # --- Audit ---
    AUDIT_LOG_PATH: str = "var/audit/audit.jsonl"

    # --- CORS ---
    CORS_ORIGINS: str = ""

    # --- LLM (AWS Bedrock Converse) ---
    AWS_ACCESS_KEY_ID: SecretStr | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_DEFAULT_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str | None = None
    LLM_TIMEOUT_SECONDS: float = Field(default=12.0, gt=0, le=120)
    LLM_MAX_TOKENS: int = Field(default=150, ge=64, le=4096)  # NLU chỉ trả JSON ngắn

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production_like(self) -> bool:
        return self.APP_ENV in (AppEnv.STAGING, AppEnv.PRODUCTION)

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.is_production_like and "*" in self.cors_origins_list:
            raise ValueError("CORS_ORIGINS='*' bị cấm ở staging/production.")
        if not self.BACKEND_BASE_URL:
            raise ValueError("Thiếu BACKEND_BASE_URL (vd https://woodhub-be.onrender.com).")
        if not self.BACKEND_BASE_URL.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("BACKEND_BASE_URL phải dùng https (trừ localhost).")
        if self.NLU_MODE == NLUMode.LLM and not self.BEDROCK_MODEL_ID:
            raise ValueError("NLU_MODE=llm cần BEDROCK_MODEL_ID.")
        return self

    @property
    def llm_enabled(self) -> bool:
        if self.NLU_MODE == NLUMode.RULES:
            return False
        return bool(self.BEDROCK_MODEL_ID and (self.NLU_MODE == NLUMode.LLM or self.AWS_ACCESS_KEY_ID))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
