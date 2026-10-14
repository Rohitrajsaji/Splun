"""Sprint 2 verification: BRD parsing, AST scanning, triangulation,
consultant reports, handoff spec and CLI."""

from __future__ import annotations

import textwrap
import warnings

import pytest

from cli.consultant import main as cli_main
from core.engine.consultant import TechnicalConsultant
from core.engine.reconciler import DiscrepancyWarning, reconcile_chat_claim
from core.ingestion.brd_parser import ingest_brd_markdown, parse_brd_markdown, subject_for
from core.ingestion.code_scanner import get_symbol_info, scan_python_codebase
from core.storage.db import ProjectMemoryDB

BRD = textwrap.dedent(
    """\
    # Payments Platform BRD

    ## 1. Customer Onboarding

    ### REQ-001: Customer Email Validation
    The system MUST capture a valid `email` for every customer at onboarding.
    - **Constraint:** Mandatory
    - **Field:** `email`
    - **Acceptance Criteria:**
      - Submitting the form without an email returns a validation error
      - Emails are validated against RFC 5322

    ### REQ-002: Marketing Opt-in
    Customers may opt in to marketing emails.
    Constraint: optional
    Field: marketing_opt_in

    ## 2. Payments

    - **REQ-003**: Dual Approval
      Payments above $10,000 SHALL require two distinct approvers.
    - REQ-004: Payment Reference
      A free-text `reference` field is optional.

    ```markdown
    ### REQ-999: Example inside a code fence (must be ignored)
    ```

    ## Glossary
    NSI: National Systems Integrator.
    """
)

MODELS_PY = textwrap.dedent(
    """\
    from typing import Optional


    class Customer(Base):
        __tablename__ = "customers"
        id = Column(Integer, primary_key=True)
        email = Column(String(255), nullable=True)
        name = Column(String, nullable=False)
        marketing_opt_in = Column(Boolean, default=False)

        def full_name(self):
            return self.name


    class CustomerSchema(BaseModel):
        email: Optional[str] = None
        reference: str | None = None


    async def create_customer(payload, *, notify=True):
        return payload


    legacy_reference = Field(required=False)
    """
)


def _line_of(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    database = ProjectMemoryDB(":memory:")
    database.create_project("pay", "Payments Platform", "/repos/payments")
    yield database
    database.close()


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "src"
    (root / "app").mkdir(parents=True)
    (root / "app" / "models.py").write_text(MODELS_PY, encoding="utf-8")
    (root / "app" / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "ignored.py").write_text("class Ignored: pass\n", encoding="utf-8")
    return root


@pytest.fixture
def loaded(db, codebase):
    """BRD + code + scenarios, ready for consultant queries."""
    ingest_brd_markdown(db, "pay", "BRD.md", content=BRD)
    scan_python_codebase(db, "pay", str(codebase))
    rows = [
        (10, "Capture customer email", "Ready_For_Impl", None, "REQ-001", ["Customer"]),
        (11, "Marketing opt-in toggle", "Unstarted", None, "REQ-002", ["Customer"]),
        (12, "Dual approval workflow", "Blocked", "Waiting on IAM roles", "REQ-003", None),
        (13, "Payment reference field", "Clarification_Needed", None, "REQ-004", None),
        (14, "Refund ledger export", "Unstarted", None, None, None),
        (15, "Audit log retention", "Ready_For_Impl", None, None, None),
        (16, "Fraud score hook", "Unstarted", "Vendor contract pending", None, None),
        (17, "Statement PDF", "Impl_Reported", None, None, None),
    ]
    for num, title, status, blockers, req, symbols in rows:
        data = {"scenario_num": num, "title": title, "status": status, "blockers": blockers,
                "linked_req_id": req, "description": f"Description for {title}"}
        if symbols:
            data["code_symbols"] = symbols
        db.upsert_scenario("pay", data)
    return TechnicalConsultant(db, "pay")


# --------------------------------------------------------------------------- #
# 1. BRD parsing
# --------------------------------------------------------------------------- #
def test_parse_brd_extracts_requirements_and_constraints():
    reqs = {r.req_id: r for r in parse_brd_markdown(BRD)}
    assert list(reqs) == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]  # REQ-999 is fenced

    r1 = reqs["REQ-001"]
    assert r1.title == "Customer Email Validation"
    assert r1.statement == "The system MUST capture a valid `email` for every customer at onboarding."
    assert (r1.constraint_level, r1.constraint_source) == ("mandatory", "explicit")
    assert r1.fields == ["email"]
    assert r1.acceptance_criteria == [
        "Submitting the form without an email returns a validation error",
        "Emails are validated against RFC 5322",
    ]
    assert r1.line_start == _line_of(BRD, "### REQ-001")
    assert r1.line_end == _line_of(BRD, "RFC 5322")

    assert (reqs["REQ-002"].constraint_level, reqs["REQ-002"].constraint_source) == ("optional", "explicit")
    assert reqs["REQ-002"].fields == ["marketing_opt_in"]
    assert reqs["REQ-003"].title == "Dual Approval"
    assert reqs["REQ-003"].statement == "Payments above $10,000 SHALL require two distinct approvers."
    assert (reqs["REQ-003"].constraint_level, reqs["REQ-003"].constraint_source) == ("mandatory", "keyword")
    assert reqs["REQ-004"].line_start == reqs["REQ-004"].line_end - 1  # bullet + one continuation
    assert (reqs["REQ-004"].constraint_level, reqs["REQ-004"].fields) == ("optional", ["reference"])


