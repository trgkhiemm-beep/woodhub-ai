"""
Entry point WoodHub AI Agent.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

`app` được tạo lazily (PEP 562) để test có thể gọi create_app(settings=...) mà không cần biến môi trường.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import agent as agent_api
from app.api import legacy as legacy_api
from app.api.deps import RateLimiter
from app.api.schemas import CONTRACT_VERSION
from app.config import Settings, get_settings
from app.container import Container, build_container

logger = logging.getLogger("woodhub")


def _error(status: int, code: str, message: str, request: Request) -> JSONResponse:
    rid = request.headers.get("X-Request-Id")
    return JSONResponse(status_code=status, content={"status": "error", "code": code, "message": message, "request_id": rid})


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("WoodHub AI Agent start | env=%s nlu=%s llm=%s backend=%s", settings.APP_ENV.value,
                    settings.NLU_MODE.value, settings.llm_enabled, settings.BACKEND_BASE_URL)
        yield
        await app.state.container.aclose()

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

    return app


def __getattr__(name: str) -> Any:
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
