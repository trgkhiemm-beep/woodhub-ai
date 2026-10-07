"""
Contract vị trí Backend → Agent.

Backend gửi `SendAiMessageRequest{content, lat, lng}` (khách, /api/ai-chat/...) và `AdminAiChatRequest{message, sessionId, lat, lng}`.
Agent phải nhận cả hai, chuẩn hóa content → message, lat/lng → location, và chuyển GPS thật tới find_nearby_workshops.
Vị trí là tùy chọn: chỉ tìm xưởng gần cần vị trí; vị trí hỏng/giá trị mẫu bị bỏ qua, không làm lỗi cả lượt.

Chạy qua HTTP thật của Agent (TestClient) với Backend THẬT; transport chỉ ghi lại URL đã gọi (không dữ liệu giả).
"""
import os
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.schemas import ChatRequest
from app.audit import MemoryAuditSink
from app.container import build_container
from app.main import create_app
from tests.conftest import ReadOnlyTransport, make_settings

LAT, LNG = 10.77, 106.69
WORKSHOPS = "/api/stores/nearby/workshops"
ASKS_LOCATION = "vị trí"


# ---------------------------------------------------------------- schema (không cần mạng)
@pytest.mark.parametrize("body,message,location", [
    ({"content": "tôi muốn mua ghế", "lat": LAT, "lng": LNG}, "tôi muốn mua ghế", (LAT, LNG)),   # Backend SendAiMessageRequest
    ({"message": "x", "sessionId": "s1", "lat": LAT, "lng": LNG}, "x", (LAT, LNG)),             # AdminAiChatRequest
    ({"message": None, "content": "x", "lat": LAT, "lng": LNG}, "x", (LAT, LNG)),               # Spring serialize null
    ({"query": "x", "lat": str(LAT), "lng": str(LNG)}, "x", (LAT, LNG)),                         # số dạng chuỗi
    ({"content": "x", "latitude": LAT, "longitude": LNG}, "x", (LAT, LNG)),
    ({"message": "x", "location": {"lat": LAT, "lng": LNG}}, "x", (LAT, LNG)),                    # dạng cũ vẫn chạy
    ({"content": "x"}, "x", None),
    ({"content": "x", "lat": LAT}, "x", None),                                                   # thiếu lng
    ({"content": "x", "lat": -90, "lng": -180}, "x", None),                                      # giá trị mẫu Swagger
    ({"content": "x", "lat": 0, "lng": 0}, "x", None),
    ({"content": "x", "lat": 91, "lng": LNG}, "x", None),                                        # ngoài [-90, 90]
    ({"content": "x", "lat": LAT, "lng": 181}, "x", None),                                       # ngoài [-180, 180]
    ({"content": "x", "lat": "abc", "lng": LNG}, "x", None),
    ({"content": "x", "lat": True, "lng": LNG}, "x", None),
    ({"content": "x", "location": {"lat": 200, "lng": 500}}, "x", None),
])
def test_request_normalization(body, message, location):
    req = ChatRequest.model_validate(body)
    assert req.message == message
    got = (req.location.lat, req.location.lng) if req.location else None
    assert got == location and (req.lat, req.lng) == (location or (None, None))


def test_message_or_content_is_still_required():
    with pytest.raises(ValueError):
        ChatRequest.model_validate({"lat": LAT, "lng": LNG})


def test_openapi_exposes_backend_fields():
    s = TestClient(create_app(make_settings())).get("/openapi.json").json()["components"]["schemas"]["ChatRequest"]
    assert {"message", "lat", "lng", "location"} <= set(s["properties"])
    assert "content" in s["properties"]["message"]["x-aliases"] and s["required"] == ["message"]


# ---------------------------------------------------------------- HTTP Agent → Backend thật
class Recording(ReadOnlyTransport):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[httpx.URL] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        return await super().handle_async_request(request)

    def workshop_calls(self) -> list[dict[str, list[str]]]:
        return [parse_qs(u.query.decode()) for u in self.urls if u.path == WORKSHOPS]


live = pytest.mark.usefixtures("live")


