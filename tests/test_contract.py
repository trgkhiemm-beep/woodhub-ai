"""
Contract agent-api: file contracts/agent-api.openapi.json khớp code; alias camelCase của Backend (Spring) được chấp nhận;
block supplier_info/order_status có schema. Không cần mạng (Backend thay bằng transport luôn lỗi kết nối — mô phỏng hạ tầng).
"""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.audit import MemoryAuditSink
from app.container import build_container
from app.domain.messages import ACTIONS_DISABLED
from app.main import create_app
from tests.conftest import make_settings

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "agent-api.openapi.json"


@pytest.fixture(scope="module")
def client():
    def offline(request):
        raise httpx.ConnectError("backend offline in contract tests", request=request)

    settings = make_settings(BACKEND_MAX_RETRIES=0, BACKEND_BASE_URL="https://backend.test")
    container = build_container(settings, transport=httpx.MockTransport(offline), audit_sinks=[MemoryAuditSink()])
    return TestClient(create_app(settings, container=container))


def test_contract_file_matches_code(client):
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == client.app.openapi(), \
        "Sinh lại contracts/agent-api.openapi.json (lệnh trong README)"


def test_contract_documents_aliases_and_block_schemas(client):
    s = client.app.openapi()["components"]["schemas"]
    assert s["ChatRequest"]["properties"]["session_id"]["x-aliases"] == ["sessionId"]
    assert s["ChatRequest"]["properties"]["client_message_id"]["x-aliases"] == ["clientMessageId"]
    assert s["ConfirmRequest"]["properties"]["confirmation_code"]["x-aliases"] == ["confirmationCode"]
    assert s["ConfirmRequest"]["properties"]["session_id"]["x-aliases"] == ["sessionId"]
    assert {"supplier_info", "order_status"} <= set(s["Block"]["properties"]["kind"]["enum"])
    assert {"phone", "email", "stores", "topic"} <= set(s["SupplierInfoBlockData"]["properties"])
    assert {"status", "order_number", "history"} <= set(s["OrderStatusBlockData"]["properties"])
    # response vẫn snake_case (không đổi với Backend đang dùng)
    assert "session_id" in s["AgentResponse"]["properties"] and "sessionId" not in s["AgentResponse"]["properties"]


@pytest.mark.parametrize("path", ["/v1/agent/chat", "/v1/agent/manage/chat"])
def test_camelcase_session_id_keeps_conversation(client, path):
    first = client.post(path, json={"message": "xin chào"}).json()
    sid = first["session_id"]
    second = client.post(path, json={"message": "xin chào", "sessionId": sid, "clientMessageId": "m-2"}).json()
    assert second["session_id"] == sid  # alias camelCase được nhận → cùng phiên


def test_confirm_and_cancel_accept_camelcase(client):
    r = client.post("/v1/agent/actions/act-1/confirm", json={"confirmationCode": "ABC123", "sessionId": "s-1"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "ACTION_NOT_FOUND" and r.json()["message"] == ACTIONS_DISABLED
    r = client.post("/v1/agent/actions/act-1/confirm", json={"confirmation_code": "ABC123"})
    assert r.status_code == 200 and r.json()["error"]["code"] == "ACTION_NOT_FOUND"
    r = client.post("/v1/agent/actions/act-1/cancel")
    assert r.status_code == 200 and r.json()["type"] == "error" and r.json()["action"] is None
