"""Sprint 4 verification: Git-diff auto-verification, portable .ai-memory/ compiler,
zero-dependency MCP stdio server, and FastAPI verification/export endpoints."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from cli.mcp_server import handle_jsonrpc_request
from core.engine.exporter import export_portable_memory_bundle
from core.ingestion.brd_parser import ingest_brd_markdown
from core.ingestion.git_verifier import extract_changed_files, verify_scenarios_from_git
from core.storage.db import ProjectMemoryDB

SAMPLE_DIFF = textwrap.dedent(
    """\
    commit a1b2c3d4e5f6
    Author: Dev <dev@example.com>
    Date:   Wed Oct 14 00:00:00 2026 +0000

        feat: implement dual approval workflow for SC-002 and SC-003

    diff --git a/payments/approval.py b/payments/approval.py
    index 1234567..89abcdef 100644
    --- a/payments/approval.py
    +++ b/payments/approval.py
    @@ -10,3 +10,7 @@ def process_payment(amount):
    +    if amount > 10000:
    +        return require_dual_approvers(amount)
         return single_approval(amount)
    diff --git a/tests/test_approval.py b/tests/test_approval.py
    new file mode 100644
    --- /dev/null
    +++ b/tests/test_approval.py
    @@ -0,0 +1,5 @@
    +def test_dual_approval():
    +    assert True
    """
)


@pytest.fixture
def db():
    database = ProjectMemoryDB(":memory:")
    database.create_project("proj-x", "Core Platform", "/repos/core")
    database.upsert_scenario("proj-x", {"scenario_num": 1, "title": "Customer Login", "status": "Impl_Reported"})
    database.upsert_scenario("proj-x", {"scenario_num": 2, "title": "Dual Approval", "status": "Ready_For_Impl"})
    database.upsert_scenario("proj-x", {"scenario_num": 3, "title": "High Value Limits", "status": "Blocked", "blockers": "API pending"})
    yield database
    database.close()


@pytest.fixture
def git_repo(tmp_path):
    """Sets up a real local git repository for testing commit show verification."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Dev"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=str(repo_dir), check=True)

    app_file = repo_dir / "auth.py"
    app_file.write_text("def login(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "auth.py"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-m", "fix: finalize login implementation for SC-001"],
        cwd=str(repo_dir),
        check=True,
    )
    return repo_dir


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_sprint4.db")
    app = create_app(db_file)
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 1. Git-Diff Verification
# --------------------------------------------------------------------------- #
def test_verify_scenarios_from_raw_diff(db):
    res = verify_scenarios_from_git(
        db=db,
        project_id="proj-x",
        diff_text=SAMPLE_DIFF,
        commit_sha="a1b2c3d4",
    )

    assert len(res["verified_scenarios"]) == 2
    assert "proj-x:SC-002" in res["verified_scenarios"]
    assert "proj-x:SC-003" in res["verified_scenarios"]
    assert res["evidence_id"].startswith("DIFF-a1b2c3d4-")
    assert "payments/approval.py" in res["changed_files"]
    assert "tests/test_approval.py" in res["changed_files"]

    s2 = db.get_scenario("proj-x", 2)
    s3 = db.get_scenario("proj-x", 3)
    assert s2["status"] == "Verified"
    assert s2["verification_evidence_id"] == res["evidence_id"]
    assert s3["status"] == "Verified"
    assert s3["blockers"] is None  # Blocker cleared on verification proof

    # Evidence stored verbatim
    ev = db.get_evidence("proj-x", res["evidence_id"])
    assert ev["source_type"] == "GIT_DIFF"
    assert ev["source_reference"] == "git:a1b2c3d4"
    assert "require_dual_approvers" in ev["content"]


def test_verify_scenarios_from_real_git_repo(db, git_repo):
    # Verify SC-001 from git repo commit
    res = verify_scenarios_from_git(
        db=db,
        project_id="proj-x",
        repo_path=str(git_repo),
        commit_sha="HEAD",
    )

    assert "proj-x:SC-001" in res["verified_scenarios"]
    s1 = db.get_scenario("proj-x", 1)
    assert s1["status"] == "Verified"
    assert s1["verification_evidence_id"] == res["evidence_id"]
    assert "auth.py" in res["changed_files"]


def test_verify_scenarios_with_explicit_ids(db):
    # Pass diff that mentions nothing, but supply explicit scenario_ids
    minimal_diff = "diff --git a/x.py b/x.py\n+ # some fix"
    res = verify_scenarios_from_git(
        db=db,
        project_id="proj-x",
        diff_text=minimal_diff,
        commit_sha="c0ffee",
        scenario_ids=["SC-001"],
    )
    assert res["verified_scenarios"] == ["proj-x:SC-001"]
    assert db.get_scenario("proj-x", 1)["status"] == "Verified"


