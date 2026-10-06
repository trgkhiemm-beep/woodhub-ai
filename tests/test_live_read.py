"""
LIVE: READ / SEARCH / REALTIME trên DỮ LIỆU THẬT (Backend → Supabase), đối chiếu Supabase trực tiếp.
Mục tiêu: agent chỉ nói điều có trong source of truth, và nói "chưa có thông tin" khi source không có.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.agent.composer import vnd
from app.domain.messages import BACKEND_DENIED, NO_INFO
from app.main import create_app
from app.tools.base import AgentProfile
from tests.conftest import build_live, make_settings, principal

pytestmark = pytest.mark.usefixtures("live")


@pytest.fixture
def agent(audit_sink):
    container, _ = build_live(audit_sink)
    return container.agent


def ask(loop, agent, msg, profile=AgentProfile.CUSTOMER, **kw):
    return loop.run_until_complete(agent.handle_turn(message=msg, principal=principal(), profile=profile,
                                                     request_id="req-test-0001", **kw))


def no_unverified_numbers(text: str) -> bool:
    return not re.search(r"\d{1,2}[:h]\d{2}|\b1[89]00\b|\b0\d{9}\b", text)


# ---------------------------------------------------------------- product / price (realtime)
def test_price_by_model_code_matches_supabase(loop, agent, truth):
    real = truth.active_product_named("KTV01")
    price = float(real["product_variants"][0]["price"])
    r = ask(loop, agent, "Giá Kệ Tivi KTV01 bao nhiêu?")
    assert r.type == "answer"
    assert real["name"] in r.message and vnd(price) in r.message
    assert r.sources and r.sources[0].system == "backend" and r.sources[0].freshness == "realtime"
    assert r.sources[0].record_id == real["id"]


def test_product_detail_block_matches_supabase(loop, agent, truth):
    real = truth.active_product_named("TB06")
    r = ask(loop, agent, "Cho xem chi tiết TB06")
    detail = next(b.data for b in r.blocks if b.kind == "product_detail")
    assert detail["id"] == real["id"]
    assert {float(v["price"]) for v in real["product_variants"]} == {v["price"] for v in detail["variants"]}
    assert detail["description"] == real["description"]


def test_follow_up_question_uses_conversation_product(loop, agent, truth):
    real = truth.active_product_named("KTV01")
    first = ask(loop, agent, "Giá KTV01")
    r = ask(loop, agent, "cái này giá bao nhiêu", session_id=first.session_id)
    assert vnd(float(real["product_variants"][0]["price"])) in r.message


def test_search_with_price_filter_only_returns_real_matching_products(loop, agent, truth):
    r = ask(loop, agent, "Tìm bàn dưới 3 triệu")
    items = next(b.data for b in r.blocks if b.kind == "recommendation")["items"]
    assert items
    active = {p["id"]: p for p in truth.get("products", status="eq.active", select="id,name,product_variants(price)")}
    for it in items:
        assert it["id"] in active, it
        assert min(float(v["price"]) for v in active[it["id"]]["product_variants"]) <= 3_000_000
        assert it["price"] <= 3_000_000 and vnd(it["price"]) in r.message


def test_search_unaccented_input_finds_real_products(loop, agent, truth):
    r = ask(loop, agent, "co giuong go soi khong")
    items = next(b.data for b in r.blocks if b.kind == "recommendation")["items"]
    assert any(i["name"] == "Giường Ngủ Gỗ Sồi Tự Nhiên" for i in items)


def test_compare_two_real_products(loop, agent, truth):
    a, b = truth.active_product_named("KTV01"), truth.active_product_named("KTV02")
    r = ask(loop, agent, "So sánh KTV01 và KTV02")
    rows = next(x.data for x in r.blocks if x.kind == "product_comparison")
    assert [row["product_id"] for row in rows] == [a["id"], b["id"]]
    assert rows[0]["price"] == float(a["product_variants"][0]["price"])


def test_unknown_product_is_not_invented(loop, agent):
    r = ask(loop, agent, "Giá ZZX999 bao nhiêu")
    assert r.message == NO_INFO


def test_categories_match_supabase(loop, agent, truth):
    r = ask(loop, agent, "Danh mục sản phẩm gồm những gì")
    got = {i["name"] for i in next(b.data for b in r.blocks if b.kind == "taxonomy")["items"]}
    assert got == {c["name"] for c in truth.get("categories", select="name")}


def test_branches_match_supabase_stores(loop, agent, truth):
    r = ask(loop, agent, "Cửa hàng ở Hồ Chí Minh có chi nhánh nào")
    got = next(b.data for b in r.blocks if b.kind == "branch_list")
    retailer_ids = {s["id"] for s in truth.get("suppliers", type="eq.retailer", status="eq.active", select="id")}
    real = [s for s in truth.get("stores", select="id,supplier_id,city") if s["supplier_id"] in retailer_ids
            and "Hồ Chí Minh" in (s["city"] or "")]
    assert {b["id"] for b in got} == {s["id"] for s in real}


# ---------------------------------------------------------------- inventory (realtime, không công khai)
def test_inventory_for_guest_is_unknown_not_guessed(loop, agent, truth):
    assert truth.get("store_inventory", select="store_id", limit="1") == []  # dữ liệu thật: chưa có tồn kho
    r = ask(loop, agent, "KTV01 còn bao nhiêu cái?")
    assert r.message == f"- {truth.active_product_named('KTV01')['name']} — chưa có dữ liệu tồn kho"
    assert "còn " not in r.message.split(":", 1)[-1] or "chưa" in r.message


# ---------------------------------------------------------------- knowledge chưa có trên Backend/Supabase
@pytest.mark.parametrize("msg", [
    "Chính sách đổi trả thế nào?", "Bảo hành bao lâu?", "Có voucher nào không?", "Hướng dẫn tạo mẫu 3D từ ảnh",
])
def test_missing_knowledge_answers_unverified(loop, agent, msg):
    r = ask(loop, agent, msg)
    assert "chưa có thông tin đã xác minh" in r.message or r.message == NO_INFO
    assert no_unverified_numbers(r.message) and not r.sources


@pytest.mark.parametrize("msg", ["Giờ mở cửa là mấy giờ?", "Hotline của shop là gì?"])
def test_contact_question_without_supplier_asks_which_supplier(loop, agent, truth, msg):
    # WoodHub là sàn nhiều nhà cung cấp: không có "thông tin cửa hàng chung" → hỏi lại, gợi ý nhà cung cấp THẬT
    r = ask(loop, agent, msg)
    real = {s["business_name"] for s in truth.get("suppliers", select="business_name")}
    shown = {b["name"] for b in r.blocks[0].data} if r.blocks else set()
    assert r.type == "clarification" and "nhà cung cấp nào" in r.message and shown and shown <= real


def test_workshops_access_decided_by_backend(loop, agent):
    # Agent không tự chặn guest: gọi Backend (API này của Backend yêu cầu đăng nhập) và chuyển lời từ chối của Backend
    r = ask(loop, agent, "Tìm xưởng gần tôi", location=(10.77, 106.70))
    assert r.meta.tools_used == ["find_nearby_workshops"] and r.message == BACKEND_DENIED


def test_out_of_scope_and_cart(loop, agent):
    assert ask(loop, agent, "thời tiết hôm nay").message.startswith("Xin lỗi")
    assert "giỏ hàng" in ask(loop, agent, "thêm vào giỏ hàng").message


# ---------------------------------------------------------------- HTTP contract trên dữ liệu thật
@pytest.fixture
def http(audit_sink):
    container, _ = build_live(audit_sink)
    with TestClient(create_app(make_settings(), container=container)) as c:
        yield c


def test_http_chat_contract(http, truth):
    real = truth.active_product_named("KTV01")
    res = http.post("/v1/agent/chat", json={"message": "Giá KTV01"})
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "answer" and body["meta"]["role"] == "customer" and body["meta"]["profile"] == "customer"
    assert body["session_id"] and body["request_id"]
    assert vnd(float(real["product_variants"][0]["price"])) in body["message"]


def test_http_stream_events(http):
    res = http.post("/v1/agent/chat/stream", json={"message": "Tìm kệ tivi"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = re.findall(r"^event: (\w+)$", res.text, flags=re.M)
    assert events[0] == "meta" and events[-1] == "done" and "delta" in events and "block" in events


def test_legacy_chat_sse_keeps_old_shape_with_real_ids(http, truth):
    res = http.post("/chat", json={"query": "Tìm kệ tivi", "session_id": "legacy-1"})
    payloads = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    assert payloads[0]["type"] == "chunk" and payloads[-1]["type"] == "done"
    products = next(p["payload"] for p in payloads if p["type"] == "debug_data")
    real_ids = {p["id"] for p in truth.get("products", select="id")}
    assert products and all(p["id"] in real_ids for p in products)
    assert set(products[0]) >= {"id", "name", "description", "price", "status"}


def test_agent_does_not_validate_user_tokens(http, truth):
    # Agent không xác thực người dùng: token lạ/không có token không làm Agent từ chối; dữ liệu công khai vẫn trả về
    real = truth.active_product_named("KTV01")
    for headers in ({}, {"Authorization": "Bearer not-a-real-token"}, {"Authorization": "Basic xyz"}):
        res = http.post("/v1/agent/chat", json={"message": "Giá KTV01"}, headers=headers)
        assert res.status_code == 200 and res.json()["message"].startswith(f"- {real['name']} — ")


def test_manage_route_is_the_same_read_only_assistant(http):
    res = http.post("/v1/agent/manage/chat", json={"message": "Đổi giá KTV01 thành 1000đ"})
    assert res.status_code == 200 and "không thay đổi dữ liệu" in res.json()["message"] and res.json()["action"] is None


def test_validation_error_envelope(http):
    res = http.post("/v1/agent/chat", json={"message": "   "})
    assert res.status_code == 422 and res.json()["code"] == "VALIDATION_ERROR"
