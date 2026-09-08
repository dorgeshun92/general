"""Static checks for dashboard/ — no browser needed.

The dashboard is plain HTML/CSS/JS served by the API. These tests pin the safety rules it
must keep: only allowed external hosts, no inline handlers, no innerHTML, every API route it
uses is in docs/api-contract.md, and the sample data carries no SSN-shaped or long digit runs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.common.masking import contains_unmasked_pii

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"
CONTRACT = ROOT / "docs" / "api-contract.md"

ALLOWED_HOSTS = {"cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"}

# Routes from docs/api-contract.md that app.js calls (path prefixes without /api and without
# path parameters). Keep in sync when a view gains a route.
USED_ROUTES = [
    "/health", "/summary", "/loans", "/runs/latest", "/findings/search", "/review-queue",
    "/review-decisions", "/action-decisions", "/run-requests", "/eval/latest",
]


def _read(name: str) -> str:
    return (DASH / name).read_text(encoding="utf-8")


def test_files_exist():
    for name in ("index.html", "app.js", "styles.css", "demo-data.js", "README.md"):
        assert (DASH / name).is_file(), name


def test_index_references_only_allowed_external_hosts():
    html = _read("index.html")
    hosts = set(re.findall(r"https?://([^/\"'\s]+)", html))
    assert hosts, "expected at least the font and supabase hosts"
    assert hosts <= ALLOWED_HOSTS, hosts - ALLOWED_HOSTS
    js_hosts = set(re.findall(r"https?://([^/\"'\s)]+)", _read("app.js")))
    assert js_hosts <= ALLOWED_HOSTS, js_hosts - ALLOWED_HOSTS
    assert "cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js" in html


def test_no_inline_event_handlers_or_inline_scripts_in_html():
    html = _read("index.html")
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE), "inline event handler attribute"
    assert not re.search(r"javascript:", html, re.IGNORECASE)
    # every <script> is src-only; nothing executes data inline
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.DOTALL | re.IGNORECASE):
        assert "src=" in m.group(1) and not m.group(2).strip(), "inline script body"


def test_app_js_never_uses_innerhtml_or_eval():
    js = _read("app.js")
    body = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)          # block comments
    body = re.sub(r"^\s*//.*$", "", body, flags=re.MULTILINE)      # line comments
    body = body.replace('else if (k === "html") throw new Error("innerHTML is not allowed");', "")
    assert "innerHTML" not in body
    assert "outerHTML" not in body
    assert "insertAdjacentHTML" not in body
    assert "document.write" not in body
    assert not re.search(r"\beval\(", body)
    assert "new Function" not in body


def test_used_routes_are_documented_and_present_in_app_js():
    contract = CONTRACT.read_text(encoding="utf-8")
    js = _read("app.js")
    documented = set(re.findall(r"`/api(/[^`?\s]*)", contract))
    for route in USED_ROUTES:
        assert any(d == route or d.startswith(route) for d in documented), f"{route} not in api-contract.md"
        assert f'"{route}"' in js or f'"{route}/' in js or f"'{route}" in js, f"{route} not used in app.js"
    # parameterised routes the run view builds
    for fragment in ('"/runs/" + encodeURIComponent', '"/findings"', '"/documents"', '"/reports"', '"/run-requests/" + encodeURIComponent'):
        assert fragment in js, fragment
    assert "Authorization" in js and "Bearer " in js
    assert "/config.js" in _read("index.html")


def test_demo_data_parses_and_is_read_only_shape():
    node = shutil.which("node") or "/opt/node22/bin/node"
    if not Path(node).exists():
        pytest.skip("node not available")
    script = (
        "global.window={};require(process.argv[1]);const d=window.MPIRE_DEMO_DATA;"
        "if(!d||!Array.isArray(d.runs)||d.runs.length!==3)throw new Error('runs');"
        "const s=d.summary();if(s.loans!==3)throw new Error('summary');"
        "for(const r of d.runs){if(!r.bundle.loan.loan_id.startsWith('LN-EXAMPLE-'))throw new Error('synthetic id');}"
        "console.log('ok')"
    )
    out = subprocess.run([node, "-e", script, str(DASH / "demo-data.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_demo_data_has_no_unmasked_pii_patterns():
    text = _read("demo-data.js")
    # sha256 digests are the only allowed long hex runs; strip them before scanning.
    scrubbed = re.sub(r"\b[0-9a-f]{64}\b", "", text)
    assert contains_unmasked_pii(scrubbed) == []
    assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b", scrubbed)
    assert re.search(r"\d{8,}", re.sub(r"\b[0-9a-f]{64}\b", "", text)) is None


def test_required_copy_present():
    html = _read("index.html")
    assert "DECISION SUPPORT ONLY" in html
    assert "Derived, masked data. No source documents are stored or shown." in html
    assert "PREVIEW" in html and "not connected" in html
