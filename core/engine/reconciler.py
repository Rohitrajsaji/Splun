"""Chat-claim reconciliation: controlled authority + discrepancy detection.

A chat message is *evidence that someone said something*, never proof that it
is true. ``reconcile_chat_claim`` therefore:

a) stores the raw message verbatim in ``evidence_vault`` (source_type CHAT);
b) applies a status update to a scenario, but caps any "it's done" claim at
   ``Impl_Reported`` unless it cites GIT_DIFF / TEST_LOG evidence that actually
   exists in this project's vault;
c) records the assertion as a ``REPORTED_CLAIM`` and, if it contradicts an
   active ``DOCUMENTED_FACT`` on the same subject, raises a
   ``DiscrepancyWarning`` while keeping *both* records active.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from typing import Callable, Optional

from core.storage.db import COMPLETION_STATUSES, ProjectMemoryDB, normalize_status

ALLOWED_CLAIM_KEYS = frozenset(
    {
        "text",                      # raw conversation text (required)
        "speaker",                   # who said it
        "source_reference",          # e.g. 'slack:#proj-alpha/1697012345.000100'
        "evidence_id",               # optional explicit id for the chat evidence
        "byte_range",                # optional byte span within the source
        "scenario_num",              # scenario the claim is about
        "reported_status",           # free-text status, e.g. 'Done'
        "verification_evidence_id",  # GIT_DIFF / TEST_LOG evidence id
        "subject_entity",            # entity the assertion is about
        "assertion",                 # normalised claim text (defaults to text)
        "rationale",
    }
)


class DiscrepancyWarning(UserWarning):
    """A reported claim contradicts an active documented fact."""


def _normalize_text(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def default_contradicts(fact_text: str, claim_text: str) -> bool:
    """Conservative deterministic comparator: any claim whose normalised text
    differs from the documented fact is flagged for human review."""
    return _normalize_text(fact_text) != _normalize_text(claim_text)


def reconcile_chat_claim(
    db: ProjectMemoryDB,
    project_id: str,
    claim: dict,
    *,
    contradicts: Callable[[str, str], bool] = default_contradicts,
    emit_warnings: bool = True,
) -> dict:
    unknown = set(claim) - ALLOWED_CLAIM_KEYS
    if unknown:
        raise ValueError(f"Unknown claim fields: {sorted(unknown)}")
    text = claim.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("claim['text'] (raw conversation text) is required")
    db.require_project(project_id)

    speaker: Optional[str] = claim.get("speaker")
    source_reference = claim.get("source_reference") or f"chat:{speaker or 'unknown'}"
    evidence_id = claim.get("evidence_id") or (
        "CHAT-"
        + hashlib.sha256(f"{project_id}\x1f{source_reference}\x1f{text}".encode("utf-8")).hexdigest()[:16]
    )

    result: dict = {
        "evidence_id": evidence_id,
        "scenario_id": None,
        "requested_status": None,
        "applied_status": None,
        "verified": False,
        "claim_decision_id": None,
        "discrepancies": [],
        "notes": [],
    }

    with db.transaction():
        # (a) verbatim conversation evidence
        db.add_evidence(project_id, evidence_id, "CHAT", source_reference, text, claim.get("byte_range"))

        # (b) controlled-authority status update
        if claim.get("reported_status") is not None:
            if claim.get("scenario_num") is None:
                raise ValueError("reported_status requires scenario_num")
            scenario = db.get_scenario(project_id, claim["scenario_num"])
            if scenario is None:
                result["notes"].append(
                    f"Scenario #{claim['scenario_num']} not found; status claim recorded as evidence only"
                )
            else:
                requested = normalize_status(claim["reported_status"])
                result["requested_status"] = requested
                result["scenario_id"] = scenario["id"]
                update: dict = {"scenario_num": scenario["scenario_num"]}

                if requested in COMPLETION_STATUSES:
                    ver_id = claim.get("verification_evidence_id")
                    if db.is_valid_verification_evidence(project_id, ver_id):
                        applied = "Verified"
                        update["verification_evidence_id"] = ver_id
                        result["verified"] = True
                    else:
                        applied = "Impl_Reported"
                        result["notes"].append(
                            "Completion reported without valid GIT_DIFF/TEST_LOG verification "
                            "evidence; status capped at Impl_Reported"
                        )
                        if scenario["status"] == "Verified":
                            applied = "Verified"  # unproven claim never downgrades proof
                            result["verified"] = True
                else:
                    applied = requested

                update["status"] = applied
                db.upsert_scenario(project_id, update)
                result["applied_status"] = applied

        # (c) epistemic record + discrepancy detection
        subject = claim.get("subject_entity")
        if subject:
            assertion = claim.get("assertion") or text
            active_facts = [
                d
                for d in db.get_decisions(project_id, subject, active_only=True)
                if d["epistemic_status"] == "DOCUMENTED_FACT"
            ]
            # A claim may only supersede earlier claims, never a documented fact.
            claim_id = db.record_decision(
                project_id,
                subject,
                assertion,
                "REPORTED_CLAIM",
                evidence_id,
                rationale=claim.get("rationale"),
                created_by=speaker,
                supersede_statuses={"REPORTED_CLAIM"},
            )
            result["claim_decision_id"] = claim_id
            for fact in active_facts:
                if contradicts(fact["decision_text"], assertion):
                    result["discrepancies"].append(
                        {
                            "subject_entity": subject,
                            "fact_decision_id": fact["id"],
                            "fact_text": fact["decision_text"],
                            "fact_evidence_id": fact["source_evidence_id"],
                            "claim_decision_id": claim_id,
                            "claim_text": assertion,
                            "claim_evidence_id": evidence_id,
                        }
                    )

    # Emit after commit so a warnings-as-errors filter can't roll back the record.
    if emit_warnings:
        for d in result["discrepancies"]:
            warnings.warn(
                f"[{project_id}] REPORTED_CLAIM {d['claim_decision_id']} on '{d['subject_entity']}' "
                f"contradicts DOCUMENTED_FACT {d['fact_decision_id']}: "
                f"fact={d['fact_text']!r} vs claim={d['claim_text']!r}",
                DiscrepancyWarning,
                stacklevel=2,
            )
    return result


def find_active_discrepancies(
    db: ProjectMemoryDB,
    project_id: str,
    *,
    contradicts: Callable[[str, str], bool] = default_contradicts,
) -> list[dict]:
    """Recompute open discrepancies from the decision graph: every active
    REPORTED_CLAIM that contradicts an active DOCUMENTED_FACT on the same
    subject. Resolving one (e.g. via an APPROVED_DECISION that supersedes the
    records, or a newer agreeing claim) removes it from this list."""
    active = db.get_decisions(project_id, active_only=True)
    by_subject: dict[str, list[dict]] = {}
    for d in active:
        by_subject.setdefault(d["subject_entity"], []).append(d)

    found: list[dict] = []
    for subject in sorted(by_subject):
        records = by_subject[subject]
        facts = [d for d in records if d["epistemic_status"] == "DOCUMENTED_FACT"]
        claims = [d for d in records if d["epistemic_status"] == "REPORTED_CLAIM"]
        for fact in facts:
            for claim in claims:
                if contradicts(fact["decision_text"], claim["decision_text"]):
                    found.append(
                        {
                            "subject_entity": subject,
                            "fact_decision_id": fact["id"],
                            "fact_text": fact["decision_text"],
                            "fact_evidence_id": fact["source_evidence_id"],
                            "claim_decision_id": claim["id"],
                            "claim_text": claim["decision_text"],
                            "claim_evidence_id": claim["source_evidence_id"],
                            "claimed_by": claim["created_by"],
                        }
                    )
    return found
