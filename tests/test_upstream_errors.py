"""
Regression cho sự cố UPSTREAM_RATE_LIMITED (request_id 474cd8db-…): truy đúng tầng, không khuếch đại retry, map lỗi đúng.

- Lỗi hạ tầng được mô phỏng ở tầng transport HTTP (429 / timeout / 500) — KHÔNG có dữ liệu sản phẩm giả.
- Luồng bình thường (tìm kiếm, lọc giá, tư vấn) chạy trên Backend THẬT, đối chiếu Supabase (chỉ SELECT).
"""
import logging
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.backend.client import upstream_layer
from app.audit import MemoryAuditSink
from app.container import build_container
from app.domain.messages import API_ERROR, UPSTREAM_BUSY
from app.main import create_app
from app.nlu.lexicon import fold
from tests.conftest import build_live, make_settings, principal
from app.tools.base import AgentProfile

SPRING_429 = '{"timestamp":"2026-10-07T05:00:00Z","status":429,"error":"Too Many Requests","path":"/api/products"}'
CLOUDFLARE_429 = "<html><body>error code: 1015 — You are being rate limited (Cloudflare)</body></html>"


class Upstream:
    """Transport ghi lại MỌI request Agent → Backend và trả lỗi theo kịch bản cho /api/products (không dữ liệu giả)."""

    def __init__(self, mode: str):
        self.mode, self.calls = mode, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, req.headers.get("x-request-id")))
        if self.mode == "429-spring":
            return httpx.Response(429, text=SPRING_429, headers={"content-type": "application/json", "retry-after": "5"})
        if self.mode == "429-cloudflare":
            return httpx.Response(429, text=CLOUDFLARE_429, headers={"content-type": "text/html", "cf-ray": "abc-SIN"})
        if self.mode == "timeout":
            raise httpx.ReadTimeout("slow upstream", request=req)
        if self.mode == "500":  # Backend → Supabase hỏng thường lộ ra thành 5xx
            return httpx.Response(500, json={"status": 500, "error": "Internal Server Error", "path": req.url.path})
        raise AssertionError(self.mode)


def client_for(mode: str, retries: int = 2):
    up = Upstream(mode)
    s = make_settings(BACKEND_BASE_URL="https://backend.test", BACKEND_MAX_RETRIES=retries)
    c = build_container(s, transport=httpx.MockTransport(up), audit_sinks=[MemoryAuditSink()])
    c.backend._backoff_base = 0  # test nhanh, logic retry không đổi
    return TestClient(create_app(s, container=c)), up


RID = "474cd8db-6842-44e3-8ae0-ff7f4d9fb8d2"


def ask(c, msg):
    return c.post("/v1/agent/chat", json={"message": msg}, headers={"X-Request-Id": RID}).json()


@pytest.mark.parametrize("mode", ["429-spring", "429-cloudflare"])
def test_backend_429_maps_to_upstream_rate_limited_without_retry(mode, caplog):
    c, up = client_for(mode)
    with caplog.at_level(logging.WARNING):
        r = ask(c, "bàn dưới 3 củ")
    assert r["type"] == "error" and r["error"]["code"] == "UPSTREAM_RATE_LIMITED" and r["message"] == UPSTREAM_BUSY
    assert r["meta"]["tools_used"] == ["search_products"] and r["meta"]["planner"] == "rules"  # không LLM
    per_path = Counter(x[1] for x in up.calls)
    assert max(per_path.values()) == 1, per_path                      # KHÔNG retry: mỗi endpoint đúng 1 lần
    assert all(x[0] == "GET" for x in up.calls)                       # chỉ đọc
    assert all(x[2] == RID for x in up.calls)                         # Backend nhận được X-Request-Id để đối chiếu log
    log = caplog.text
    assert f"upstream_429 request_id={RID}" in log and "GET /api/" in log
    assert ("layer=backend-app" if mode == "429-spring" else "layer=cloudflare") in log


def test_backend_timeout_is_api_error_not_rate_limit():
    c, up = client_for("timeout", retries=2)
    r = ask(c, "bàn dưới 3 củ")
    assert r["type"] == "error" and r["error"]["code"] == "UPSTREAM_TIMEOUT" and r["message"] == API_ERROR
    per_path = Counter(x[1] for x in up.calls)
    assert max(per_path.values()) == 3, per_path   # 1 + BACKEND_MAX_RETRIES, có giới hạn (không vòng lặp)


def test_upstream_500_is_api_error_with_bounded_retry():
    c, up = client_for("500", retries=2)
    r = ask(c, "bàn dưới 3 củ")
    assert r["error"]["code"] == "UPSTREAM_UNAVAILABLE" and r["message"] == API_ERROR
    assert max(Counter(x[1] for x in up.calls).values()) == 3


def test_one_user_request_makes_bounded_backend_calls():
    c, up = client_for("429-spring")
    ask(c, "bàn dưới 3 củ")
    # lần đầu: nạp từ vựng (danh mục, chất liệu, nhà cung cấp) + danh mục (cache) + 1 lần tìm → không nhân bản do retry
    assert len(up.calls) <= 5, up.calls


def test_upstream_layer_classification():
    assert upstream_layer(httpx.Response(429, text=SPRING_429, headers={"content-type": "application/json"})) == "backend-app"
    assert upstream_layer(httpx.Response(429, text=CLOUDFLARE_429, headers={"content-type": "text/html"})) == "cloudflare"
    assert upstream_layer(httpx.Response(429, text="Too many requests", headers={"rndr-id": "x"})) == "render-edge-or-app"


# ---------------------------------------------------------------- luồng bình thường trên dữ liệu thật
live = pytest.mark.usefixtures("live")


@pytest.fixture(scope="module")
def agent():
    container, transport = build_live(MemoryAuditSink())
    yield container
    assert transport.writes == []


def turn(loop, container, msg):
    return loop.run_until_complete(container.agent.handle_turn(message=msg, principal=principal(), profile=AgentProfile.CUSTOMER,
                                                               request_id="req-upstream-0001"))


@live
@pytest.mark.parametrize("msg,limit", [("bàn dưới 3 củ", 3e6), ("tìm bàn dưới 3 triệu", 3e6), ("ban duoi 2tr5", 2.5e6)])
def test_normal_product_search_uses_search_tool_and_real_data(loop, agent, truth, msg, limit):
    before = agent.backend.stats["requests"]
    r = turn(loop, agent, msg)
    real = {p["id"]: p for p in truth.get("products", status="eq.active", select="id,name,product_variants(price)")}
    items = next(b.data for b in r.blocks if b.kind == "recommendation")["items"]
    assert r.type == "answer" and r.meta.tools_used == ["search_products"] and r.meta.intents == ["product_search"]
    assert r.meta.planner == "rules" and 1 <= len(items) <= 5
    for it in items:
        p = real[it["id"]]
        assert it["price"] == min(float(v["price"]) for v in p["product_variants"]) <= limit and "ban" in fold(p["name"])
    assert [i["price"] for i in items] == sorted(i["price"] for i in items)
    assert agent.backend.stats["requests"] - before <= 4  # 1 truy vấn danh sách (+ nạp từ vựng/danh mục lần đầu)


@live
def test_recommendation_still_uses_recommend_tool(loop, agent):
    r = turn(loop, agent, "gợi ý bàn học dưới 3 triệu")
    assert r.meta.tools_used == ["recommend_products"] and r.meta.intents == ["recommend"] and r.type == "answer"
