"""Sprint 3 verification: Multi-thread conversational memory, undoable memory ribbon,
FastAPI REST backend, live sidecar state, and 3-column web app integration."""

from __future__ import annotations

import io
import textwrap

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.app import create_app
from core.storage.db import ProjectMemoryDB

SAMPLE_BRD = textwrap.dedent(
    """\
    # Sprint 3 Test BRD

    ### REQ-001: Email Mandatory
    The system MUST capture customer email at registration.
    - Constraint: Mandatory
    - Field: `email`
    - Priority: High

    ### REQ-002: Phone Optional
    Customer telephone number is optional.
    - Constraint: Optional
    - Field: `phone`
    """
)

SAMPLE_CODE = textwrap.dedent(
    """\
    class Customer(Base):
        __tablename__ = "customers"
        email = Column(String, nullable=True)
        phone = Column(String, nullable=True)
    """
)


def _create_sample_excel_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Scenarios"
    ws.append(["Scenario #", "Title", "Description", "Status", "Blockers", "Linked Requirement"])
    ws.append([1, "Register Customer Email", "Email registration flow", "Unstarted", None, "REQ-001"])
    ws.append([2, "Capture Phone Number", "Phone capture flow", "Unstarted", None, "REQ-002"])
    ws.append([3, "Export Audit Log", "CSV export of logs", "Ready_For_Impl", None, None])
    ws.append([4, "Process Refund", "Stripe refund endpoint", "Unstarted", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_sprint3.db")
    app = create_app(db_file)
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 1. Project Management & Isolation
# --------------------------------------------------------------------------- #
def test_create_and_isolate_projects(client):
    # Create Project Alpha
    r1 = client.post("/api/projects", json={"project_id": "alpha", "name": "Alpha Platform"})
    assert r1.status_code == 201
    assert r1.json()["project_id"] == "alpha"

    # Create Project Beta
    r2 = client.post("/api/projects", json={"project_id": "beta", "name": "Beta Core"})
    assert r2.status_code == 201

    # List Projects
    r_list = client.get("/api/projects")
    assert r_list.status_code == 200
    p_ids = [p["project_id"] for p in r_list.json()]
    assert "alpha" in p_ids and "beta" in p_ids

    # Threads in Alpha are isolated from Beta
    t_a = client.post("/api/projects/alpha/threads", json={"title": "Alpha Planning"})
    assert t_a.status_code == 201
    t_a_id = t_a.json()["thread_id"]

    alpha_threads = client.get("/api/projects/alpha/threads").json()
    beta_threads = client.get("/api/projects/beta/threads").json()
    assert any(t["thread_id"] == t_a_id for t in alpha_threads)
    assert not any(t["thread_id"] == t_a_id for t in beta_threads)

    # 404 for non-existent project
    assert client.get("/api/projects/nonexistent/sidecar").status_code == 404


# --------------------------------------------------------------------------- #
# 2. Multi-Source Ingestion & Live Sidecar
# --------------------------------------------------------------------------- #
def test_multi_source_ingestion_populates_sidecar(client):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments"})

    # 1. Ingest Excel via multipart file upload
    excel_bytes = _create_sample_excel_bytes()
    r_excel = client.post(
        "/api/projects/pay/ingest/excel",
        files={"file": ("scenarios.xlsx", excel_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r_excel.status_code == 200
    assert r_excel.json()["rows_ingested"] == 4

    # 2. Ingest BRD via JSON
    r_brd = client.post(
        "/api/projects/pay/ingest/brd",
        json={"content": SAMPLE_BRD, "source_name": "BRD.md"},
    )
    assert r_brd.status_code == 200
    assert r_brd.json()["requirements_ingested"] == 2

    # 3. Ingest Code via JSON snippet
    r_code = client.post(
        "/api/projects/pay/ingest/code",
        json={"code": SAMPLE_CODE, "filename": "models.py"},
    )
    assert r_code.status_code == 200
    assert r_code.json()["symbols_found"] >= 2

    # 4. Verify Sidecar reflects all three sources
    r_sidecar = client.get("/api/projects/pay/sidecar")
    assert r_sidecar.status_code == 200
    sidecar = r_sidecar.json()

    # Scenarios loaded
    assert len(sidecar["scenarios"]) == 4
    assert sidecar["status_report"]["total_scenarios"] == 4

    # BRD requirements recorded as DOCUMENTED_FACT decisions
    assert len(sidecar["active_decisions"]) >= 2
    assert any(d["subject_entity"] == "requirement:REQ-001" for d in sidecar["active_decisions"])

    # Triangulation discrepancy detected: REQ-001 mandatory vs email nullable=True in code
    agenda = sidecar["stakeholder_agenda"]
    assert any(item["type"] == "BRD_CODE_DISCREPANCY" for item in agenda)


# --------------------------------------------------------------------------- #
# 3. Multi-Thread Conversational Memory (Section 13 Benchmark)
# --------------------------------------------------------------------------- #
def test_multi_thread_shared_conversational_memory(client):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments"})
    client.post(
        "/api/projects/pay/ingest/excel",
        files={"file": ("scenarios.xlsx", _create_sample_excel_bytes(), "application/octet-stream")},
    )
    client.post("/api/projects/pay/ingest/brd", json={"content": SAMPLE_BRD})

    # Thread 1: Developer reports updates
    t1 = client.post("/api/projects/pay/threads", json={"title": "Dev Daily Thread"}).json()
    t1_id = t1["thread_id"]

    chat1 = client.post(
        f"/api/projects/pay/threads/{t1_id}/chat",
        json={"message": "completed SC-001, SC-002 is blocked on vendor KYC API, and NSI confirmed REQ-001 is optional"},
    )
    assert chat1.status_code == 200
    res1 = chat1.json()

    # Mutations ribbon items generated
    assert len(res1["mutations"]) >= 3
    assert any("SC-001" in m for m in res1["mutations"])
    assert any("SC-002" in m and "Blocked" in m for m in res1["mutations"])
    assert any("REQ-001" in m for m in res1["mutations"])

    # Immediate sidecar update
    sc = res1["sidecar"]
    s1 = next(s for s in sc["scenarios"] if s["scenario_num"] == 1)
    s2 = next(s for s in sc["scenarios"] if s["scenario_num"] == 2)
    assert s1["status"] == "Impl_Reported"
    assert s2["status"] == "Blocked"
    assert "vendor KYC API" in s2["blockers"]

    # Thread 2: A different consultant opens a new thread and queries current project state
    t2 = client.post("/api/projects/pay/threads", json={"title": "Executive Overview Thread"}).json()
    t2_id = t2["thread_id"]

    chat2 = client.post(
        f"/api/projects/pay/threads/{t2_id}/chat",
        json={"message": "What is the current state of the project?"},
    )
    assert chat2.status_code == 200
    reply2 = chat2.json()["reply"]

    # Thread 2's response reflects the updates enacted in Thread 1
    assert "SC-002" in reply2
    assert "vendor KYC API" in reply2


# --------------------------------------------------------------------------- #
# 4. Lightweight (Read-Only) Mode
# --------------------------------------------------------------------------- #
def test_lightweight_mode_does_not_mutate_memory(client):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments"})
    client.post(
        "/api/projects/pay/ingest/excel",
        files={"file": ("scenarios.xlsx", _create_sample_excel_bytes(), "application/octet-stream")},
    )

    t = client.post("/api/projects/pay/threads", json={"title": "Quick Ask", "mode": "lightweight"}).json()
    tid = t["thread_id"]

    # Send a completion claim in lightweight mode
    res = client.post(
        f"/api/projects/pay/threads/{tid}/chat",
        json={"message": "completed SC-004 and SC-001"},
    )
    assert res.status_code == 200
    data = res.json()

    # Mutations must be empty
    assert data["mutations"] == []

    # Scenarios must NOT have mutated
    sidecar = client.get("/api/projects/pay/sidecar").json()
    s4 = next(s for s in sidecar["scenarios"] if s["scenario_num"] == 4)
    s1 = next(s for s in sidecar["scenarios"] if s["scenario_num"] == 1)
    assert s4["status"] == "Unstarted"
    assert s1["status"] == "Unstarted"


# --------------------------------------------------------------------------- #
# 5. Undo Ribbon (Deterministic Reversal)
# --------------------------------------------------------------------------- #
def test_undo_ribbon_reverts_turn_mutations(client):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments"})
    client.post(
        "/api/projects/pay/ingest/excel",
        files={"file": ("scenarios.xlsx", _create_sample_excel_bytes(), "application/octet-stream")},
    )
    t = client.post("/api/projects/pay/threads", json={"title": "Dev Sprint"}).json()
    tid = t["thread_id"]

    # Apply turn with mutations
    turn_res = client.post(
        f"/api/projects/pay/threads/{tid}/chat",
        json={"message": "SC-003 is blocked on infrastructure and completed SC-004"},
    ).json()

    asst_msg_id = turn_res["assistant_message_id"]
    assert len(turn_res["mutations"]) >= 2

    # Check sidecar before undo
    sc_before = client.get("/api/projects/pay/sidecar").json()["scenarios"]
    assert next(s for s in sc_before if s["scenario_num"] == 3)["status"] == "Blocked"
    assert next(s for s in sc_before if s["scenario_num"] == 4)["status"] == "Impl_Reported"

    # Call Undo Endpoint
    undo_res = client.post(f"/api/projects/pay/messages/{asst_msg_id}/undo")
    assert undo_res.status_code == 200
    undo_data = undo_res.json()
    assert undo_data["success"] is True
    assert undo_data["already_undone"] is False

    # Check sidecar after undo: reverted to prior state
    sc_after = client.get("/api/projects/pay/sidecar").json()["scenarios"]
    s3_restored = next(s for s in sc_after if s["scenario_num"] == 3)
    s4_restored = next(s for s in sc_after if s["scenario_num"] == 4)
    assert s3_restored["status"] == "Ready_For_Impl"
    assert s3_restored["blockers"] is None
    assert s4_restored["status"] == "Unstarted"

    # Idempotent re-undo
    re_undo = client.post(f"/api/projects/pay/messages/{asst_msg_id}/undo").json()
    assert re_undo["already_undone"] is True


# --------------------------------------------------------------------------- #
# 6. Agent Handoff Spec Endpoint & Web UI
# --------------------------------------------------------------------------- #
def test_agent_handoff_and_static_ui(client):
    client.post("/api/projects", json={"project_id": "pay", "name": "Payments Platform"})
    client.post(
        "/api/projects/pay/ingest/excel",
        files={"file": ("scenarios.xlsx", _create_sample_excel_bytes(), "application/octet-stream")},
    )
    client.post("/api/projects/pay/ingest/brd", json={"content": SAMPLE_BRD})

    # Test Handoff Endpoint
    h_res = client.post("/api/projects/pay/handoff", json={"scenarios": "SC-001,SC-002"})
    assert h_res.status_code == 200
    markdown = h_res.json()["markdown"]
    assert "# Agent Handoff Spec: Payments Platform" in markdown
    assert "SC-001" in markdown and "SC-002" in markdown

    # Test Web UI HTML served at /
    ui_res = client.get("/")
    assert ui_res.status_code == 200
    assert "AI Project Consultant" in ui_res.text
    assert "Projects Tab" in ui_res.text
    assert "Knowledge Dump" in ui_res.text
