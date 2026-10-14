"""Sprint 1 verification: storage, Excel ingestion, bi-temporal decisions,
controlled authority and discrepancy detection."""

from __future__ import annotations

import io
import warnings

import pytest
from openpyxl import Workbook

from core.engine.reconciler import DiscrepancyWarning, reconcile_chat_claim
from core.ingestion.excel_parser import ingest_scenarios_excel
from core.storage.db import ProjectMemoryDB, ProjectNotFoundError

HEADERS = ["Scenario #", "Title", "Description", "Status", "Blockers", "Linked Requirement"]
SHEET_STATUSES = ["Not Started", "Blocked", "Needs Clarification", "Ready", "Done", "Verified"]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    database = ProjectMemoryDB(":memory:")
    yield database
    database.close()


@pytest.fixture
def two_projects(db):
    db.create_project("proj-a", "Project Alpha", "/repos/alpha")
    db.create_project("proj-b", "Project Beta", "/repos/beta")
    return db


def _scenario_workbook(rows: int = 80, bad_status_at: int | None = None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Scenarios"
    ws.append(HEADERS)
    for n in range(1, rows + 1):
        status = "Teleported" if n == bad_status_at else SHEET_STATUSES[(n - 1) % len(SHEET_STATUSES)]
        ws.append(
            [
                n,
                f"Scenario title {n}",
                f"Payment flow variant uniqtoken{n:03d} handles edge case {n}",
                status,
                f"Waiting on vendor API {n}" if status == "Blocked" else None,
                f"REQ-{100 + n}",
            ]
        )
    return wb


@pytest.fixture
def scenarios_xlsx(tmp_path):
    path = tmp_path / "80_Scenarios.xlsx"
    _scenario_workbook(80).save(path)
    return path


@pytest.fixture
def payments_project(db):
    """Project with a BRD mandatory rule recorded as a DOCUMENTED_FACT."""
    db.create_project("pay", "Payments Platform")
    brd_text = "All payments above $10,000 MUST require dual approval by two distinct approvers."
    db.add_evidence("pay", "BRD-4.2", "BRD", "BRD.md#L42-L44", brd_text, byte_range="1024-1105")
    fact_id = db.record_decision(
        "pay",
        "payments.high_value.approval",
        "Payments above $10,000 require dual approval",
        "DOCUMENTED_FACT",
        "BRD-4.2",
        rationale="BRD section 4.2 mandatory control",
        created_by="ba@client",
    )
    db.upsert_scenario("pay", {"scenario_num": 14, "title": "High-value payment approval"})
    return db, fact_id


# --------------------------------------------------------------------------- #
# 1. Database creation & project isolation
# --------------------------------------------------------------------------- #
def test_schema_tables_created(db):
    names = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master")}
    assert {"projects", "scenarios", "decisions", "evidence_vault"} <= names


def test_project_isolation(two_projects):
    db = two_projects
    db.upsert_scenario("proj-a", {"scenario_num": 1, "title": "A login", "status": "Blocked"})
    db.upsert_scenario("proj-b", {"scenario_num": 1, "title": "B login"})
    db.add_evidence("proj-a", "E1", "CHAT", "chat:a", "alpha secret rollout plan")
    db.add_evidence("proj-b", "E1", "CHAT", "chat:b", "beta public rollout plan")
    db.record_decision("proj-a", "auth", "Use SSO", "APPROVED_DECISION", "E1")

    a, b = db.get_scenarios("proj-a"), db.get_scenarios("proj-b")
    assert [s["title"] for s in a] == ["A login"]
    assert [s["title"] for s in b] == ["B login"]
    assert a[0]["id"] != b[0]["id"]
    assert db.get_scenarios("proj-a", status="Blocked")[0]["title"] == "A login"
    assert db.get_scenarios("proj-b", status="Blocked") == []

    # Same evidence id in two projects stays distinct; search never leaks.
    assert [r["content"] for r in db.search_evidence("proj-a", "rollout")] == ["alpha secret rollout plan"]
    assert db.search_evidence("proj-b", "secret") == []
    assert db.get_decisions("proj-b") == []
    assert len(db.get_decisions("proj-a")) == 1

    # Cross-project writes and unknown projects are rejected.
    with pytest.raises(PermissionError):
        db.upsert_scenario("proj-b", {"id": a[0]["id"], "scenario_num": 1, "title": "hijack"})
    with pytest.raises(ValueError):  # evidence from proj-b cannot back a proj-a decision
        db.record_decision("proj-a", "x", "y", "PROPOSAL", "only-in-b")
    with pytest.raises(ProjectNotFoundError):
        db.get_scenarios("proj-zzz")
    with pytest.raises(ProjectNotFoundError):
        db.add_evidence("proj-zzz", "E", "CHAT", "ref", "text")


def test_partial_upsert_preserves_fields_and_verified_requires_evidence(two_projects):
    db = two_projects
    db.upsert_scenario(
        "proj-a",
        {"scenario_num": 7, "title": "Refunds", "description": "Full refunds", "code_symbols": ["refund()"]},
    )
    db.upsert_scenario("proj-a", {"scenario_num": 7, "status": "Blocked", "blockers": "Ledger API"})
    s = db.get_scenario("proj-a", 7)
    assert (s["title"], s["description"], s["status"], s["blockers"]) == (
        "Refunds", "Full refunds", "Blocked", "Ledger API"
    )
    assert s["code_symbols"] == ["refund()"]
    with pytest.raises(ValueError):
        db.upsert_scenario("proj-a", {"scenario_num": 7, "status": "Verified"})


def test_evidence_is_immutable_and_search_is_injection_safe(two_projects):
    db = two_projects
    db.add_evidence("proj-a", "E9", "BRD", "BRD.md#L1", "Users MUST reset passwords every 90 days")
    db.add_evidence("proj-a", "E9", "BRD", "BRD.md#L1", "Users MUST reset passwords every 90 days")  # no-op
    assert db.count_evidence("proj-a") == 1
    with pytest.raises(ValueError):
        db.add_evidence("proj-a", "E9", "BRD", "BRD.md#L1", "tampered")
    with pytest.raises(ValueError):
        db.add_evidence("proj-a", "E10", "TWEET", "x", "bad source type")
    # FTS5 operators / quotes in user input must not raise.
    assert db.search_evidence("proj-a", 'passwords" OR NEAR(* -days') == []
    hits = db.search_evidence("proj-a", "reset passwords")
    assert hits and hits[0]["evidence_id"] == "E9" and isinstance(hits[0]["score"], float)


# --------------------------------------------------------------------------- #
# 2. Excel ingestion (80 rows)
# --------------------------------------------------------------------------- #
def test_ingest_80_row_excel(db, scenarios_xlsx):
    db.create_project("proj-x", "Excel Project")
    summary = ingest_scenarios_excel(db, "proj-x", str(scenarios_xlsx))

    assert summary["rows_ingested"] == 80
    scenarios = db.get_scenarios("proj-x")
    assert len(scenarios) == 80
    assert [s["scenario_num"] for s in scenarios] == list(range(1, 81))
    assert db.count_evidence("proj-x", "EXCEL") == 80

    s14 = db.get_scenario("proj-x", 14)
    assert s14["title"] == "Scenario title 14"
    assert s14["linked_req_id"] == "REQ-114"
    assert s14["status"] == "Blocked"  # (14-1) % 6 == 1
    assert s14["blockers"] == "Waiting on vendor API 14"

    # 'Done' and 'Verified' cells are claims, not proof -> Impl_Reported.
    assert db.get_scenario("proj-x", 5)["status"] == "Impl_Reported"
    assert db.get_scenario("proj-x", 6)["status"] == "Impl_Reported"
    assert db.get_scenarios("proj-x", status="Verified") == []

    # Verbatim row text + exact sheet/row coordinate in the evidence vault.
    hits = db.search_evidence("proj-x", "uniqtoken014")
    assert len(hits) == 1
    assert hits[0]["source_type"] == "EXCEL"
    assert hits[0]["source_reference"] == "80_Scenarios.xlsx!Scenarios!A15:F15"
    assert hits[0]["content"].startswith("Scenario #: 14 | Title: Scenario title 14 | Description:")
    assert "Linked Requirement: REQ-114" in hits[0]["content"]


def test_excel_reingest_is_idempotent_and_in_memory_stream(db):
    db.create_project("proj-x", "Excel Project")
    buf = io.BytesIO()
    _scenario_workbook(80).save(buf)
    buf.seek(0)
    first = ingest_scenarios_excel(db, "proj-x", buf)
    buf.seek(0)
    second = ingest_scenarios_excel(db, "proj-x", buf)
    assert first["evidence_ids"] == second["evidence_ids"]
    assert len(db.get_scenarios("proj-x")) == 80
    assert db.count_evidence("proj-x", "EXCEL") == 80


def test_excel_bad_row_is_atomic(db, tmp_path):
    db.create_project("proj-x", "Excel Project")
    path = tmp_path / "bad.xlsx"
    _scenario_workbook(80, bad_status_at=40).save(path)
    with pytest.raises(ValueError, match=r"bad.xlsx!Scenarios!41"):
        ingest_scenarios_excel(db, "proj-x", path)
    assert db.get_scenarios("proj-x") == []
    assert db.count_evidence("proj-x") == 0


# --------------------------------------------------------------------------- #
# 3. Bi-temporal decision superseding
# --------------------------------------------------------------------------- #
def test_bitemporal_decision_supersede(two_projects):
    db = two_projects
    db.add_evidence("proj-a", "MTG-1", "CHAT", "meeting:2026-10-01", "We'll use Postgres.")
    db.add_evidence("proj-a", "MTG-2", "CHAT", "meeting:2026-10-08", "Switching to SQLite for v1.")

    old_id = db.record_decision("proj-a", "storage.engine", "Use Postgres", "APPROVED_DECISION", "MTG-1")
    new_id = db.record_decision(
        "proj-a", "storage.engine", "Use SQLite for v1", "APPROVED_DECISION", "MTG-2",
        rationale="Single-user desktop deployment", created_by="tech-lead",
    )

    old, new = db.get_decision("proj-a", old_id), db.get_decision("proj-a", new_id)
    assert old["valid_to"] is not None
    assert old["superseded_by"] == new_id
    assert old["valid_to"] == new["valid_from"]
    assert old["valid_from"] <= old["valid_to"]
    assert new["valid_to"] is None and new["superseded_by"] is None

    history = db.get_decisions("proj-a", "storage.engine")
    assert [d["id"] for d in history] == [old_id, new_id]
    assert [d["id"] for d in db.get_decisions("proj-a", "storage.engine", active_only=True)] == [new_id]

    # Superseding is scoped to the subject.
    db.add_evidence("proj-a", "MTG-3", "CHAT", "meeting", "Use REST.")
    db.record_decision("proj-a", "api.style", "REST", "APPROVED_DECISION", "MTG-3")
    assert db.get_decision("proj-a", new_id)["valid_to"] is None

    with pytest.raises(ValueError):
        db.record_decision("proj-a", "storage.engine", "x", "GUESS", "MTG-1")


# --------------------------------------------------------------------------- #
# 4. Epistemic verification & discrepancy detection
# --------------------------------------------------------------------------- #
def test_completion_claim_without_evidence_is_impl_reported(payments_project):
    db, _ = payments_project
    result = reconcile_chat_claim(
        db,
        "pay",
        {
            "text": "Scenario 14 is done and verified, shipped it last night!",
            "speaker": "dev@vendor",
            "source_reference": "slack:#payments/1697012345.0001",
            "scenario_num": 14,
            "reported_status": "Verified",
        },
    )
    assert result["requested_status"] == "Verified"
    assert result["applied_status"] == "Impl_Reported"
    assert result["verified"] is False
    assert db.get_scenario("pay", 14)["status"] == "Impl_Reported"
    ev = db.get_evidence("pay", result["evidence_id"])
    assert ev["source_type"] == "CHAT"
    assert ev["content"] == "Scenario 14 is done and verified, shipped it last night!"


def test_completion_claim_with_invalid_evidence_is_not_verified(payments_project):
    db, _ = payments_project
    db.create_project("other", "Other")
    db.add_evidence("other", "DIFF-X", "GIT_DIFF", "abc123", "diff --git a/x b/x")
    db.add_evidence("pay", "CHAT-PROOF", "CHAT", "slack", "trust me it works")
    for bogus in ("DIFF-X", "CHAT-PROOF", "does-not-exist"):
        result = reconcile_chat_claim(
            db, "pay",
            {"text": f"Done, see {bogus}", "scenario_num": 14, "reported_status": "Done",
             "verification_evidence_id": bogus},
        )
        assert result["applied_status"] == "Impl_Reported"
    assert db.get_scenario("pay", 14)["verification_evidence_id"] is None


def test_completion_claim_with_git_diff_is_verified(payments_project):
    db, _ = payments_project
    db.add_evidence(
        "pay", "DIFF-9f3c", "GIT_DIFF", "git:9f3c1e2",
        "diff --git a/payments/approval.py b/payments/approval.py\n+require_two_approvers(amount)",
        byte_range="0-92",
    )
    result = reconcile_chat_claim(
        db, "pay",
        {"text": "Dual approval merged in 9f3c1e2", "scenario_num": 14, "reported_status": "Done",
         "verification_evidence_id": "DIFF-9f3c"},
    )
    assert result["applied_status"] == "Verified" and result["verified"] is True
    s = db.get_scenario("pay", 14)
    assert s["status"] == "Verified" and s["verification_evidence_id"] == "DIFF-9f3c"

    # A later unproven 'done' claim must not downgrade proven status.
    again = reconcile_chat_claim(db, "pay", {"text": "yep done", "scenario_num": 14, "reported_status": "done"})
    assert again["applied_status"] == "Verified"
    assert db.get_scenario("pay", 14)["status"] == "Verified"


def test_reported_claim_contradicting_brd_fact_flags_discrepancy(payments_project):
    db, fact_id = payments_project
    claim = {
        "text": "FYI we went with single approval for all payment amounts, dual approval slowed QA.",
        "speaker": "dev@vendor",
        "source_reference": "slack:#payments/1697099999.0042",
        "subject_entity": "payments.high_value.approval",
        "assertion": "Single approval for all payment amounts",
        "scenario_num": 14,
        "reported_status": "Done",
    }
    with pytest.warns(DiscrepancyWarning, match="contradicts DOCUMENTED_FACT"):
        result = reconcile_chat_claim(db, "pay", claim)

    assert len(result["discrepancies"]) == 1
    d = result["discrepancies"][0]
    assert d["fact_decision_id"] == fact_id
    assert d["fact_evidence_id"] == "BRD-4.2"
    assert d["claim_evidence_id"] == result["evidence_id"]

    # Both records remain; the BRD fact is NOT overwritten or superseded.
    fact = db.get_decision("pay", fact_id)
    assert fact["valid_to"] is None and fact["superseded_by"] is None
    assert fact["decision_text"] == "Payments above $10,000 require dual approval"
    active = db.get_decisions("pay", "payments.high_value.approval", active_only=True)
    assert {a["epistemic_status"] for a in active} == {"DOCUMENTED_FACT", "REPORTED_CLAIM"}
    claim_rec = db.get_decision("pay", result["claim_decision_id"])
    assert claim_rec["created_by"] == "dev@vendor"
    assert claim_rec["source_evidence_id"] == result["evidence_id"]
    # And the unverified 'Done' is still capped.
    assert db.get_scenario("pay", 14)["status"] == "Impl_Reported"


def test_newer_claim_supersedes_older_claim_but_not_fact(payments_project):
    db, fact_id = payments_project
    with pytest.warns(DiscrepancyWarning):
        first = reconcile_chat_claim(
            db, "pay", {"text": "single approval only", "subject_entity": "payments.high_value.approval"}
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # agreeing claim must NOT warn
        second = reconcile_chat_claim(
            db, "pay",
            {"text": "Fixed: payments above $10,000 require dual approval",
             "subject_entity": "payments.high_value.approval",
             "assertion": "Payments above $10,000 require dual approval"},
        )
    assert second["discrepancies"] == []
    assert db.get_decision("pay", first["claim_decision_id"])["superseded_by"] == second["claim_decision_id"]
    assert db.get_decision("pay", fact_id)["valid_to"] is None