def test_ingest_brd_stores_evidence_and_documented_facts(db):
    result = ingest_brd_markdown(db, "pay", "docs/BRD.md", content=BRD)
    assert result["req_ids"] == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]
    assert db.count_evidence("pay", "BRD") == 4

    req = db.get_requirement("pay", "REQ-001")
    start, end = _line_of(BRD, "### REQ-001"), _line_of(BRD, "RFC 5322")
    assert req["source_reference"] == f"BRD.md#L{start}-L{end}"
    ev = db.get_evidence("pay", req["evidence_id"])
    assert ev["source_type"] == "BRD"
    assert ev["content"].startswith("### REQ-001: Customer Email Validation")
    b0, b1 = map(int, ev["byte_range"].split("-"))
    assert BRD.encode("utf-8")[b0:b1].decode("utf-8") == ev["content"]  # exact byte span

    facts = db.get_decisions("pay", subject_for("REQ-001"), active_only=True)
    assert len(facts) == 1
    assert facts[0]["epistemic_status"] == "DOCUMENTED_FACT"
    assert facts[0]["decision_text"] == req["statement"]
    assert facts[0]["source_evidence_id"] == req["evidence_id"]
    assert db.search_evidence("pay", "distinct approvers")[0]["source_reference"].startswith("BRD.md#L")


def test_brd_reingest_idempotent_and_revision_supersedes(db):
    ingest_brd_markdown(db, "pay", "BRD.md", content=BRD)
    ingest_brd_markdown(db, "pay", "BRD.md", content=BRD)
    assert len(db.get_decisions("pay", subject_for("REQ-001"))) == 1
    assert db.count_evidence("pay", "BRD") == 4

    revised = BRD.replace("for every customer at onboarding", "for every customer and guarantor")
    ingest_brd_markdown(db, "pay", "BRD.md", content=revised)
    history = db.get_decisions("pay", subject_for("REQ-001"))
    assert len(history) == 2
    assert history[0]["superseded_by"] == history[1]["id"] and history[1]["valid_to"] is None
    assert "guarantor" in db.get_requirement("pay", "REQ-001")["statement"]
    assert len(db.get_decisions("pay", subject_for("REQ-002"))) == 1  # untouched


def test_brd_duplicate_ids_rejected(db):
    with pytest.raises(ValueError, match="Duplicate requirement id REQ-001"):
        ingest_brd_markdown(db, "pay", "x.md", content="### REQ-001: A\nx\n### REQ-001: B\ny\n")


