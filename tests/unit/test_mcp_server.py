"""mpire-audit MCP server: tools, annotations, shapes, limits, errors, writes, resources, prompt, selftest.

Everything runs through the SDK's in-process client (mcp.Client(server) -> in-memory transport)
against a seeded InMemoryRepository, so no network and no Supabase.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

import mcp_server.server as srv
from services.config import Settings
from services.demo import seed_demo
from services.repository import InMemoryRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTITY = "test-identity"
FORBIDDEN = re.compile(r"send|email|sms|los|aus|trid|submit|price|lock|intake|audit_run")
SENTINEL_KEY = "sk-SENTINEL-NEVER-PRINT-0123456789"

READ_TOOLS = {
    "mortgage_summary", "mortgage_list_loans", "mortgage_list_runs", "mortgage_get_run", "mortgage_list_findings",
    "mortgage_get_finding", "mortgage_search_findings", "mortgage_list_documents", "mortgage_review_queue",
    "mortgage_get_report", "mortgage_list_missing_documents", "mortgage_list_conflicts",
    "mortgage_list_proposed_actions", "mortgage_list_approvals_required", "mortgage_eval_latest",
    "mortgage_list_run_requests",
}
WRITE_TOOLS = {"mortgage_create_run_request", "mortgage_record_review_decision", "mortgage_record_action_decision"}

L1, R1 = "LN-EXAMPLE-0001", "RUN-DEMO-0001"   # READY
L2, R2 = "LN-EXAMPLE-0002", "RUN-DEMO-0002"   # NOT_READY, review item RV-001
L3, R3 = "LN-EXAMPLE-0003", "RUN-DEMO-0003"   # preapproval only, 5 findings, proposed actions


# ---- harness ----------------------------------------------------------------------------------


class Harness:
    """Sync facade over the async client so tests stay plain functions."""

    def __init__(self, repo: InMemoryRepository, server) -> None:
        self.repo = repo
        self.server = server

    def _run(self, fn):
        async def go():
            async with Client(self.server) as client:
                return await fn(client)
        try:
            return anyio.run(go)
        except BaseExceptionGroup as group:  # the client's task group wraps a single protocol error
            leaf = group
            while isinstance(leaf, BaseExceptionGroup) and len(leaf.exceptions) == 1:
                leaf = leaf.exceptions[0]
            raise leaf from None

    def call(self, _tool: str, **args: Any):
        return self._run(lambda c: c.call_tool(_tool, args))

    def ok(self, _tool: str, **args: Any) -> dict[str, Any]:
        res = self.call(_tool, **args)
        assert not res.is_error, res.content[0].text if res.content else "no content"
        assert isinstance(res.structured_content, dict)
        json.dumps(res.structured_content)  # JSON-serializable
        return res.structured_content

    def err(self, _tool: str, **args: Any) -> str:
        res = self.call(_tool, **args)
        assert res.is_error, "expected an error result"
        text = res.content[0].text
        assert "Traceback" not in text and "  File " not in text
        return text

    def tools(self):
        return self._run(lambda c: c.list_tools()).tools

    def instructions(self) -> str:
        async def go(c):
            return c.instructions
        return self._run(go)

    def read(self, uri: str) -> str:
        return self._run(lambda c: c.read_resource(uri)).contents[0].text

    def resources(self):
        return self._run(lambda c: c.list_resources()).resources

    def templates(self):
        return self._run(lambda c: c.list_resource_templates()).resource_templates

    def prompts(self):
        return self._run(lambda c: c.list_prompts()).prompts

    def prompt(self, name: str, **args: str) -> str:
        return self._run(lambda c: c.get_prompt(name, args)).messages[0].content.text


@pytest.fixture
def h() -> Harness:
    repo = InMemoryRepository()
    seed_demo(repo)
    return Harness(repo, srv.build_server(repository=repo, identity=IDENTITY))


# ---- catalogue --------------------------------------------------------------------------------


def test_tool_catalogue_names_and_annotations(h: Harness):
    tools = {t.name: t for t in h.tools()}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name, tool in tools.items():
        assert name.startswith("mortgage_")
        assert not FORBIDDEN.search(name), name
        assert tool.description and len(tool.description) > 30
        a = tool.annotations
        assert a is not None, name
        assert a.destructive_hint is False and a.open_world_hint is False, name
        if name in READ_TOOLS:
            assert a.read_only_hint is True and a.idempotent_hint is True, name
        else:
            assert a.read_only_hint is False and a.idempotent_hint is False, name
        assert tool.output_schema and tool.output_schema.get("type") == "object", name


def test_write_tool_descriptions_state_human_gate(h: Harness):
    tools = {t.name: t for t in h.tools()}
    assert "/mortgage-file-audit" in tools["mortgage_create_run_request"].description
    assert "NOT run" in tools["mortgage_create_run_request"].description
    for name in ("mortgage_record_review_decision", "mortgage_record_action_decision"):
        d = tools[name].description
        assert "named human" in d and "ONLY" in d and "no external effect" in d


def test_instructions_state_what_the_server_cannot_do(h: Harness):
    text = h.instructions()
    for phrase in ("NO tool", "email or SMS", "LOS", "AUS", "TRID", "prices", "lender", "submits", "source document", "audit\nrun"):
        assert phrase in text, phrase
    assert "PASS, FAIL, MISSING, REVIEW, NOT_APPLICABLE" in text
    assert "READY, NOT_READY, HUMAN_REVIEW" in text


def test_limit_schema_has_ceiling(h: Harness):
    tools = {t.name: t for t in h.tools()}
    lim = tools["mortgage_list_findings"].input_schema["properties"]["limit"]
    assert lim["minimum"] == 1 and lim["maximum"] == srv.MAX_LIST_LIMIT and lim["default"] == 100
    assert tools["mortgage_search_findings"].input_schema["properties"]["limit"]["maximum"] == srv.MAX_SEARCH_LIMIT
    assert "PREAPPROVAL" in json.dumps(tools["mortgage_get_finding"].input_schema)


# ---- read tools -------------------------------------------------------------------------------


def test_summary_matches_repository(h: Harness):
    out = h.ok("mortgage_summary")
    expected = h.repo.dashboard_summary().model_dump()
    assert {k: out[k] for k in expected} == expected
    assert out["loans"] == 3 and out["ready"] == 1 and out["not_ready"] == 1 and out["no_gate"] == 1


def test_list_loans_with_latest_run(h: Harness):
    out = h.ok("mortgage_list_loans")
    assert out["count"] == 3 and [l["loan_id"] for l in out["loans"]] == [L1, L2, L3]
    by_id = {l["loan_id"]: l for l in out["loans"]}
    assert by_id[L1]["latest_run"]["overall_status"] == "READY"
    assert by_id[L2]["latest_run"]["overall_status"] == "NOT_READY" and by_id[L2]["latest_run"]["blocking_open"] == 1
    assert by_id[L3]["latest_run"]["overall_status"] is None
    assert "known_limitations" not in by_id[L1]["latest_run"]  # summary view, not the full row


def test_list_runs_and_limit(h: Harness):
    out = h.ok("mortgage_list_runs")
    assert out["count"] == 3 and out["limit"] == 20
    out = h.ok("mortgage_list_runs", loan_id=L2)
    assert out["count"] == 1 and out["runs"][0]["run_id"] == R2
    out = h.ok("mortgage_list_runs", limit=1)
    assert out["count"] == 1
    assert "not found" in h.err("mortgage_list_runs", loan_id="LN-NOPE")
    assert "validation" in h.err("mortgage_list_runs", limit=0).lower()
    assert h.call("mortgage_list_runs", limit=srv.MAX_RUNS_LIMIT + 1).is_error


def test_get_run_is_small_and_complete(h: Harness):
    out = h.ok("mortgage_get_run", loan_id=L2, run_id=R2)
    assert out["overall_status"] == "NOT_READY" and out["counts"]["MISSING"] == 1
    assert isinstance(out["known_limitations"], list)
    assert out["report_names"] == ["report.md"]
    assert out["child_counts"]["documents"] == 7 and out["child_counts"]["findings"] == 2
    assert out["decision_counts"] == {"review": 0, "action": 0}
    for heavy in ("documents", "findings", "reports", "bundle"):
        assert heavy not in out
    msg = h.err("mortgage_get_run", loan_id=L2, run_id="RUN-NOPE")
    assert "not found" in msg and "mortgage_list_runs" in msg


def test_list_findings_blocking_first_filters_and_limit(h: Harness):
    out = h.ok("mortgage_list_findings", loan_id=L3, run_id=R3)
    assert out["count"] == out["total"] == 5 and out["truncated"] is False
    flags = [f["blocking"] for f in out["findings"]]
    assert flags == sorted(flags, reverse=True)
    assert all("calculation" not in f and "has_calculation" in f for f in out["findings"])
    assert h.ok("mortgage_list_findings", loan_id=L3, run_id=R3, result="MISSING")["count"] == 1
    n_open = len(h.repo.list_findings(L3, R3, blocking=False))
    assert 0 < n_open < 5 and h.ok("mortgage_list_findings", loan_id=L3, run_id=R3, blocking=False)["count"] == n_open
    assert h.ok("mortgage_list_findings", loan_id=L3, run_id=R3, audit_type="SUBMISSION_READINESS")["count"] == 0
    rule = out["findings"][0]["rule_id"]
    assert h.ok("mortgage_list_findings", loan_id=L3, run_id=R3, rule_id=rule)["count"] >= 1
    page = h.ok("mortgage_list_findings", loan_id=L3, run_id=R3, limit=2)
    assert page["count"] == 2 and page["total"] == 5 and page["truncated"] is True
    assert h.call("mortgage_list_findings", loan_id=L3, run_id=R3, result="MAYBE").is_error


def test_get_finding_full_and_unknown(h: Harness):
    listed = h.ok("mortgage_list_findings", loan_id=L1, run_id=R1, audit_type="PREAPPROVAL")["findings"]
    with_calc = next(f for f in listed if f["has_calculation"])
    out = h.ok("mortgage_get_finding", loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", finding_id=with_calc["finding_id"])
    assert isinstance(out["calculation"], dict) and isinstance(out["evidence_ids"], list)
    assert out["review_decisions"] == [] and "discrepancy" in out  # full dump keeps nulls
    msg = h.err("mortgage_get_finding", loan_id=L1, run_id=R1, audit_type="PREAPPROVAL", finding_id="F-999")
    assert "F-999" in msg and "not found" in msg


def test_search_findings(h: Harness):
    out = h.ok("mortgage_search_findings", query="loan amount")
    assert out["count"] >= 1 and all({"loan_id", "run_id", "audit_type", "finding_id"} <= set(f) for f in out["findings"])
    assert h.ok("mortgage_search_findings", query="loan amount", limit=1)["count"] == 1
    assert h.ok("mortgage_search_findings", query="zzz-no-such-text")["count"] == 0
    assert "blank" in h.err("mortgage_search_findings", query="   ")


def test_documents_missing_conflicts_actions_approvals(h: Harness):
    docs = h.ok("mortgage_list_documents", loan_id=L2, run_id=R2)
    assert docs["count"] == 7 and all(len(d["sha256"]) == 64 for d in docs["documents"])
    assert h.ok("mortgage_list_missing_documents", loan_id=L2, run_id=R2)["count"] == 1
    assert h.ok("mortgage_list_conflicts", loan_id=L2, run_id=R2)["count"] == 0
    acts = h.ok("mortgage_list_proposed_actions", loan_id=L3, run_id=R3)
    assert acts["count"] >= 1 and acts["pending"] == acts["count"]
    assert all(a["status"] == "DRAFT_HUMAN_APPROVAL_REQUIRED" and a["decisions"] == [] for a in acts["proposed_actions"])
    assert h.ok("mortgage_list_approvals_required", loan_id=L3, run_id=R3)["count"] >= 0
    for name in ("mortgage_list_documents", "mortgage_list_missing_documents", "mortgage_list_conflicts",
                 "mortgage_list_proposed_actions", "mortgage_list_approvals_required"):
        assert "not found" in h.err(name, loan_id="LN-NOPE", run_id="RUN-NOPE")


def test_review_queue(h: Harness):
    out = h.ok("mortgage_review_queue")
    assert out["count"] == len(h.repo.review_queue()) == 6
    flags = [e["blocking"] for e in out["entries"]]
    assert flags == sorted(flags, reverse=True)
    assert h.ok("mortgage_review_queue", loan_id=L2)["count"] == 1
    assert h.ok("mortgage_review_queue", reviewer_role="PROCESSOR")["count"] == len(h.repo.review_queue(reviewer_role="PROCESSOR"))
    assert h.ok("mortgage_review_queue", limit=2)["truncated"] is True
    assert h.call("mortgage_review_queue", reviewer_role="JANITOR").is_error


def test_get_report_and_cap(h: Harness, monkeypatch):
    out = h.ok("mortgage_get_report", loan_id=L1, run_id=R1)
    assert out["name"] == "report.md" and out["truncated"] is False and out["content_md"].startswith("# DEMO REPORT")
    assert out["chars"] == len(out["content_md"]) and len(out["sha256"]) == 64
    monkeypatch.setattr(srv, "REPORT_CHAR_CAP", 25)
    out = h.ok("mortgage_get_report", loan_id=L1, run_id=R1, name="report.md")
    assert out["truncated"] is True and len(out["content_md"]) == 25 and out["chars"] > 25
    assert "not found" in h.err("mortgage_get_report", loan_id=L1, run_id=R1, name="other.md")
    assert h.call("mortgage_get_report", loan_id=L1, run_id=R1, name="../secret").is_error


def test_get_report_withholds_unmasked_pii(h: Harness):
    rep = h.repo.reports[(L1, R1)][0]
    h.repo.reports[(L1, R1)][0] = rep.model_copy(update={"content_md": rep.content_md + "\nSSN 123-45-6789\n"})
    msg = h.err("mortgage_get_report", loan_id=L1, run_id=R1)
    assert "unmasked" in msg and "123-45-6789" not in msg


def test_eval_latest_absent_and_present(h: Harness):
    out = h.ok("mortgage_eval_latest")
    assert out == {"available": False, "all_targets_met": None, "targets": {}, "report_keys": []}
    from services.models import EvalReport
    h.repo.add_eval_report(EvalReport(all_targets_met=True, targets={"precision": {"target": "0.95", "actual": "1.00", "met": True}},
                                      report={"cases": 3}))
    out = h.ok("mortgage_eval_latest")
    assert out["available"] and out["all_targets_met"] is True and out["targets"]["precision"]["met"] is True
    assert out["report_keys"] == ["cases"] and "cases" not in out


# ---- write tools ------------------------------------------------------------------------------


def test_create_run_request_queues_only(h: Harness):
    assert h.ok("mortgage_list_run_requests")["count"] == 0
    out = h.ok("mortgage_create_run_request", loan_id=L2, note="please re-run after new paystub")
    req = out["request"]
    assert req["status"] == "QUEUED" and req["requested_by"] == IDENTITY and req["run_id"] is None
    assert out["executed"] is False and "/mortgage-file-audit" in out["next_step"]
    assert len(h.repo.run_requests) == 1 and h.repo.run_requests[0].loan_id == L2
    assert h.ok("mortgage_list_run_requests", status="QUEUED")["count"] == 1
    assert h.ok("mortgage_list_run_requests", status="COMPLETED")["count"] == 0
    assert h.ok("mortgage_summary")["queued_requests"] == 1
    assert "not found" in h.err("mortgage_create_run_request", loan_id="LN-NOPE")
    assert len(h.repo.run_requests) == 1


def test_record_review_decision_on_review_item_and_finding(h: Harness):
    before = h.ok("mortgage_review_queue")["count"]
    out = h.ok("mortgage_record_review_decision", loan_id=L2, run_id=R2, target_id="RV-001", decision="CONFIRMED",
               note="Jane Doe (processor) confirmed 2026-09-08")
    d = out["decision"]
    assert d["decided_by"] == IDENTITY and d["audit_type"] is None and d["decision_id"] and d["decided_at"]
    assert out["executed_externally"] is False
    assert len(h.repo.review_decisions) == 1 and h.repo.review_decisions[0].decided_by == IDENTITY
    assert h.ok("mortgage_review_queue")["count"] == before - 1
    assert h.ok("mortgage_review_queue", loan_id=L2)["count"] == 0

    entry = next(e for e in h.ok("mortgage_review_queue")["entries"] if e["kind"] == "FINDING")
    h.ok("mortgage_record_review_decision", loan_id=entry["loan_id"], run_id=entry["run_id"], audit_type=entry["audit_type"],
         target_id=entry["target_id"], decision="NEEDS_INFO")
    assert h.ok("mortgage_review_queue")["count"] == before - 2
    full = h.ok("mortgage_get_finding", loan_id=entry["loan_id"], run_id=entry["run_id"], audit_type=entry["audit_type"],
                finding_id=entry["target_id"])
    assert full["review_decisions"][0]["decision"] == "NEEDS_INFO"
    assert h.ok("mortgage_get_run", loan_id=entry["loan_id"], run_id=entry["run_id"])["decision_counts"]["review"] >= 1


def test_record_review_decision_errors(h: Harness):
    msg = h.err("mortgage_record_review_decision", loan_id=L2, run_id=R2, target_id="RV-999", decision="CONFIRMED")
    assert "RV-999" in msg and "not found" in msg and "mortgage_review_queue" in msg
    # a finding id with audit_type omitted is looked up among review items, so it is rejected
    assert "not found" in h.err("mortgage_record_review_decision", loan_id=L2, run_id=R2, target_id="F-002", decision="CONFIRMED")
    assert "not found" in h.err("mortgage_record_review_decision", loan_id=L2, run_id="RUN-NOPE", target_id="RV-001", decision="CONFIRMED")
    assert h.call("mortgage_record_review_decision", loan_id=L2, run_id=R2, target_id="RV-001", decision="MAYBE").is_error
    assert h.repo.review_decisions == []


def test_record_action_decision(h: Harness):
    acts = h.ok("mortgage_list_proposed_actions", loan_id=L3, run_id=R3)["proposed_actions"]
    a = acts[0]
    out = h.ok("mortgage_record_action_decision", loan_id=L3, run_id=R3, audit_type=a["audit_type"], action_id=a["action_id"],
               decision="ACCEPTED", note="Sam (LO) accepted 2026-09-08")
    assert out["decision"]["decided_by"] == IDENTITY and out["executed_externally"] is False
    assert len(h.repo.action_decisions) == 1
    after = h.ok("mortgage_list_proposed_actions", loan_id=L3, run_id=R3)
    assert after["pending"] == after["count"] - 1
    assert after["proposed_actions"][0]["decisions"][0]["decision"] == "ACCEPTED"
    assert h.ok("mortgage_summary")["pending_actions"] == h.repo.dashboard_summary().pending_actions == after["pending"] + 1
    msg = h.err("mortgage_record_action_decision", loan_id=L3, run_id=R3, audit_type=a["audit_type"], action_id="PA-999", decision="REJECTED")
    assert "PA-999" in msg and "not found" in msg and "mortgage_list_proposed_actions" in msg
    assert len(h.repo.action_decisions) == 1


# ---- resources and prompt ---------------------------------------------------------------------


def test_resources_resolve(h: Harness):
    uris = {str(r.uri) for r in h.resources()}
    assert {"mortgage://summary", "mortgage://loans"} <= uris
    templates = {t.uri_template for t in h.templates()}
    assert {"mortgage://runs/{loan_id}/{run_id}", "mortgage://reports/{loan_id}/{run_id}/{name}"} <= templates

    summary = json.loads(h.read("mortgage://summary"))
    assert summary["loans"] == 3
    loans = json.loads(h.read("mortgage://loans"))
    assert loans["count"] == 3
    run = json.loads(h.read(f"mortgage://runs/{L2}/{R2}"))
    assert run["overall_status"] == "NOT_READY" and run["report_names"] == ["report.md"]
    assert h.read(f"mortgage://reports/{L1}/{R1}/report.md").startswith("# DEMO REPORT")
    with pytest.raises(MCPError, match="not found"):
        h.read(f"mortgage://runs/{L2}/RUN-NOPE")
    with pytest.raises(MCPError, match="not found"):
        h.read(f"mortgage://reports/{L1}/{R1}/nope.md")


def test_prompt_renders(h: Harness):
    names = {p.name for p in h.prompts()}
    assert "mortgage_review_queue_briefing" in names
    text = h.prompt("mortgage_review_queue_briefing", reviewer_role="underwriter")
    assert 'mortgage_review_queue(reviewer_role="UNDERWRITER")' in text
    assert "Do not invent" in text and "do not call the record tools" in text
    assert "PASS, FAIL, MISSING, REVIEW, NOT_APPLICABLE" in text
    with pytest.raises(MCPError, match="reviewer_role must be one of"):
        h.prompt("mortgage_review_queue_briefing", reviewer_role="JANITOR")


# ---- construction, secrets, selftest ----------------------------------------------------------


def test_build_server_memory_backend_is_seeded(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", SENTINEL_KEY)
    monkeypatch.setenv("MPIRE_MCP_IDENTITY", "env-identity")
    server = srv.build_server(settings=Settings(backend="memory"))
    hh = Harness(InMemoryRepository(), server)
    assert hh.ok("mortgage_list_loans")["count"] == 3
    req = hh.ok("mortgage_create_run_request", loan_id=L1)["request"]
    assert req["requested_by"] == "env-identity"
    blob = json.dumps([hh.ok(n) for n in ("mortgage_summary", "mortgage_list_loans", "mortgage_eval_latest")]) + hh.instructions()
    assert SENTINEL_KEY not in blob


def test_build_server_supabase_backend_never_leaks_key(monkeypatch):
    """Construction needs no network; a failing call reports the exception class, never the key or a traceback."""
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError(f"refused apikey={SENTINEL_KEY}")

    monkeypatch.setattr(httpx.Client, "request", boom)
    monkeypatch.setenv("MPIRE_MCP_BEARER", "user-jwt")
    settings = Settings(backend="supabase", supabase_url="https://example.supabase.co", supabase_service_role_key=SENTINEL_KEY)
    server = srv.build_server(settings=settings)
    hh = Harness(InMemoryRepository(), server)
    msg = hh.err("mortgage_summary")
    assert "backend error" in msg and SENTINEL_KEY not in msg and "user-jwt" not in msg


def test_selftest_subprocess_exits_zero():
    env = {**os.environ, "MPIRE_REPO_BACKEND": "memory", "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run([sys.executable, "-m", "mcp_server", "--selftest"], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["tool_count"] == len(READ_TOOLS | WRITE_TOOLS)
    assert out["mortgage_summary"]["loans"] == 3


def test_main_rejects_bad_config(monkeypatch, capsys):
    monkeypatch.setenv("MPIRE_REPO_BACKEND", "supabase")
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(srv, "get_settings", lambda: (_ for _ in ()).throw(ValueError("MPIRE_REPO_BACKEND=supabase requires SUPABASE_URL")))
    assert srv.main([]) == 2
    assert "configuration error" in capsys.readouterr().err
