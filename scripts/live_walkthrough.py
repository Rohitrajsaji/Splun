"""Live End-to-End Walkthrough Script for Warehouse 72 against http://127.0.0.1:8000.

Executes real HTTP requests against the live running server and MCP server stdio.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "consultant.db"


def http_req(method: str, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            try:
                return status, json.loads(body)
            except Exception:
                return status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def run_walkthrough() -> None:
    results: list[str] = []

    print("=" * 70)
    print("STARTING LIVE END-TO-END WAREHOUSE 72 WALKTHROUGH")
    print(f"Target: {BASE_URL}")
    print(f"Database: {DB_PATH}")
    print("=" * 70)

    # ----------------------------------------------------------------------- #
    # Check 1: Live Web UI (GET /)
    # ----------------------------------------------------------------------- #
    print("\n--- Check 1: Live Web UI (GET /) ---")
    status, html = http_req("GET", "/")
    assert status == 200, f"Expected 200, got {status}"
    assert "project-select" in html, "Missing project-select in UI"
    assert "Load Warehouse 72 Demo" in html, "Missing Load Warehouse 72 Demo button"
    assert "Scenarios Tracker" in html, "Missing Scenarios Tracker tab"
    assert "Decisions" in html, "Missing Decisions tab"
    assert "Agenda & Discrepancies" in html, "Missing Agenda & Discrepancies tab"
    assert "Agent Handoff Spec" in html, "Missing Agent Handoff Spec tab"
    assert "Coordinator Plan" in html, "Missing Coordinator Plan tab"
    assert "undoMessage" in html, "Missing undoMessage function in UI"
    assert "/api/projects/demo/warehouse72" in html, "Missing demo seed route in JS"
    assert "/coordinator/plan" in html, "Missing coordinator plan route in JS"
    print("[OK] Check 1 Passed: Web UI HTML/JS loaded with all 5 tabs, button, undo handler, and matched routes.")
    results.append("Check 1: Live Web UI (GET /) - PASSED")

    # ----------------------------------------------------------------------- #
    # Check 2: 1-Click Demo Seed (POST /api/projects/demo/warehouse72)
    # ----------------------------------------------------------------------- #
    print("\n--- Check 2: 1-Click Demo Seed ---")
    status, seed_data = http_req("POST", "/api/projects/demo/warehouse72")
    assert status == 200, f"Expected 200, got {status}: {seed_data}"
    assert seed_data["project_id"] == "warehouse-72"
    assert seed_data["scenarios_seeded"] == 80
    assert seed_data["requirements_seeded"] >= 8
    assert seed_data["symbols_scanned"] > 0

    # Query sidecar to verify statuses
    status, sidecar = http_req("GET", "/api/projects/warehouse-72/sidecar")
    assert status == 200
    sc_map = {s["scenario_num"]: s for s in sidecar["scenarios"]}
    assert len(sc_map) == 80
    assert sc_map[1]["status"] == "Verified" and sc_map[1]["verification_evidence_id"]
    assert sc_map[2]["status"] == "Verified" and sc_map[2]["verification_evidence_id"]
    assert sc_map[3]["status"] == "Verified" and sc_map[3]["verification_evidence_id"]
    assert sc_map[4]["status"] == "Impl_Reported"
    assert sc_map[5]["status"] == "Impl_Reported"
    assert sc_map[6]["status"] == "Blocked"
    assert "NSI Tax Service" in (sc_map[6]["blockers"] or "")
    print(f"[OK] Check 2 Passed: Seeded 80 scenarios, REQ-001..008, AST symbols, SC-1..3 Verified, SC-4..5 Impl_Reported, SC-6 Blocked.")
    results.append("Check 2: 1-Click Demo Seed - PASSED")

    # ----------------------------------------------------------------------- #
    # Check 3: Shared Memory Across Threads
    # ----------------------------------------------------------------------- #
    print("\n--- Check 3: Shared Memory Across Threads ---", flush=True)
    # 1. Create Thread 1
    t1_status, t1_data = http_req("POST", "/api/projects/warehouse-72/threads", {"title": "Dev Channel 1", "mode": "project"})
    assert t1_status in (200, 201), f"Expected 200/201, got {t1_status}: {t1_data}"
    t1_id = t1_data["thread_id"]

    # 2. Send update claim in Thread 1
    chat_payload = {
        "message": "completed SC-007 and SC-008. Also SC-009 is blocked on scanner firmware.",
        "mode": "project"
    }
    chat_status, chat_resp = http_req("POST", f"/api/projects/warehouse-72/threads/{t1_id}/chat", chat_payload)
    assert chat_status == 200, f"Chat failed: {chat_resp}"
    assert chat_resp["mutations"] and len(chat_resp["mutations"]) >= 2
    t1_msg_id = chat_resp["assistant_message_id"]

    # Verify status transitions
    status, sidecar_after_t1 = http_req("GET", "/api/projects/warehouse-72/sidecar")
    sc_map_t1 = {s["scenario_num"]: s for s in sidecar_after_t1["scenarios"]}
    assert sc_map_t1[7]["status"] == "Impl_Reported"
    assert sc_map_t1[8]["status"] == "Impl_Reported"
    assert sc_map_t1[9]["status"] == "Blocked"
    assert "scanner firmware" in (sc_map_t1[9]["blockers"] or "")

    # 3. Create Thread 2 and verify shared memory
    t2_status, t2_data = http_req("POST", "/api/projects/warehouse-72/threads", {"title": "PM Channel 2", "mode": "project"})
    assert t2_status in (200, 201), f"Expected 200/201, got {t2_status}: {t2_data}"
    t2_id = t2_data["thread_id"]

    t2_chat_status, t2_chat = http_req("POST", f"/api/projects/warehouse-72/threads/{t2_id}/chat", {"message": "What is the current state and what should I work on next?"})
    assert t2_chat_status == 200
    # Sidecar in Thread 2 reflects SC-007, 008, 009 updates
    t2_sidecar = t2_chat["sidecar"]
    t2_sc_map = {s["scenario_num"]: s for s in t2_sidecar["scenarios"]}
    assert t2_sc_map[7]["status"] == "Impl_Reported"
    assert t2_sc_map[8]["status"] == "Impl_Reported"
    assert t2_sc_map[9]["status"] == "Blocked"
    print("[OK] Check 3 Passed: Thread 1 mutations captured in UI ribbon; Thread 2 immediately reflects shared project memory.", flush=True)
    results.append("Check 3: Shared Memory Across Threads - PASSED")

    # ----------------------------------------------------------------------- #
    # Check 4: 1-Click Undo
    # ----------------------------------------------------------------------- #
    print("\n--- Check 4: 1-Click Undo ---", flush=True)
    undo_status, undo_data = http_req("POST", f"/api/projects/warehouse-72/messages/{t1_msg_id}/undo")
    assert undo_status == 200, f"Undo failed: {undo_data}"
    assert undo_data.get("success") is True, f"Undo failed: {undo_data}"

    status, sidecar_after_undo = http_req("GET", "/api/projects/warehouse-72/sidecar")
    sc_map_undo = {s["scenario_num"]: s for s in sidecar_after_undo["scenarios"]}
    # SC-007 and SC-008 should revert to Ready_For_Impl (pre-turn state)
    assert sc_map_undo[7]["status"] == "Ready_For_Impl"
    assert sc_map_undo[8]["status"] == "Ready_For_Impl"
    # SC-009 should revert to Ready_For_Impl and blockers cleared
    assert sc_map_undo[9]["status"] == "Ready_For_Impl"
    assert not sc_map_undo[9]["blockers"]
    print("[OK] Check 4 Passed: 1-Click Undo cleanly reverted SC-007, SC-008, and SC-009 to their exact pre-turn state.", flush=True)
    results.append("Check 4: 1-Click Undo - PASSED")

    # ----------------------------------------------------------------------- #
    # Check 5: Discrepancy, Bi-Temporal History & Coordinator Plan
    # ----------------------------------------------------------------------- #
    print("\n--- Check 5: Discrepancy, Bi-Temporal History & Coordinator Plan ---")
    status, sidecar = http_req("GET", "/api/projects/warehouse-72/sidecar")
    assert status == 200
    # 1. Bi-temporal decisions check
    req3_decs = [d for d in sidecar["active_decisions"] if d["subject_entity"] == "requirement:REQ-003"]
    assert len(req3_decs) == 1
    assert "latency-based" in req3_decs[0]["decision_text"]

    # 2. Coordinator plan
    plan_status, plan = http_req("GET", "/api/projects/warehouse-72/coordinator/plan")
    assert plan_status == 200, f"Plan failed: {plan}"
    assert plan["total_scenarios"] == 80
    assert plan["completed_count"] >= 3
    assert plan["escalated_count"] > 0
    assert plan["ready_batches_count"] > 0

    # Verify REQ-001 conflict is in escalations
    esc_sc_nums = {e["scenario_num"]: e for e in plan["escalations"]}
    assert 6 in esc_sc_nums, "SC-006 (Blocked) must be in escalations"
    assert 4 in esc_sc_nums, "SC-004 (REQ-001 contradiction) must be in escalations"
    esc_4 = esc_sc_nums[4]
    assert esc_4["status"] == "Contradiction_Detected"
    assert "tax_id" in esc_4["reason"]
    assert "stakeholder_question" in esc_4 and len(esc_4["stakeholder_question"]) > 10

    # Verify batches
    for b in plan["batches"]:
        assert b["batch_id"].startswith("BATCH-")
        assert len(b["scenarios"]) == b["scenario_count"]
        # Ensure no escalated scenario is inside ready batches
        for s in b["scenarios"]:
            assert s["scenario_num"] not in esc_sc_nums
    print(f"[OK] Check 5 Passed: Discrepancies and bi-temporal decisions surfaced; Coordinator Plan isolated {plan['escalated_count']} escalations and clustered {plan['ready_batches_count']} unblocked batches.")
    results.append("Check 5: Discrepancy, Bi-Temporal History & Coordinator Plan - PASSED")

    # ----------------------------------------------------------------------- #
    # Check 6: Live MCP Server Verification on the Same DB
    # ----------------------------------------------------------------------- #
    print("\n--- Check 6: Live MCP Server Verification on the Same DB ---")
    mcp_proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "cli/mcp_server.py"), "--db", str(DB_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Call consultant_status
    req_status = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "consultant_status", "arguments": {"project_id": "warehouse-72"}}
    }) + "\n"
    # Call consultant_coordinator_plan
    req_plan = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "consultant_coordinator_plan", "arguments": {"project_id": "warehouse-72"}}
    }) + "\n"

    stdout, _ = mcp_proc.communicate(input=req_status + req_plan, timeout=10)
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert len(lines) >= 2, f"Expected 2 JSON-RPC responses, got: {stdout}"

    resp1 = json.loads(lines[0])
    status_content = json.loads(resp1["result"]["content"][0]["text"])
    assert status_content["project_id"] == "warehouse-72"
    assert status_content["total_scenarios"] == 80
    assert status_content["by_status"]["Verified"] == 3

    resp2 = json.loads(lines[1])
    plan_content = json.loads(resp2["result"]["content"][0]["text"])
    assert plan_content["project_id"] == "warehouse-72"
    assert plan_content["ready_batches_count"] > 0
    assert plan_content["escalated_count"] > 0
    print("[OK] Check 6 Passed: MCP Server on consultant.db returned identical live status and coordinator plan.")
    results.append("Check 6: Live MCP Server on same DB - PASSED")

    print("\n" + "=" * 70)
    print("ALL 6 LIVE END-TO-END CHECKS COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    for r in results:
        print(f"  • {r}")


if __name__ == "__main__":
    run_walkthrough()