# --------------------------------------------------------------------------- #
# 2. AST code scanning
# --------------------------------------------------------------------------- #
def test_scan_python_codebase(db, codebase):
    result = scan_python_codebase(db, "pay", str(codebase))
    assert result["files_scanned"] == 1
    assert [e["file"] for e in result["errors"]] == ["app/broken.py"]

    syms = {s["qualname"]: s for s in db.get_code_symbols("pay")}
    assert "Ignored" not in syms  # .venv skipped
    assert syms["Customer"]["kind"] == "class"
    assert syms["Customer"]["details"]["bases"] == ["Base"]
    assert syms["Customer.full_name"]["kind"] == "method"
    assert syms["create_customer"]["kind"] == "function"
    assert syms["create_customer"]["details"]["async"] is True
    assert syms["create_customer"]["details"]["args"] == ["payload", "notify"]

    email = syms["Customer.email"]
    assert email["kind"] == "field"
    assert email["details"]["keywords"] == {"nullable": True}
    assert email["details"]["indicates"] == "nullable"
    assert email["line_start"] == _line_of(MODELS_PY, "email = Column")
    assert syms["Customer.name"]["details"]["indicates"] == "mandatory"
    assert syms["Customer.id"]["details"]["indicates"] is None
    assert syms["CustomerSchema.email"]["details"]["indicates"] == "nullable"
    assert "annotation=Optional[str]" in syms["CustomerSchema.email"]["details"]["indicators"]
    assert syms["CustomerSchema.reference"]["details"]["indicates"] == "nullable"
    assert syms["legacy_reference"]["kind"] == "assignment"
    assert syms["legacy_reference"]["details"]["indicates"] == "optional"

    assert db.count_evidence("pay", "CODE_AST") == len(syms)


def test_get_symbol_info_returns_verbatim_evidence(db, codebase):
    scan_python_codebase(db, "pay", str(codebase))
    info = get_symbol_info(db, "pay", "email")
    assert [i["qualname"] for i in info] == ["Customer.email", "CustomerSchema.email"]
    ev = info[0]["evidence"]
    line = _line_of(MODELS_PY, "email = Column")
    assert ev["source_type"] == "CODE_AST"
    assert ev["source_reference"] == f"app/models.py#L{line}-L{line}"
    assert ev["content"] == "email = Column(String(255), nullable=True)"
    b0, b1 = map(int, ev["byte_range"].split("-"))
    assert MODELS_PY.encode("utf-8")[b0:b1].decode("utf-8") == ev["content"]
    assert get_symbol_info(db, "pay", "Customer.full_name")[0]["kind"] == "method"
    assert get_symbol_info(db, "pay", "nope") == []

    # Re-scan is idempotent for evidence and replaces the symbol snapshot.
    before = db.count_evidence("pay", "CODE_AST")
    scan_python_codebase(db, "pay", str(codebase))
    assert db.count_evidence("pay", "CODE_AST") == before


# --------------------------------------------------------------------------- #
# 3. Triangulation
# --------------------------------------------------------------------------- #
def test_triangulation_flags_mandatory_vs_nullable(loaded):
    tri = loaded.triangulate_scenario("SC-010")
    assert tri["brd_constraint"] == "mandatory"
    assert tri["discrepancy_detected"] is True
    assert {c["symbol"] for c in tri["conflicts"]} == {"Customer.email", "CustomerSchema.email"}
    assert all(c["brd_reference"].startswith("BRD.md#L") for c in tri["conflicts"])
    assert tri["requirement_evidence"][0]["content"].startswith("### REQ-001")
    assert any("REQ-001" in q and "app/models.py" in q for q in tri["clarification_questions"])
    assert any(d["epistemic_status"] == "DOCUMENTED_FACT" for d in tri["active_decisions"])


