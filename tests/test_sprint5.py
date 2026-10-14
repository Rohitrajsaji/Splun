"""Sprint 5 Verification Tests.

Covers:
1. "Warehouse 72" 80-Scenario Benchmark Seeder (excel, BRD, AST symbols, bi-temporal decisions, git diff).
2. Autonomous Project Coordinator (triangulation, clustering into batches, escalations queue).
3. Worktree execution packet compilation, test command execution, TEST_LOG evidence, and verification promotion gate.
4. FastAPI endpoints (/api/projects/demo/warehouse72, /api/projects/{id}/coordinator/plan).
5. MCP Server tool consultant_coordinator_plan.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from cli.mcp_server import handle_jsonrpc_request
from core.engine.consultant import TechnicalConsultant
from core.engine.coordinator import AutonomousCoordinator
from core.ingestion.demo_seeder import seed_warehouse72_demo
from core.storage.db import ProjectMemoryDB


@pytest.fixture
def db():
    database = ProjectMemoryDB(":memory:")
    yield database
    database.close()


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_sprint5.db")
    app = create_app(db_file)
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 1. Benchmark Seeder Verification
# --------------------------------------------------------------------------- #
def test_seed_warehouse72_demo(db, tmp_path):
    ws_dir = str(tmp_path / "warehouse72_ws")
    res = seed_warehouse72_demo(db, project_id="warehouse-72", workspace_dir=ws_dir)

    # 1. Scenarios count
    assert res["scenarios_seeded"] == 80
    scenarios = db.get_scenarios("warehouse-72")
    assert len(scenarios) == 80

    # 2. BRD requirements
    assert res["requirements_seeded"] >= 8
    req1 = db.get_requirement("warehouse-72", "REQ-001")
    assert req1 is not None
    assert req1["constraint_level"] == "mandatory"
    assert "tax_id" in req1["fields"]

    req4 = db.get_requirement("warehouse-72", "REQ-004")
    assert req4 is not None
    assert req4["constraint_level"] == "optional"

    # 3. Code AST scan
    assert res["symbols_scanned"] > 0
    symbols = db.find_code_symbols("warehouse-72", "tax_id")
    assert len(symbols) > 0
    tax_sym = symbols[0]
    assert tax_sym["details"].get("indicates") == "nullable"

    # 4. Bi-temporal superseded decision on REQ-003
    decs = db.get_decisions("warehouse-72", subject_entity="requirement:REQ-003", active_only=False)
    assert len(decs) >= 2
    superseded = [d for d in decs if d.get("superseded_by")]
    assert len(superseded) >= 1
    assert superseded[0]["valid_to"] is not None

    active_decs = db.get_decisions("warehouse-72", subject_entity="requirement:REQ-003", active_only=True)
    assert len(active_decs) == 1
    assert "latency-based" in active_decs[0]["decision_text"]

    # 5. Scenario statuses: SC-001..SC-003 verified, SC-004..SC-005 Impl_Reported, SC-006 blocked
    sc1 = db.get_scenario("warehouse-72", 1)
    sc2 = db.get_scenario("warehouse-72", 2)
    sc3 = db.get_scenario("warehouse-72", 3)
    assert sc1["status"] == "Verified"
    assert sc2["status"] == "Verified"
    assert sc3["status"] == "Verified"
    assert sc1["verification_evidence_id"] is not None

    sc4 = db.get_scenario("warehouse-72", 4)
    sc5 = db.get_scenario("warehouse-72", 5)
    assert sc4["status"] == "Impl_Reported"
    assert sc5["status"] == "Impl_Reported"

    sc6 = db.get_scenario("warehouse-72", 6)
    assert sc6["status"] == "Blocked"
    assert "NSI Tax Service" in sc6["blockers"]

    # 6. Kickoff thread exists with messages
    threads = db.list_threads("warehouse-72")
    assert len(threads) >= 1
    msgs = db.get_thread_messages("warehouse-72", threads[0]["thread_id"])
    assert len(msgs) >= 2

    # 7. Live triangulation discrepancy on REQ-001
    consultant = TechnicalConsultant(db, "warehouse-72")
    tri = consultant.triangulate_scenario("warehouse-72:SC-004")
    assert tri["discrepancy_detected"] is True
    assert len(tri["conflicts"]) > 0
    assert tri["conflicts"][0]["code_indicates"] == "nullable"
    assert tri["conflicts"][0]["brd_constraint"] == "mandatory"


# --------------------------------------------------------------------------- #
# 2. Autonomous Coordinator: Execution Plan & Clustering
# --------------------------------------------------------------------------- #
def test_coordinator_build_execution_plan(db, tmp_path):
    ws_dir = str(tmp_path / "warehouse72_ws")
    seed_warehouse72_demo(db, project_id="warehouse-72", workspace_dir=ws_dir)

    coordinator = AutonomousCoordinator(db, "warehouse-72")
    plan = coordinator.build_execution_plan()

    assert plan["total_scenarios"] == 80
    assert plan["completed_count"] >= 3
    assert plan["escalated_count"] > 0
    assert plan["ready_batches_count"] > 0

    # 1. Completed scenarios match SC-001..SC-003
    completed_nums = {c["scenario_num"] for c in plan["completed"]}
    assert {1, 2, 3}.issubset(completed_nums)

    # 2. Escalations isolate blockers and triangulation conflicts
    escalated_nums = {e["scenario_num"] for e in plan["escalations"]}
    # SC-006 is blocked
    assert 6 in escalated_nums
    esc_6 = next(e for e in plan["escalations"] if e["scenario_num"] == 6)
    assert esc_6["status"] == "Blocked"
    assert "stakeholder_question" in esc_6

    # SC-004 & SC-005 have REQ-001 contradiction (mandatory vs nullable)
    assert 4 in escalated_nums
    assert 5 in escalated_nums
    esc_4 = next(e for e in plan["escalations"] if e["scenario_num"] == 4)
    assert esc_4["status"] == "Contradiction_Detected"
    assert "tax_id" in esc_4["reason"]
    assert "stakeholder_question" in esc_4

    # 3. Execution Batches group ready scenarios by shared root cause
    for batch in plan["batches"]:
        assert "batch_id" in batch
        assert "title" in batch
        assert "root_cause_type" in batch
        assert batch["scenario_count"] == len(batch["scenarios"])
        assert "recommended_action" in batch

        # Make sure no escalated scenario leaked into an execution batch
        for sc in batch["scenarios"]:
            assert sc["scenario_num"] not in escalated_nums
            assert sc["scenario_num"] not in completed_nums


# --------------------------------------------------------------------------- #
# 3. Autonomous Coordinator: Worktree Execution & Verification Gate
# --------------------------------------------------------------------------- #
def test_coordinator_execute_batch_in_worktree(db, tmp_path):
    db.create_project("proj-worktree", "Worktree Test", repo_path=str(tmp_path))
    db.upsert_scenario("proj-worktree", {"scenario_num": 10, "title": "Worker Queue", "status": "Ready_For_Impl"})
    db.upsert_scenario("proj-worktree", {"scenario_num": 11, "title": "Retry Mechanism", "status": "Ready_For_Impl"})

    coordinator = AutonomousCoordinator(db, "proj-worktree")
    worktree_target = tmp_path / "batch_wt_01"

    # Test 1: Generate self-contained execution packet without test execution
    packet_res = coordinator.execute_batch_in_worktree(
        batch_id="BATCH-01",
        scenario_ids=["SC-010", "SC-011"],
        worktree_dir=str(worktree_target),
    )
    assert packet_res["success"] is True
    assert packet_res["promoted_to_verified"] is False
    assert (worktree_target / "HANDOFF_SPEC.md").is_file()
    assert (worktree_target / ".ai-memory" / "PROJECT_STATE.md").is_file()
    assert (worktree_target / ".ai-memory" / "SKILL.md").is_file()

    # Test 2: Run a failing test command -> exit code != 0 -> remains unverified
    fail_res = coordinator.execute_batch_in_worktree(
        batch_id="BATCH-01",
        scenario_ids=["SC-010", "SC-011"],
        worktree_dir=str(worktree_target),
        test_command=[sys.executable, "-c", "import sys; sys.exit(42)"],
    )
    assert fail_res["success"] is False
    assert fail_res["promoted_to_verified"] is False
    assert fail_res["exit_code"] == 42
    # Verify scenarios did not promote to Verified
    sc10 = db.get_scenario("proj-worktree", 10)
    assert sc10["status"] == "Impl_Reported"

    # Test 3: Run a passing test command -> exit code 0 -> promoted to Verified backed by TEST_LOG
    pass_res = coordinator.execute_batch_in_worktree(
        batch_id="BATCH-01",
        scenario_ids=["SC-010", "SC-011"],
        worktree_dir=str(worktree_target),
        test_command=[sys.executable, "-c", "print('ALL TESTS PASSED'); import sys; sys.exit(0)"],
    )
    assert pass_res["success"] is True
    assert pass_res["promoted_to_verified"] is True
    assert pass_res["exit_code"] == 0
    assert "TEST-LOG-BATCH-01" in pass_res["evidence_id"]

    # Scenarios must now be Verified with verification_evidence_id set
    sc10_after = db.get_scenario("proj-worktree", 10)
    sc11_after = db.get_scenario("proj-worktree", 11)
    assert sc10_after["status"] == "Verified"
    assert sc11_after["status"] == "Verified"
    assert sc10_after["verification_evidence_id"] == pass_res["evidence_id"]

    # Evidence exists in evidence_vault with source_type='TEST_LOG'
    ev = db.get_evidence("proj-worktree", pass_res["evidence_id"])
    assert ev is not None
    assert ev["source_type"] == "TEST_LOG"
    assert "ALL TESTS PASSED" in ev["content"]


# --------------------------------------------------------------------------- #
# 4. FastAPI Endpoints (/demo/warehouse72 & /coordinator/plan)
# --------------------------------------------------------------------------- #
def test_fastapi_coordinator_and_demo_endpoints(client):
    # 1. POST /api/projects/demo/warehouse72
    demo_res = client.post("/api/projects/demo/warehouse72")
    assert demo_res.status_code == 200
    demo_data = demo_res.json()
    assert demo_data["project_id"] == "warehouse-72"
    assert demo_data["scenarios_seeded"] == 80
    assert "sidecar" in demo_data
    assert demo_data["sidecar"]["status_report"]["total_scenarios"] == 80

    # 2. GET /api/projects/warehouse-72/coordinator/plan
    plan_res = client.get("/api/projects/warehouse-72/coordinator/plan")
    assert plan_res.status_code == 200
    plan_data = plan_res.json()
    assert plan_data["project_id"] == "warehouse-72"
    assert plan_data["total_scenarios"] == 80
    assert plan_data["ready_batches_count"] > 0
    assert len(plan_data["batches"]) == plan_data["ready_batches_count"]
    assert len(plan_data["escalations"]) == plan_data["escalated_count"]

    # 3. GET /api/projects/{non_existent}/coordinator/plan -> 404
    err_res = client.get("/api/projects/non-existent-proj/coordinator/plan")
    assert err_res.status_code == 404


# --------------------------------------------------------------------------- #
# 5. MCP Server Tool: consultant_coordinator_plan
# --------------------------------------------------------------------------- #
def test_mcp_server_coordinator_plan(db, tmp_path):
    ws_dir = str(tmp_path / "warehouse72_ws")
    seed_warehouse72_demo(db, project_id="warehouse-72", workspace_dir=ws_dir)

    # 1. Tool listed in tools/list
    list_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    list_res = handle_jsonrpc_request(db, list_req)
    tools = [t["name"] for t in list_res["result"]["tools"]]
    assert "consultant_coordinator_plan" in tools

    # 2. Call consultant_coordinator_plan
    call_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "consultant_coordinator_plan",
            "arguments": {"project_id": "warehouse-72"},
        },
    }
    call_res = handle_jsonrpc_request(db, call_req)
    assert "result" in call_res
    text_content = call_res["result"]["content"][0]["text"]
    plan_obj = json.loads(text_content)

    assert plan_obj["project_id"] == "warehouse-72"
    assert plan_obj["total_scenarios"] == 80
    assert len(plan_obj["batches"]) > 0
    assert len(plan_obj["escalations"]) > 0
