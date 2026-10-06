"""
Resilience của BackendClient. Lỗi hạ tầng được mô phỏng ở tầng transport HTTP (không phải dữ liệu):
timeout, 5xx, JSON hỏng, 403/404/409/429, endpoint ngoài allowlist, không retry request không idempotent.
"""
import httpx
import pytest

from app.adapters.backend.adapters import BackendCatalogAdapter, BackendInventoryAdapter
from app.adapters.backend.client import BackendClient
from app.domain import errors
from app.domain.models import SearchCriteria
from app.domain.principal import Principal

GUEST = Principal.guest()
PID = "c391aff3-5459-4ef7-852a-113f7a90baba"


def client_with(handler, retries=2):
    return BackendClient("https://backend.test", transport=httpx.MockTransport(handler), max_retries=retries, backoff_base=0)


def test_timeout_retries_get_then_raises(loop):
    calls = []

    def handler(req):
        calls.append(req.method)
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(errors.UpstreamTimeout):
        loop.run_until_complete(client_with(handler).request("GET", "/api/categories"))
    assert len(calls) == 3  # 1 + 2 retry


def test_5xx_then_success_recovers(loop):
    seq = iter([httpx.Response(503), httpx.Response(200, json=[])])
    assert loop.run_until_complete(client_with(lambda r: next(seq)).request("GET", "/api/categories")) == []


@pytest.mark.parametrize("method,path", [("PATCH", f"/api/stores/{PID}/inventory/{PID}"), ("PUT", f"/api/variants/{PID}"),
                                         ("PUT", f"/api/products/{PID}"), ("POST", "/api/categories"),
                                         ("DELETE", f"/api/products/{PID}")])
def test_writes_are_blocked_before_any_request(loop, method, path):
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(200, json={})

    with pytest.raises(errors.Forbidden):
        loop.run_until_complete(client_with(handler).request(method, path, json={"x": 1}))
    assert calls == []  # agent chỉ đọc: không request ghi nào rời khỏi AI service


@pytest.mark.parametrize("status,exc", [(400, errors.ValidationFailed), (401, errors.Unauthenticated), (403, errors.Forbidden),
                                        (404, errors.NotFound), (409, errors.Conflict), (429, errors.RateLimited)])
def test_status_mapping(loop, status, exc):
    with pytest.raises(exc):
        loop.run_until_complete(client_with(lambda r: httpx.Response(status, text="x"), retries=0).request("GET", "/api/categories"))


def test_malformed_json(loop):
    c = client_with(lambda r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(errors.MalformedResponse):
        loop.run_until_complete(c.request("GET", "/api/categories"))


def test_malformed_shape_is_rejected_not_guessed(loop):
    c = client_with(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(errors.MalformedResponse):
        loop.run_until_complete(BackendCatalogAdapter(c).get_product(PID, GUEST))
    c2 = client_with(lambda r: httpx.Response(200, json={"content": "not-a-list", "page": {}}))
    with pytest.raises(errors.MalformedResponse):
        loop.run_until_complete(BackendCatalogAdapter(c2).search_products(SearchCriteria(), GUEST))


def test_endpoint_allowlist_blocks_arbitrary_calls(loop):
    c = client_with(lambda r: httpx.Response(200, json={}))
    for method, path in [("DELETE", f"/api/products/{PID}"), ("GET", "/api/users"), ("POST", "/api/payments/subscription"),
                         ("GET", "/api/products/../users"), ("PUT", "/api/users/x/password")]:
        with pytest.raises(errors.Forbidden):
            loop.run_until_complete(c.request(method, path))


def test_forwarded_token_is_passed_through_untouched(loop):
    # Agent không giải mã/kiểm tra token: chuyển tiếp NGUYÊN TRẠNG cho Backend, Backend quyết định quyền
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": PID, "name": "X", "variants": []})

    catalog = BackendCatalogAdapter(client_with(handler))
    loop.run_until_complete(catalog.get_product(PID, Principal(access_token="opaque.not-a-jwt")))
    assert seen["auth"] == "Bearer opaque.not-a-jwt"
    loop.run_until_complete(catalog.get_product(PID, GUEST))
    assert seen["auth"] is None  # không có token → không gửi header


def test_inventory_denied_by_backend_is_unknown_not_guessed(loop):
    inv = BackendInventoryAdapter(client_with(lambda r: httpx.Response(403), retries=0))
    with pytest.raises(errors.CapabilityUnavailable):
        loop.run_until_complete(inv.get_inventory(PID, GUEST))