def test_triangulation_no_conflict_cases(loaded):
    assert loaded.triangulate_scenario("SC-011")["discrepancy_detected"] is False  # optional req
    assert loaded.triangulate_scenario("SC-012")["discrepancy_detected"] is False  # no field
    assert loaded.triangulate_scenario("14")["requirement"] is None
    with pytest.raises(LookupError):
        loaded.triangulate_scenario("SC-999")
    with pytest.raises(LookupError):
        loaded.triangulate_scenario("other-project:SC-010")


def test_approved_decision_resolves_then_brd_revision_reopens(loaded):
    db = loaded.db
    db.add_evidence("pay", "MTG-7", "CHAT", "meeting:2026-10-13",
                    "Client agreed: email optional for walk-in customers in phase 1.")
    approval = db.record_decision("pay", subject_for("REQ-001"),
                                  "Email may be null for walk-in customers in phase 1",
                                  "APPROVED_DECISION", "MTG-7", created_by="client-po")
    tri = loaded.triangulate_scenario("SC-010")
    assert tri["discrepancy_detected"] is False
    assert tri["approved_override"]["id"] == approval
    assert tri["conflicts"]  # mismatch still visible, but accepted

    # Unchanged BRD re-ingest must NOT reopen it...
    ingest_brd_markdown(db, "pay", "BRD.md", content=BRD)
    assert loaded.triangulate_scenario("SC-010")["discrepancy_detected"] is False
    # ...but a revised BRD (newer fact) does.
    ingest_brd_markdown(db, "pay", "BRD.md", content=BRD.replace("valid `email`", "verified `email`"))
    assert loaded.triangulate_scenario("SC-010")["discrepancy_detected"] is True


# --------------------------------------------------------------------------- #
# 4. Consultant reports, next scenarios, agenda, handoff
# --------------------------------------------------------------------------- #
def test_project_status_report(loaded):
    r = loaded.get_project_status_report()
    assert r["total_scenarios"] == 8
    assert r["by_status"] == {"Unstarted": 3, "Clarification_Needed": 1, "Blocked": 1,
                              "Ready_For_Impl": 2, "Impl_Reported": 1, "Verified": 0}
    assert [b["scenario"] for b in r["active_blockers"]] == ["SC-012", "SC-016"]
    assert r["requirements"] == 4


def test_get_next_scenarios(loaded):
    nxt = loaded.get_next_scenarios()
    # Ready_For_Impl first, then Unstarted; SC-016 has a blocker, SC-012 is Blocked.
    assert [s["scenario_num"] for s in nxt] == [10, 15, 11, 14]
    assert [s["scenario_num"] for s in loaded.get_next_scenarios(limit=2)] == [10, 15]


def test_stakeholder_agenda(loaded):
    with pytest.warns(DiscrepancyWarning):
        reconcile_chat_claim(loaded.db, "pay", {
            "text": "We shipped single approval for all payments.",
            "speaker": "dev@nsi",
            "subject_entity": subject_for("REQ-003"),
            "assertion": "Single approval for all payments",
        })
    agenda = loaded.get_stakeholder_agenda()
    by_type: dict[str, list[dict]] = {}
    for item in agenda:
        by_type.setdefault(item["type"], []).append(item)

    assert set(by_type) == {"CLAIM_DISCREPANCY", "BRD_CODE_DISCREPANCY", "CLARIFICATION_NEEDED", "BLOCKED"}
    claim = by_type["CLAIM_DISCREPANCY"][0]
    assert claim["subject"] == "requirement:REQ-003"
    assert {e["source_type"] for e in claim["evidence"]} == {"BRD", "CHAT"}

    tri = by_type["BRD_CODE_DISCREPANCY"][0]
    assert tri["scenario_id"] == "pay:SC-010"
    assert tri["evidence"][0]["source_type"] == "BRD"
    assert tri["evidence"][0]["content"].startswith("### REQ-001")
    assert {e["source_type"] for e in tri["evidence"][1:]} == {"CODE_AST"}

    clar = by_type["CLARIFICATION_NEEDED"][0]
    assert "REQ-004" in clar["question"] and clar["evidence"][0]["source_reference"].startswith("BRD.md#L")
    blocked = by_type["BLOCKED"][0]
    assert "Waiting on IAM roles" in blocked["question"]
    assert blocked["evidence"][0]["content"].startswith("- **REQ-003**")