@pytest.fixture
def api():
    rec = Recording()
    s = make_settings()
    app = create_app(s, container=build_container(s, transport=rec, audit_sinks=[MemoryAuditSink()]))
    yield TestClient(app), rec
    assert rec.writes == []


def post(client, body, path="/v1/agent/chat", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(path, json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@live
@pytest.mark.parametrize("path", ["/v1/agent/chat", "/v1/agent/manage/chat"])
def test_A_backend_gps_reaches_find_nearby_workshops(api, path):
    client, rec = api
    r = post(client, {"content": "tìm xưởng gần tôi", "lat": LAT, "lng": LNG}, path)
    assert r["meta"]["tools_used"] == ["find_nearby_workshops"]
    assert r["type"] != "clarification" and ASKS_LOCATION not in r["message"]   # không bảo bật vị trí khi đã có GPS
    calls = rec.workshop_calls()
    assert len(calls) == 1 and calls[0]["lat"] == [str(LAT)] and calls[0]["lng"] == [str(LNG)]  # đúng GPS Backend gửi


@live
def test_A_legacy_chat_route_also_forwards_gps(api):
    client, rec = api
    r = client.post("/chat", json={"content": "tìm xưởng gần tôi", "sessionId": "loc-legacy-1", "lat": LAT, "lng": LNG})
    assert r.status_code == 200 and ASKS_LOCATION not in r.text
    calls = rec.workshop_calls()
    assert len(calls) == 1 and calls[0]["lat"] == [str(LAT)] and calls[0]["lng"] == [str(LNG)]


@live
@pytest.mark.skipif(not os.environ.get("LIVE_CUSTOMER_TOKEN"), reason="cần token khách thật (Backend yêu cầu đăng nhập)")
def test_A_nearby_with_customer_token_returns_workshops(api):
    client, _ = api
    r = post(client, {"content": "tìm xưởng gần tôi", "lat": LAT, "lng": LNG}, token=os.environ["LIVE_CUSTOMER_TOKEN"])
    assert r["type"] == "answer" and r["meta"]["tools_used"] == ["find_nearby_workshops"]


@live
@pytest.mark.parametrize("body", [
    {"content": "tìm xưởng gần tôi"},
    {"content": "tìm xưởng gần tôi", "lat": -90, "lng": -180},   # E: giá trị mẫu → bỏ qua
    {"content": "tìm xưởng gần tôi", "lat": 123, "lng": LNG},    # E: ngoài phạm vi → bỏ qua, không 422
    {"content": "tìm xưởng gần tôi", "lat": "abc", "lng": "x"},  # E: không phải số → bỏ qua, không 422
])
def test_B_E_nearby_without_valid_gps_asks_for_location(api, body):
    client, rec = api
    r = post(client, body)
    assert r["type"] == "clarification" and ASKS_LOCATION in r["message"]
    assert rec.workshop_calls() == []   # không gọi Backend với vị trí bịa


@live
@pytest.mark.parametrize("body", [
    {"content": "tìm bàn dưới 3 triệu"},                                      # C
    {"content": "tìm bàn dưới 3 triệu", "lat": LAT, "lng": LNG},              # D
    {"content": "tìm bàn dưới 3 triệu", "lat": 999, "lng": -999},             # E
])
def test_C_D_E_product_search_never_needs_location(api, body):
    client, rec = api
    r = post(client, body)
    assert r["type"] == "answer" and r["meta"]["tools_used"] == ["search_products"]
    assert ASKS_LOCATION not in r["message"] and rec.workshop_calls() == []
    assert next(b for b in r["blocks"] if b["kind"] == "recommendation")["data"]["items"]


@live
@pytest.mark.parametrize("content,tool", [("so sánh TB06 và TB21V", "compare_products"), ("cách đặt hàng trên WoodHub", None)])
def test_compare_and_faq_do_not_require_location(api, content, tool):
    client, rec = api
    r = post(client, {"content": content})
    assert ASKS_LOCATION not in r["message"] and rec.workshop_calls() == []
    if tool:
        assert tool in r["meta"]["tools_used"]
