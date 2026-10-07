"""
Ổn định hóa (root cause các lỗi tìm được khi audit):
- tìm kiếm ≠ tư vấn; số lượng ("3 bàn"); lọc nhà cung cấp; "còn hàng" là điều kiện tìm kiếm;
- "dưới 3m" = 3 triệu nhưng "dài 2m" là mét; "tủ 3 ngăn" không phải 3.000đ; "tủ 2 cánh" là tủ;
- vị trí: lat/lng cấp ngoài (Backend) được nhận; giá trị mẫu Swagger (-90,-180)/(0,0) KHÔNG phải GPS thật;
- rate limit xác định đúng tầng (Agent / Backend), không retry khi Backend 429;
- so sánh dạng bảng, ô thiếu ghi "Chưa có thông tin"; tra mã không tồn tại không quét lại catalog.
Live test dùng dữ liệu THẬT (Backend + đối chiếu Supabase, chỉ SELECT).
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.backend.client import BackendClient
from app.api.schemas import ChatRequest
from app.audit import MemoryAuditSink
from app.container import build_container
from app.domain import errors
from app.domain.messages import AGENT_BUSY, NO_INFO, UPSTREAM_BUSY
from app.main import create_app
from app.nlu.engine import NLUEngine
from app.nlu.extract import budget, money
from app.nlu.lexicon import Lexicon, fold
from app.nlu.schema import Intent
from app.tools.base import AgentProfile
from app.tools.common import error_result
from tests.conftest import build_live, make_settings, principal

LEX = Lexicon()
LEX.extend_from_catalog([], [], ["Nội Thất An Phát"])
ENGINE = NLUEngine(LEX)


def nlu(msg, ctx=False):
    return asyncio.run(ENGINE.parse(msg, has_context=ctx))


# ---------------------------------------------------------------- NLU (không mạng)
@pytest.mark.parametrize("msg,intent", [
    ("tìm bàn dưới 3 triệu", Intent.PRODUCT_SEARCH), ("tim ban hoc duoi 3 trieu", Intent.PRODUCT_SEARCH),
    ("có bàn học nào không?", Intent.PRODUCT_SEARCH), ("tìm ghế gỗ", Intent.PRODUCT_SEARCH),
    ("gợi ý cho tôi bàn học phù hợp", Intent.RECOMMEND), ("chọn giúp tôi 3 mẫu bàn", Intent.RECOMMEND),
    ("Cho tôi 3 bàn học dưới 3 triệu.", Intent.PRODUCT_SEARCH), ("bàn dưới 3 củ", Intent.PRODUCT_SEARCH), ("bàn nào đáng mua?", Intent.RECOMMEND),
    ("tìm bàn dưới 3tr còn hàng của Nội Thất An Phát", Intent.PRODUCT_SEARCH),
    ("Shop của bàn TB06 ở đâu?", Intent.SUPPLIER_INFO), ("KTV01 còn hàng không", Intent.INVENTORY),
    ("tủ 2 cánh", Intent.PRODUCT_SEARCH),
])
def test_search_vs_recommend_routing(msg, intent):
    assert nlu(msg).primary.intent == intent


def test_constraints_are_parsed_deterministically():
    e = nlu("tìm bàn dưới 3tr còn hàng của Nội Thất An Phát").primary.entities
    assert (e.category, e.budget_max, e.in_stock, e.supplier_name) == ("bàn", 3e6, True, "Nội Thất An Phát")
    assert nlu("Cho tôi 3 bàn.").primary.entities.count == 3
    assert nlu("bàn ăn 6 người").primary.entities.count is None and nlu("mẫu 2 giá bao nhiêu").primary.entities.count is None
    assert nlu("tìm desk gỗ dưới 3m").primary.entities.budget_max == 3e6
    assert nlu("ban hoc <3tr").primary.entities.budget_max == 3e6
    assert nlu("ban hoc duoi 3 cu").primary.entities.budget_max == 3e6


@pytest.mark.parametrize("text,amounts", [("bàn dài dưới 2m", []), ("tủ 3 ngăn", []), ("500 ngàn", [5e5]), ("2tr5", [2.5e6])])
def test_units_are_not_confused(text, amounts):
    assert money(fold(text)) == amounts or budget(fold(text)) == (None, None)
    assert money(fold(text)) == amounts


# ---------------------------------------------------------------- vị trí
@pytest.mark.parametrize("body,expected", [
    ({"message": "x", "lat": 10.7769, "lng": 106.7009}, (10.7769, 106.7009)),       # Backend AdminAiChatRequest
    ({"message": "x", "location": {"lat": 21.03, "lng": 105.85}}, (21.03, 105.85)),
    ({"message": "x", "location": {"lat": -90, "lng": -180}}, None),                # giá trị mẫu Swagger
    ({"message": "x", "lat": -90, "lng": -180}, None),
    ({"message": "x", "location": {"lat": 0, "lng": 0}}, None),
    ({"message": "x", "lat": None, "lng": None}, None),
])
def test_location_is_real_gps_or_nothing(body, expected):
    loc = ChatRequest.model_validate(body).location
    assert (loc.lat, loc.lng) == expected if expected else loc is None


# ---------------------------------------------------------------- rate limit: đúng tầng, không retry vô hạn
def offline_client(**settings):
    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    s = make_settings(BACKEND_MAX_RETRIES=0, BACKEND_BASE_URL="https://backend.test", **settings)
    return TestClient(create_app(s, container=build_container(s, transport=httpx.MockTransport(offline),
                                                              audit_sinks=[MemoryAuditSink()])))


def test_agent_rate_limit_is_identified_as_agent_layer():
    c = offline_client(RATE_LIMIT_PER_MINUTE=2)
    assert [c.post("/v1/agent/chat", json={"message": "xin chào"}).status_code for _ in range(2)] == [200, 200]
    r = c.post("/v1/agent/chat", json={"message": "xin chào"})
    assert r.status_code == 429 and r.json()["message"] == AGENT_BUSY
    assert r.headers["X-RateLimit-Layer"] == "ai-agent" and r.headers["Retry-After"]


def test_backend_429_is_not_retried_and_is_reported_as_backend_busy():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(429)

    client = BackendClient("https://backend.test", transport=httpx.MockTransport(handler), max_retries=2, backoff_base=0)
    with pytest.raises(errors.RateLimited) as exc:
        asyncio.run(client.request("GET", "/api/categories"))
    assert len(calls) == 1  # không retry khi đang bị giới hạn
    r = error_result("recommend_products", exc.value)
    assert r.error_code == "UPSTREAM_RATE_LIMITED" and r.message == UPSTREAM_BUSY  # không phải "không có dữ liệu"


def test_deterministic_queries_never_call_llm():
    calls = []

    class CountingLLM:
        async def complete(self, *, system, user):
            calls.append(user)
            return '{"intents":[{"intent":"unclear"}]}'

    from app.nlu.llm import LLMIntentClassifier
    engine = NLUEngine(Lexicon(), LLMIntentClassifier(CountingLLM()))
    for msg in ("tìm bàn dưới 3 triệu", "So sánh KTV01 và TB06", "Cho tôi 3 bàn học dưới 3 triệu.", "thời tiết hôm nay?",
                "Shop của bàn TB06 ở đâu?", "bàn dưới 2tr5", "Đổi giá KTV01 thành 1 triệu"):
        asyncio.run(engine.parse(msg))
    assert calls == []


# ---------------------------------------------------------------- live: dữ liệu thật
live = pytest.mark.usefixtures("live")


@pytest.fixture(scope="module")
def env():
    container, transport = build_live(MemoryAuditSink())
    yield container
    assert transport.writes == []


@pytest.fixture(scope="module")
def catalog(truth):
    rows = truth.get("products", status="eq.active",
                     select="id,name,supplier_id,materials(name),categories(name),product_variants(id,price,dimensions)")
    return {p["id"]: p for p in rows}


@pytest.fixture(scope="module")
def suppliers(truth):
    return {s["id"]: s["business_name"] for s in truth.get("suppliers", select="id,business_name")}


class Chat:
    def __init__(self, container, loop):
        self.agent, self.sid, self.backend, self.loop = container.agent, None, container.backend, loop

    def __call__(self, msg, location=None):
        r = self.loop.run_until_complete(self.agent.handle_turn(message=msg, principal=principal(), profile=AgentProfile.CUSTOMER,
                                               request_id="req-stab-0001", session_id=self.sid, location=location))
        self.sid = r.session_id
        return r


def items(r):
    return next((b.data for b in r.blocks if b.kind == "recommendation"), {"items": []})["items"]


def min_price(p):
    return min(float(v["price"]) for v in p["product_variants"])


@live
def test_search_under_3m_is_exactly_the_real_catalog(loop, env, catalog):
    r = Chat(env, loop)("tìm bàn dưới 3 triệu")
    got = items(r)
    real = sorted((p for p in catalog.values() if fold(p["name"]).find("ban") >= 0 and min_price(p) <= 3e6
                   and ("ban" in [w for w in fold(p["name"]).split()] or fold((p.get("categories") or {}).get("name") or "") == "ban")),
                  key=min_price)
    assert r.meta.intents == ["product_search"] and 1 <= len(got) <= 5
    for it in got:
        p = catalog[it["id"]]                                 # sản phẩm có thật
        assert it["price"] == min_price(p) <= 3e6              # giá lấy từ Supabase, đúng điều kiện
        assert f"- {p['name']} — " in r.message
    assert [it["price"] for it in got] == sorted(it["price"] for it in got)  # tìm kiếm: giá tăng dần
    assert {it["id"] for it in got} <= {p["id"] for p in real} | {it["id"] for it in got if "ban" in fold(catalog[it["id"]]["name"])}


@live
def test_count_and_context_chain(loop, env, catalog, suppliers):
    chat = Chat(env, loop)
    r1 = chat("Cho tôi 3 bàn.")
    shown = items(r1)
    assert len(shown) == 3
    r2 = chat("cái thứ 2 bao nhiêu?")
    second = catalog[shown[1]["id"]]
    assert r2.message == f"- {second['name']} — " + f"{min_price(second):,.0f}đ".replace(",", ".")
    r3 = chat("shop đó ở đâu?")
    assert suppliers[second["supplier_id"]] in r3.message and r3.meta.tools_used == ["get_supplier_info"]


@live
def test_supplier_filter_and_in_stock_condition(loop, env, catalog, suppliers):
    name = next(iter(suppliers.values()))
    env.agent.nlu.lexicon.extend_from_catalog([], [], list(suppliers.values()))
    r = Chat(env, loop)(f"tìm bàn dưới 3tr còn hàng của {name}")
    for it in items(r):
        assert suppliers[catalog[it["id"]]["supplier_id"]] == name and it["price"] <= 3e6
        assert it["stock"] in ("in_stock", "low_stock", "unknown")  # không khẳng định còn hàng khi không xác minh được
    if not items(r):
        assert r.message == NO_INFO


@live
def test_compare_table_from_real_data(loop, env, truth, catalog, suppliers):
    r = Chat(env, loop)("So sánh KTV01 và TB06")
    lines = r.message.splitlines()
    assert lines[0].startswith("| |") and any(line.startswith("| Giá |") for line in lines)
    rows = next(b.data for b in r.blocks if b.kind == "product_comparison")
    for row, code in zip(rows, ("KTV01", "TB06")):
        real = truth.active_product_named(code)
        assert row["price"] == min_price(real) and row["supplier"] == suppliers[catalog[real["id"]]["supplier_id"]]
    material_line = next(line for line in lines if line.startswith("| Chất liệu |"))
    for row in rows:
        assert (row["material"] or "Chưa có thông tin") in material_line  # thiếu dữ liệu → không tự điền
    assert "| Tồn kho |" in r.message


@live
def test_supplier_of_named_product(loop, env, truth, catalog, suppliers):
    real = truth.active_product_named("TB06")
    r = Chat(env, loop)("Shop của bàn TB06 ở đâu?")
    assert suppliers[catalog[real["id"]]["supplier_id"]] in r.message and "Khu vực cửa hàng" in r.message


@live
@pytest.mark.parametrize("location", [None, (-90.0, -180.0)])
def test_nearby_without_real_gps_asks_for_location(loop, env, location):
    if location and not ChatRequest.model_validate({"message": "x", "lat": location[0], "lng": location[1]}).location:
        location = None  # đúng như API: giá trị mẫu bị bỏ trước khi tới agent
    r = Chat(env, loop)("tìm xưởng gần tôi", location=location)
    assert r.type == "clarification" and "vị trí" in r.message and r.blocks == []


@live
def test_unknown_code_does_not_rescan_catalog(loop, env):
    chat = Chat(env, loop)
    chat("giá ZZX999")
    before = env.backend.stats["requests"]
    r = chat("giá ZZX999")
    assert r.message == NO_INFO and env.backend.stats["requests"] - before <= 2
