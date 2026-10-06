"""
Entry point WoodHub AI Agent.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

`app` được tạo lazily (PEP 562) để test có thể gọi create_app(settings=...) mà không cần biến môi trường.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import agent as agent_api
from app.api import legacy as legacy_api
from app.api.deps import RateLimiter
from app.api.schemas import BLOCK_DATA_SCHEMAS, CONTRACT_VERSION
from app.config import Settings, get_settings
from app.container import Container, build_container

logger = logging.getLogger("woodhub")


def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    rid = request.headers.get("X-Request-Id")
    return JSONResponse(status_code=status, content={"status": "error", "code": code, "message": message, "request_id": rid})


async def _keep_backend_awake(container: Container, every: int) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await container.backend.request("GET", "/api/categories")
        except Exception as exc:  # chỉ là ping; lỗi không được làm sập server
            logger.warning("Backend keep-alive thất bại: %s", type(exc).__name__)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("WoodHub AI Agent start | env=%s nlu=%s llm=%s backend=%s", settings.APP_ENV.value,
                    settings.NLU_MODE.value, settings.llm_enabled, settings.BACKEND_BASE_URL)
        container: Container = app.state.container
        # Đánh thức Backend + nạp từ vựng ngay khi khởi động (chạy nền, không chặn server)
        tasks = [asyncio.create_task(container.agent.warm_up())]
        if settings.BACKEND_KEEPALIVE_SECONDS:
            tasks.append(asyncio.create_task(_keep_backend_awake(container, settings.BACKEND_KEEPALIVE_SECONDS)))
        yield
        for t in tasks:
            t.cancel()
        await container.aclose()

    app = FastAPI(title="WoodHub AI Agent", version=CONTRACT_VERSION, lifespan=lifespan)
    app.state.container = container or build_container(settings)
    app.state.rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)

    if settings.cors_origins_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True,
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Request-Id"])

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        if isinstance(detail, dict):
            return _error(exc.status_code, detail.get("code", "HTTP_ERROR"), detail.get("message", ""), request)
        return _error(exc.status_code, "HTTP_ERROR", str(detail), request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        msg = str(first.get("msg", "Dữ liệu đầu vào không hợp lệ.")).removeprefix("Value error, ")
        return _error(422, "VALIDATION_ERROR", msg, request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error at %s", request.url.path)
        return _error(500, "INTERNAL_ERROR", "Đã có lỗi xảy ra ở server, vui lòng thử lại sau.", request)

    app.include_router(agent_api.router)
    app.include_router(legacy_api.router)

    @app.get("/health", tags=["monitoring"])
    def health() -> dict[str, str]:
        return {"status": "ok", "contract_version": CONTRACT_VERSION}

    _document_block_schemas(app)
    return app


def _document_block_schemas(app: FastAPI) -> None:
    """Thêm schema của `Block.data` theo kind (supplier_info, order_status) vào OpenAPI components."""
    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, openapi_version=app.openapi_version,
                             description=app.description, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in BLOCK_DATA_SCHEMAS:
            js = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, sub in js.pop("$defs", {}).items():
                components.setdefault(name, sub)
            components[model.__name__] = js
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


def __getattr__(name: str) -> Any:
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