def test_generate_agent_handoff_spec(loaded):
    spec = loaded.generate_agent_handoff_spec(["SC-010", "pay:SC-014"])
    assert spec.startswith("# Agent Handoff Spec: Payments Platform")
    assert "## SC-010: Capture customer email" in spec
    assert "## SC-014: Refund ledger export" in spec
    assert "> The system MUST capture a valid `email` for every customer at onboarding." in spec
    assert "constraint: `mandatory`" in spec
    assert "| `Customer.email` | field | `app/models.py#L" in spec
    assert "nullable=True" in spec
    assert "`DOCUMENTED_FACT`" in spec
    assert "Open Discrepancy" in spec
    assert "- [ ] Submitting the form without an email returns a validation error" in spec
    assert "- [ ] `Customer.email` (app/models.py) must reject missing/null values" in spec
    assert "_No linked BRD requirement on record._" in spec
    assert spec == loaded.generate_agent_handoff_spec(["SC-010", "pay:SC-014"])  # deterministic
    with pytest.raises(LookupError):
        loaded.generate_agent_handoff_spec(["SC-404"])


# --------------------------------------------------------------------------- #
# 5. CLI
# --------------------------------------------------------------------------- #
def test_cli_end_to_end(tmp_path, codebase, capsys):
    db_path = str(tmp_path / "mem.db")
    brd = tmp_path / "BRD.md"
    brd.write_text(BRD, encoding="utf-8")
    base = ["--db", db_path, "--project", "pay"]

    assert cli_main(base + ["init", "--name", "Payments Platform"]) == 0
    assert cli_main(base + ["ingest-brd", str(brd)]) == 0
    assert cli_main(base + ["scan-code", str(codebase)]) == 0
    with ProjectMemoryDB(db_path) as db:
        db.upsert_scenario("pay", {"scenario_num": 10, "title": "Capture customer email",
                                   "status": "Ready_For_Impl", "linked_req_id": "REQ-001",
                                   "code_symbols": ["Customer"]})
        db.upsert_scenario("pay", {"scenario_num": 11, "title": "Opt-in", "status": "Blocked",
                                   "blockers": "Legal review"})
    capsys.readouterr()

    assert cli_main(base + ["status"]) == 0
    out = capsys.readouterr().out
    assert "Scenarios: 2" in out and "SC-011 Opt-in [Blocked]: Legal review" in out

    assert cli_main(base + ["next"]) == 0
    assert "SC-010 [Ready_For_Impl] Capture customer email (REQ-001)" in capsys.readouterr().out

    assert cli_main(base + ["agenda"]) == 0
    out = capsys.readouterr().out
    assert "[BRD_CODE_DISCREPANCY]" in out and "Evidence: BRD BRD.md#L" in out

    assert cli_main(base + ["handoff", "--scenarios", "SC-010,SC-011"]) == 0
    out = capsys.readouterr().out
    assert "## SC-010: Capture customer email" in out and "## SC-011: Opt-in" in out

    assert cli_main(base + ["handoff", "--scenarios", "SC-999"]) == 1
    assert "not found" in capsys.readouterr().err
    assert cli_main(["--db", db_path, "--project", "ghost", "status"]) == 1


def test_sprint1_reconciler_still_quiet_on_agreeing_claim(loaded):
    """Regression: a claim matching the BRD fact text raises no warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        reconcile_chat_claim(loaded.db, "pay", {
            "text": "confirming dual approval",
            "subject_entity": subject_for("REQ-003"),
            "assertion": "Payments above $10,000 SHALL require two distinct approvers.",
        })
    assert loaded.get_project_status_report()["open_discrepancies"] == 0
