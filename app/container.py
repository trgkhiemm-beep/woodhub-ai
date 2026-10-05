"""Composition root: nối adapter → ports → tools → agent. Nơi DUY NHẤT biết implementation cụ thể."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.adapters.backend.adapters import (
    BackendCatalogAdapter, BackendDesignAdapter, BackendIdentityAdapter, BackendInventoryAdapter,
    BackendKnowledgeAdapter, BackendPromotionAdapter, BackendStoreAdapter,
)
from app.adapters.backend.client import BackendClient
from app.adapters.backend.jwt_identity import JwtIdentityAdapter
from app.agent.actions import ActionService, InMemoryActionRepository
from app.agent.executor import ToolExecutor
from app.agent.orchestrator import AgentService
from app.agent.session import SessionStore
from app.audit import AuditLogger, AuditSink, JsonlAuditSink
from app.config import Settings
from app.nlu.engine import NLUEngine
from app.nlu.lexicon import Lexicon
from app.nlu.llm import BedrockConverseClient, LLMClient, LLMIntentClassifier
from app.ports import IdentityPort, Ports
from app.tools.registry import ToolRegistry, build_registry

logger = logging.getLogger("woodhub.auth")


@dataclass
class Container:
    settings: Settings
    backend: BackendClient
    ports: Ports
    registry: ToolRegistry
    audit: AuditLogger
    actions: ActionService
    agent: AgentService

    async def aclose(self) -> None:
        await self.backend.aclose()


def _identity(settings: Settings, backend: BackendClient) -> IdentityPort:
    """Có BACKEND_JWT_SECRET → verify JWT HS256 tại chỗ; không có → dự phòng hỏi Backend GET /api/users/me."""
    if settings.BACKEND_JWT_SECRET is not None and settings.BACKEND_JWT_SECRET.get_secret_value():
        return JwtIdentityAdapter(settings.BACKEND_JWT_SECRET.get_secret_value(),
                                  leeway_seconds=settings.BACKEND_JWT_LEEWAY_SECONDS)
    logger.warning("Chưa cấu hình BACKEND_JWT_SECRET → xác thực token qua Backend GET /api/users/me")
    return BackendIdentityAdapter(backend, settings.IDENTITY_CACHE_SECONDS)


def build_container(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                    audit_sinks: list[AuditSink] | None = None, llm_client: LLMClient | None = None,
                    ports: Ports | None = None) -> Container:
    assert settings.BACKEND_BASE_URL
    backend = BackendClient(settings.BACKEND_BASE_URL, timeout=settings.BACKEND_TIMEOUT_SECONDS,
                            max_retries=settings.BACKEND_MAX_RETRIES, transport=transport)
    ports = ports or Ports(
        identity=_identity(settings, backend),
        catalog=BackendCatalogAdapter(backend, settings.SKU_SCAN_MAX_PRODUCTS),
        inventory=BackendInventoryAdapter(backend), store=BackendStoreAdapter(backend),
        promotions=BackendPromotionAdapter(backend), knowledge=BackendKnowledgeAdapter(backend),
        design=BackendDesignAdapter(backend),
    )
    registry = build_registry()
    audit = AuditLogger(audit_sinks if audit_sinks is not None else [JsonlAuditSink(settings.AUDIT_LOG_PATH)])
    actions = ActionService(InMemoryActionRepository(), registry, audit, settings)
    executor = ToolExecutor(registry, actions, audit)
    classifier = None
    if llm_client is not None or settings.llm_enabled:
        client = llm_client or BedrockConverseClient(
            model_id=settings.BEDROCK_MODEL_ID or "", region=settings.AWS_DEFAULT_REGION,
            access_key=settings.AWS_ACCESS_KEY_ID.get_secret_value() if settings.AWS_ACCESS_KEY_ID else None,
            secret_key=settings.AWS_SECRET_ACCESS_KEY.get_secret_value() if settings.AWS_SECRET_ACCESS_KEY else None,
            timeout=settings.LLM_TIMEOUT_SECONDS, max_tokens=settings.LLM_MAX_TOKENS,
        )
        classifier = LLMIntentClassifier(client)
    agent = AgentService(settings=settings, ports=ports, registry=registry, executor=executor, actions=actions,
                         sessions=SessionStore(settings.SESSION_TTL_SECONDS, settings.MAX_SESSIONS), audit=audit,
                         nlu=NLUEngine(Lexicon(), classifier))
    return Container(settings=settings, backend=backend, ports=ports, registry=registry, audit=audit, actions=actions, agent=agent)
