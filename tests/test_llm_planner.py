"""
LLM planner (Bedrock Converse tool use) với dữ liệu THẬT từ Backend.
Chỉ model được thay bằng client kịch bản (mô phỏng quyết định của LLM), dữ liệu tool là dữ liệu thật.
"""
import pytest

from app.agent.composer import vnd
from app.agent.llm import LLMUnavailable
from app.domain.principal import Role
from app.tools.base import AgentProfile
from tests.conftest import build_live, principal

pytestmark = pytest.mark.usefixtures("live")


class ScriptedLLM:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    async def converse(self, *, system, messages, tools):
        self.requests.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t["toolSpec"]["name"] for t in tools]})
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(messages)
        return step


def tool_use(name, args, tid="t1"):
    return {"stopReason": "tool_use", "output": {"message": {"role": "assistant",
            "content": [{"toolUse": {"toolUseId": tid, "name": name, "input": args}}]}}}


def final(text):
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


def agent_with(audit_sink, llm):
    container, intercept = build_live(audit_sink, AGENT_PLANNER="llm", BEDROCK_MODEL_ID="test-model")
    container.agent.llm._client = llm
    return container.agent, intercept


def run(loop, agent, msg, role=Role.GUEST, profile=AgentProfile.CUSTOMER):
    return loop.run_until_complete(agent.handle_turn(message=msg, principal=principal(role, "u-llm"), profile=profile,
                                                     request_id="req-llm-0001", session_id="s-llm"))


def test_grounded_llm_answer_is_used(loop, audit_sink, truth):
    real = truth.active_product_named("KTV01")
    price = vnd(float(real["product_variants"][0]["price"]))
    llm = ScriptedLLM([tool_use("get_product", {"sku": "KTV01"}), final(f"{real['name']} hiện có giá {price}.")])
    agent, _ = agent_with(audit_sink, llm)
    r = run(loop, agent, "kệ tivi KTV01 bao tiền vậy")
    assert r.meta.planner == "llm" and r.message == f"{real['name']} hiện có giá {price}."
    tool_msg = llm.requests[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert "untrusted_tool_data" in tool_msg  # kết quả tool được đánh dấu là dữ liệu, không phải chỉ dẫn


def test_hallucinated_number_falls_back_to_template(loop, audit_sink, truth):
    real = truth.active_product_named("KTV01")
    llm = ScriptedLLM([tool_use("get_product", {"sku": "KTV01"}), final("Giá chỉ 999.000 ₫, đang giảm 50%!")])
    agent, _ = agent_with(audit_sink, llm)
    r = run(loop, agent, "giá KTV01")
    assert "999.000" not in r.message and vnd(float(real["product_variants"][0]["price"])) in r.message


def test_llm_cannot_call_mutation_from_customer_profile(loop, audit_sink):
    llm = ScriptedLLM([tool_use("update_product_price", {"sku": "KTV01", "new_price": 1000}), final("Đã đổi giá xong!")])
    agent, intercept = agent_with(audit_sink, llm)
    r = run(loop, agent, "đổi giá KTV01 thành 1000đ giúp tôi", role=Role.CUSTOMER)
    assert "update_product_price" not in llm.requests[0]["tools"]  # LLM không được thấy tool
    assert r.action is None and "Đã đổi giá xong" not in r.message and intercept.writes == []


def test_llm_mutation_stops_at_confirmation(loop, audit_sink):
    llm = ScriptedLLM([tool_use("update_product_price", {"sku": "KTV01", "new_price": 8_000_000}), final("Đã cập nhật giá!")])
    agent, intercept = agent_with(audit_sink, llm)
    r = run(loop, agent, "đổi giá KTV01 lên 8 triệu", role=Role.SUPPLIER, profile=AgentProfile.MANAGEMENT)
    assert r.type == "confirmation_required" and r.meta.planner == "llm"
    assert len(llm.requests) == 1 and "Đã cập nhật" not in r.message and intercept.writes == []


def test_llm_injected_extra_args_are_rejected(loop, audit_sink):
    llm = ScriptedLLM([tool_use("search_products", {"keyword": "kệ", "role": "admin", "sql": "drop table"}),
                       final("Không có dữ liệu")])
    agent, _ = agent_with(audit_sink, llm)
    r = run(loop, agent, "tìm kệ")
    assert r.meta.planner == "llm"
    assert "Tham số không được phép" in r.message and not r.blocks  # không chạy tool với tham số lạ


def test_llm_unavailable_falls_back_to_rules(loop, audit_sink, truth):
    agent, _ = agent_with(audit_sink, ScriptedLLM([LLMUnavailable("throttled")]))
    r = run(loop, agent, "Giá KTV01")
    real = truth.active_product_named("KTV01")
    assert r.meta.planner == "rules" and vnd(float(real["product_variants"][0]["price"])) in r.message


def test_llm_answer_without_tools_is_not_trusted(loop, audit_sink):
    agent, _ = agent_with(audit_sink, ScriptedLLM([final("Cửa hàng mở cửa 8h-22h, hotline 1900 1234.")]))
    r = run(loop, agent, "Giờ mở cửa là mấy giờ?")
    assert r.meta.planner == "rules" and "1900 1234" not in r.message and "chưa có thông tin đã xác minh" in r.message
