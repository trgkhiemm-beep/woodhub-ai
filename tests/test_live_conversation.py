"""
LIVE: hội thoại nhiều lượt trên DỮ LIỆU THẬT — memory, tham chiếu ("mẫu 2", "cái này", "nhỏ hơn", "rẻ hơn"),
làm rõ khi thiếu thông tin, tư vấn dựa trên catalog thật (đối chiếu Supabase).
"""
import re

import pytest

from app.agent.composer import vnd
from app.domain.messages import NO_INFO
from app.nlu.lexicon import fold
from app.tools.advisor import ROOM_AFFINITY, parse_dimensions
from app.tools.base import AgentProfile
from tests.conftest import build_live, principal

pytestmark = pytest.mark.usefixtures("live")


class Chat:
    def __init__(self, loop, agent, who=None):
        self.loop, self.agent, self.sid = loop, agent, None
        self.who = who or principal()

    def __call__(self, msg):
        r = self.loop.run_until_complete(self.agent.handle_turn(message=msg, principal=self.who, profile=AgentProfile.CUSTOMER,
                                                                request_id="req-conv-0001", session_id=self.sid))
        self.sid = r.session_id
        return r


@pytest.fixture
def chat(loop, audit_sink):
    container, _ = build_live(audit_sink)
    return Chat(loop, container.agent)


def rec_items(r):
    return next(b.data for b in r.blocks if b.kind == "recommendation")["items"]


@pytest.fixture(scope="module")
def catalog(truth):
    rows = truth.get("products", status="eq.active", select="id,name,categories(name),product_variants(price,dimensions)")
    return {p["id"]: p for p in rows}


def test_needs_context_and_budget_follow_up(chat, catalog):
    r1 = chat("Tôi cần bàn ăn cho 6 người.")
    items = rec_items(r1)
    assert items and all("ban an" in fold(i["name"]) for i in items)
    r2 = chat("10 triệu")
    items2 = rec_items(r2)
    assert items2 and len(r2.message.splitlines()) == len(items2)  # chỉ "- Tên — giá", không lời dẫn
    for it in items2:
        real = catalog[it["id"]]
        assert min(float(v["price"]) for v in real["product_variants"]) <= 10_000_000
        assert "ban an" in fold(real["name"])
        assert it["seats"] is None or it["seats"] >= 6


def test_smaller_uses_reference_dimensions(chat, catalog):
    r0 = chat("Tôi cần bàn ăn cho 6 người, 10 triệu")
    ref = catalog[rec_items(r0)[0]["id"]]
    r = chat("Có mẫu nhỏ hơn không?")
    ref_area = parse_dimensions(ref["product_variants"][0]["dimensions"]).area
    items = rec_items(r) if r.blocks else []
    if not items:
        assert r.message == NO_INFO
    for it in items:
        assert it["area_cm2"] < ref_area and it["id"] != ref["id"]
        assert f"- {it['name']} — " in r.message


def test_ordinal_and_this_resolve_to_shown_products(chat, catalog):
    r = chat("tìm kệ tivi")
    items = rec_items(r)
    assert len(items) >= 2
    second = catalog[items[1]["id"]]
    r2 = chat("mẫu 2 giá bao nhiêu")
    assert second["name"] in r2.message and vnd(float(second["product_variants"][0]["price"])) in r2.message
    r3 = chat("cái này còn hàng không")
    assert r3.meta.tools_used == ["get_inventory"] and r3.message == f"- {second['name']} — chưa có dữ liệu tồn kho"
    r4 = chat("mẫu 9 bao nhiêu tiền")
    assert r4.type == "clarification"


def test_cheaper_than_current_product(chat, catalog, truth):
    ktv = truth.active_product_named("KTV01")
    price = float(ktv["product_variants"][0]["price"])
    chat("Giá KTV01")
    r = chat("cái này rẻ hơn được không")
    assert ktv["name"] not in r.message  # chỉ liệt kê mẫu rẻ hơn, không lặp lại mẫu tham chiếu
    for it in rec_items(r):
        assert it["price"] < price and it["id"] != ktv["id"]


def test_compare_shown_products(chat, catalog):
    items = rec_items(chat("tìm bàn ăn"))
    r = chat("so sánh mẫu 1 và mẫu 2")
    rows = next(b.data for b in r.blocks if b.kind == "product_comparison")
    assert [x["product_id"] for x in rows] == [items[0]["id"], items[1]["id"]]


def test_clarification_asked_once_then_recommend(chat):
    r1 = chat("Tư vấn cho tôi một cái bàn")
    assert r1.type == "clarification" and "ngân sách" in r1.message.lower()
    r2 = chat("tầm 3 triệu, để phòng làm việc")
    assert r2.type == "answer" and all(i["price"] <= 3_000_000 for i in rec_items(r2))
    assert all(any(fold(t) in fold(i["name"]) for t in ROOM_AFFINITY["phong lam viec"]) or "ban" in fold(i["name"])
               for i in rec_items(r2))


def test_missing_reference_is_not_guessed(chat):
    r = chat("cái này giá bao nhiêu")
    assert r.type == "clarification" and not re.search(r"\d\.\d{3}đ", r.message)
    assert chat("Có mẫu rẻ hơn không?").type == "clarification"


def test_unknown_category_asks_first(chat):
    assert chat("tư vấn giúp mình với").type == "clarification"


def test_unknown_session_id_starts_empty_session(loop, audit_sink):
    # Agent không biết danh tính người dùng; phiên do server tạo ngẫu nhiên, Backend giữ ánh xạ phiên ↔ người dùng.
    container, _ = build_live(audit_sink)
    a = Chat(loop, container.agent)
    a("tìm kệ tivi")
    assert a.sid.startswith("s_") and len(a.sid) >= 16  # không đoán được
    b = Chat(loop, container.agent)
    b.sid = "s_unknown-session"
    r = b("mẫu 1 giá bao nhiêu")
    assert r.type == "clarification"  # phiên lạ không có ngữ cảnh của ai