# --------------------------------------------------------------------------- #
# 2. Portable .ai-memory/ Compiler
# --------------------------------------------------------------------------- #
def test_export_portable_memory_bundle(db, tmp_path):
    # Add BRD requirement and decision
    ingest_brd_markdown(
        db,
        "proj-x",
        "BRD.md",
        content="### REQ-001: Dual Approval\nPayments above $10k MUST require two approvers.\n- Constraint: Mandatory\n",
    )

    out = export_portable_memory_bundle(db, "proj-x", tmp_path)
    bundle_dir = Path(out["output_dir"])
    assert bundle_dir.name == ".ai-memory"
    assert bundle_dir.is_dir()

    # 1. PROJECT_STATE.md
    p_state = (bundle_dir / "PROJECT_STATE.md").read_text(encoding="utf-8")
    assert "# Project State: Core Platform" in p_state
    assert "- **Total Scenarios:** 3" in p_state
    assert "Customer Login" in p_state
    assert "High Value Limits" in p_state

    # 2. DECISION_LOG.md
    d_log = (bundle_dir / "DECISION_LOG.md").read_text(encoding="utf-8")
    assert "# Epistemic Decision Log: Core Platform" in d_log
    assert "Documented Facts" in d_log
    assert "Payments above $10k MUST require two approvers." in d_log
    assert "BRD.md" in d_log

    # 3. SKILL.md
    skill = (bundle_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "name: proj-x-consultant-guardrails" in skill
    assert "Code Beats Model" in skill
    assert "Controlled Authority Gate" in skill
    assert "Standing Instructions: Core Platform Agent Guardrails" in skill


# --------------------------------------------------------------------------- #
# 3. Zero-Dependency MCP Server (JSON-RPC 2.0)
# --------------------------------------------------------------------------- #
def test_mcp_server_initialize_and_tools_list(db):
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    init_res = handle_jsonrpc_request(db, init_req)
    assert init_res["result"]["serverInfo"]["name"] == "ai-project-consultant-mcp"
    assert "tools" in init_res["result"]["capabilities"]

    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    list_res = handle_jsonrpc_request(db, list_req)
    tool_names = [t["name"] for t in list_res["result"]["tools"]]
    assert "consultant_status" in tool_names
    assert "consultant_next_scenarios" in tool_names
    assert "consultant_agenda" in tool_names
    assert "consultant_triangulate" in tool_names
    assert "consultant_handoff" in tool_names
    assert "consultant_chat_update" in tool_names
    assert "consultant_verify_git" in tool_names


def test_mcp_server_tools_call(db):
    # Call consultant_status
    status_req = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "consultant_status", "arguments": {"project_id": "proj-x"}},
    }
    status_res = handle_jsonrpc_request(db, status_req)
    content = json.loads(status_res["result"]["content"][0]["text"])
    assert content["total_scenarios"] == 3
    assert content["project_id"] == "proj-x"

    # Call consultant_verify_git
    verify_req = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {
            "name": "consultant_verify_git",
            "arguments": {
                "project_id": "proj-x",
                "diff_text": SAMPLE_DIFF,
                "commit_sha": "f00ba7",
            },
        },
    }
    verify_res = handle_jsonrpc_request(db, verify_req)
    v_data = json.loads(verify_res["result"]["content"][0]["text"])
    assert "proj-x:SC-002" in v_data["verified_scenarios"]
    assert db.get_scenario("proj-x", 2)["status"] == "Verified"

    # Error on unknown tool
    err_req = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "non_existent_tool", "arguments": {}},
    }
    err_res = handle_jsonrpc_request(db, err_req)
    assert err_res["error"]["code"] == -32601


# --------------------------------------------------------------------------- #
# 4. FastAPI Endpoints (/verify/git & /export)
# --------------------------------------------------------------------------- #
def test_fastapi_verify_git_and_export_endpoints(client, tmp_path):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments"})
    with client.app.state.db.transaction():
        client.app.state.db.upsert_scenario("pay", {"scenario_num": 4, "title": "Audit Logs", "status": "Ready_For_Impl"})

    # 1. Test POST /api/projects/{project_id}/verify/git
    v_res = client.post(
        "/api/projects/pay/verify/git",
        json={"diff_text": "diff --git a/audit.py b/audit.py\n+ log SC-004", "commit_sha": "d1ff99"},
    )
    assert v_res.status_code == 200
    v_body = v_res.json()
    assert "pay:SC-004" in v_body["verified_scenarios"]
    assert v_body["sidecar"]["status_report"]["by_status"]["Verified"] == 1

    # 2. Test POST /api/projects/{project_id}/export
    exp_dir = str(tmp_path / "custom_export")
    e_res = client.post(
        "/api/projects/pay/export",
        json={"output_dir": exp_dir},
    )
    assert e_res.status_code == 200
    e_body = e_res.json()
    assert Path(e_body["files"]["PROJECT_STATE.md"]).is_file()
    assert Path(e_body["files"]["DECISION_LOG.md"]).is_file()
    assert Path(e_body["files"]["SKILL.md"]).is_file()
