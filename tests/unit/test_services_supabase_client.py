"""SupabaseClient over httpx.MockTransport: headers, bearer clones, error parsing, request shapes."""
from __future__ import annotations

import json

import httpx
import pytest

from services.supabase_client import SupabaseClient, SupabaseError

URL = "https://proj.supabase.co"
KEY = "service-role-KEY-123"


class Recorder:
    def __init__(self, response=None):
        self.requests: list[httpx.Request] = []
        self.response = response if response is not None else httpx.Response(200, json=[])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response(request) if callable(self.response) else self.response


def _client(rec, **kw) -> SupabaseClient:
    return SupabaseClient(URL, KEY, transport=httpx.MockTransport(rec), **kw)


def test_requires_url_and_key():
    with pytest.raises(ValueError):
        SupabaseClient("", KEY)
    with pytest.raises(ValueError):
        SupabaseClient(URL, "")


def test_default_headers_and_base(monkeypatch):
    rec = Recorder()
    c = _client(rec)
    assert c.base == f"{URL}/rest/v1"
    c.select("loans")
    req = rec.requests[0]
    assert req.method == "GET" and req.url.path == "/rest/v1/loans"
    assert req.url.params["select"] == "*"
    assert req.headers["apikey"] == KEY
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert req.headers["Accept-Profile"] == "public" and req.headers["Content-Profile"] == "public"
    assert req.headers["Content-Type"] == "application/json"
    assert "Prefer" not in req.headers


def test_trailing_slash_and_custom_schema():
    rec = Recorder()
    c = SupabaseClient(URL + "/", KEY, transport=httpx.MockTransport(rec), schema="audit")
    c.select("runs")
    assert rec.requests[0].url == f"{URL}/rest/v1/runs?select=%2A"
    assert rec.requests[0].headers["Accept-Profile"] == "audit"


def test_bearer_overrides_authorization_but_not_apikey():
    rec = Recorder()
    _client(rec, bearer="user-jwt").select("runs")
    req = rec.requests[0]
    assert req.headers["Authorization"] == "Bearer user-jwt" and req.headers["apikey"] == KEY


def test_with_bearer_clone_shares_connection_and_leaves_original_untouched():
    rec = Recorder()
    c = _client(rec)
    clone = c.with_bearer("jwt-2")
    assert clone is not c and clone._http is c._http and clone.base == c.base
    clone.select("runs")
    c.select("runs")
    assert rec.requests[0].headers["Authorization"] == "Bearer jwt-2"
    assert rec.requests[1].headers["Authorization"] == f"Bearer {KEY}"
    assert rec.requests[0].headers["apikey"] == KEY


def test_select_params_and_select_one_limit():
    rec = Recorder(httpx.Response(200, json=[{"loan_id": "LN-1"}, {"loan_id": "LN-2"}]))
    c = _client(rec)
    rows = c.select("loans", {"loan_id": "eq.LN-1", "order": "loan_id.asc"})
    assert rows == [{"loan_id": "LN-1"}, {"loan_id": "LN-2"}]
    p = rec.requests[0].url.params
    assert (p["select"], p["loan_id"], p["order"]) == ("*", "eq.LN-1", "loan_id.asc")
    one = c.select_one("loans", {"loan_id": "eq.LN-1"})
    assert one == {"loan_id": "LN-1"}
    assert rec.requests[1].url.params["limit"] == "1"


def test_select_one_none_and_null_body():
    rec = Recorder(httpx.Response(200, content=b"null", headers={"content-type": "application/json"}))
    c = _client(rec)
    assert c.select("loans") == []
    assert c.select_one("loans") is None


def test_no_content_and_empty_body_return_none():
    assert _client(Recorder(httpx.Response(204)))._request("DELETE", "runs", params={"loan_id": "eq.x"}) is None
    assert _client(Recorder(httpx.Response(200, content=b"")))._request("GET", "runs") is None


def test_insert_upsert_update_delete_rpc_request_shapes():
    rec = Recorder(httpx.Response(201, json=[{"ok": 1}]))
    c = _client(rec)
    assert c.insert("runs", [{"a": 1}]) == [{"ok": 1}]
    assert c.upsert("loans", [{"loan_id": "LN-1"}], on_conflict="loan_id") == [{"ok": 1}]
    assert c.update("run_requests", {"request_id": "eq.r1"}, {"status": "PICKED_UP"}) == [{"ok": 1}]
    c.delete("runs", {"loan_id": "eq.LN-1", "run_id": "eq.R-1"})
    c.rpc("search_findings", {"q": "paystub", "max_rows": 5})
    c.rpc("noargs")
    ins, ups, upd, dele, rpc, rpc0 = rec.requests
    assert ins.method == "POST" and ins.url.path == "/rest/v1/runs" and ins.headers["Prefer"] == "return=representation"
    assert json.loads(ins.content) == [{"a": 1}]
    assert ups.method == "POST" and ups.url.params["on_conflict"] == "loan_id"
    assert ups.headers["Prefer"] == "resolution=merge-duplicates,return=representation"
    assert upd.method == "PATCH" and upd.url.params["request_id"] == "eq.r1" and json.loads(upd.content) == {"status": "PICKED_UP"}
    assert upd.headers["Prefer"] == "return=representation"
    assert dele.method == "DELETE" and dele.headers["Prefer"] == "return=minimal"
    assert dict(dele.url.params) == {"loan_id": "eq.LN-1", "run_id": "eq.R-1"}
    assert rpc.method == "POST" and rpc.url.path == "/rest/v1/rpc/search_findings"
    assert json.loads(rpc.content) == {"q": "paystub", "max_rows": 5}
    assert json.loads(rpc0.content) == {}


def test_insert_and_upsert_skip_empty_rows_without_a_request():
    rec = Recorder()
    c = _client(rec)
    assert c.insert("runs", []) == [] and c.upsert("loans", [], on_conflict="loan_id") == []
    assert rec.requests == []


def test_delete_refuses_empty_filter():
    rec = Recorder()
    c = _client(rec)
    with pytest.raises(ValueError, match="refusing to delete"):
        c.delete("runs", {})
    assert rec.requests == []


@pytest.mark.parametrize("body, headers, expected", [
    (b'{"message": "duplicate key", "code": "23505"}', {"content-type": "application/json"}, "duplicate key"),
    (b'{"hint": "check the filter", "details": "x"}', {"content-type": "application/json"}, "check the filter"),
    (b"plain text failure", {"content-type": "text/plain"}, "plain text failure"),
])
def test_error_parsing(body, headers, expected):
    c = _client(Recorder(httpx.Response(409, content=body, headers=headers)))
    with pytest.raises(SupabaseError) as exc:
        c.insert("review_decisions", [{"x": 1}])
    assert exc.value.status == 409 and exc.value.message == expected and exc.value.path == "review_decisions"
    assert "409" in str(exc.value) and "review_decisions" in str(exc.value)
    assert KEY not in str(exc.value)


def test_error_message_truncated_to_500():
    c = _client(Recorder(httpx.Response(500, json={"message": "x" * 2000})))
    with pytest.raises(SupabaseError) as exc:
        c.select("runs")
    assert len(exc.value.message) == 500


def test_transport_errors_propagate_as_httpx_errors():
    def boom(request):
        raise httpx.ConnectError("connection refused", request=request)
    c = _client(Recorder(boom))
    with pytest.raises(httpx.ConnectError):
        c.select("runs")


def test_close():
    c = _client(Recorder())
    c.close()
    assert c._http.is_closed
