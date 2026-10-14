"""Conversational memory and deterministic consultant processor.

Integrates multi-thread chat turns with ProjectMemoryDB, reconcile_chat_claim,
and TechnicalConsultant.

Supports:
- Turn-level mode: 'project' (default) vs 'lightweight' (read-only query).
- Deterministic claim extraction from natural language (completions, blockers,
  requirements/approvals).
- Snapshot capture for 1-click reversible undo ribbon.
- Evidence-backed consultant replies with citations and discrepancy alerts.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from core.engine.consultant import TechnicalConsultant
from core.engine.reconciler import DiscrepancyWarning, default_contradicts, reconcile_chat_claim
from core.ingestion.brd_parser import subject_for
from core.storage.db import ProjectMemoryDB

SCENARIO_NUM_RE = re.compile(r"\b(?:SC[-_]?)?0*(\d+)\b", re.IGNORECASE)
REQ_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]*-\d+)\b")


def extract_scenario_nums(text: str) -> list[int]:
    """Find scenario numbers mentioned in text, e.g. 'SC-004 and SC-005' -> [4, 5]."""
    # Matches patterns like SC-004, SC-4, SC004, scenario 4
    nums: list[int] = []
    for m in re.finditer(r"(?:SC[-_]?|scenario\s*)0*(\d+)", text, re.IGNORECASE):
        val = int(m.group(1))
        if val not in nums:
            nums.append(val)
    return nums


def extract_claims_from_text(text: str) -> list[dict]:
    """Deterministic extractor for common developer / consultant update assertions."""
    claims: list[dict] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = " ".join(lines)

    # 1. Evidence tag if present in the text: e.g. (evidence EV-123) or evidence DIFF-9f3c
    ev_match = re.search(r"(?:evidence|evidence_id|proof)[:\s]+([A-Za-z0-9_\-:]+)", full_text, re.IGNORECASE)
    evidence_id = ev_match.group(1).strip("()") if ev_match else None

    # 2. Blockers: e.g. 'SC-006 is blocked on <reason>' or 'SC-006 blocked by <reason>'
    blocker_matches = re.finditer(
        r"(?:SC[-_]?|scenario\s*)0*(\d+)\s+(?:is\s+)?blocked\s+(?:on|by)\s+([^.,;\n]+)",
        full_text,
        re.IGNORECASE,
    )
    for bm in blocker_matches:
        sc_num = int(bm.group(1))
        reason = bm.group(2).strip()
        claims.append(
            {
                "type": "blocker",
                "scenario_num": sc_num,
                "blocker": reason,
                "text": bm.group(0),
            }
        )

    # 3. Completion claims: e.g. 'completed SC-004 and SC-005', 'SC-004 is done', 'finished SC-010'
    completion_prefixes = re.finditer(
        r"(?:completed|finished|shipped|implemented|resolved|closed)\s+([^.,;\n]+)",
        full_text,
        re.IGNORECASE,
    )
    for cp in completion_prefixes:
        nums = extract_scenario_nums(cp.group(1))
        for num in nums:
            claims.append(
                {
                    "type": "status",
                    "scenario_num": num,
                    "reported_status": "Verified" if evidence_id else "Done",
                    "verification_evidence_id": evidence_id,
                    "text": cp.group(0),
                }
            )

    completion_suffixes = re.finditer(
        r"((?:(?:SC[-_]?|scenario\s*)0*\d+(?:\s*(?:,|and)\s*)?)+)\s+(?:is|are)\s+(?:done|completed|finished|implemented|resolved|verified)",
        full_text,
        re.IGNORECASE,
    )
    for cs in completion_suffixes:
        nums = extract_scenario_nums(cs.group(1))
        for num in nums:
            # avoid duplicates
            if not any(c.get("scenario_num") == num and c.get("type") == "status" for c in claims):
                claims.append(
                    {
                        "type": "status",
                        "scenario_num": num,
                        "reported_status": "Verified" if evidence_id else "Done",
                        "verification_evidence_id": evidence_id,
                        "text": cs.group(0),
                    }
                )

    # 4. Requirement clarifications / decisions:
    # e.g. 'NSI confirmed REQ-001 is optional', 'PO approved REQ-001 is optional'
    req_matches = re.finditer(
        r"(?:([A-Za-z]+)\s+)?(?:confirmed|approved|decided)\s+(?:that\s+)?(REQ-\d+)\s+is\s+(mandatory|optional)(?:\s+for\s+([^.,;\n]+))?",
        full_text,
        re.IGNORECASE,
    )
    for rm in req_matches:
        stakeholder = (rm.group(1) or "Stakeholder").strip()
        req_id = rm.group(2).upper()
        constraint = rm.group(3).lower()
        scope = rm.group(4).strip() if rm.group(4) else ""
        assertion_text = f"{req_id} is {constraint}" + (f" for {scope}" if scope else "")
        is_approval = bool(re.search(r"approved|confirmed by po|confirmed by client", rm.group(0), re.IGNORECASE))
        claims.append(
            {
                "type": "requirement_decision",
                "req_id": req_id,
                "subject_entity": subject_for(req_id),
                "assertion": assertion_text,
                "constraint": constraint,
                "speaker": stakeholder,
                "is_approval": is_approval,
                "text": rm.group(0),
            }
        )

    return claims


def process_chat_turn(
    db: ProjectMemoryDB,
    project_id: str,
    thread_id: str,
    user_message: str,
    mode: Optional[str] = None,
    claims: Optional[list[dict]] = None,
) -> dict:
    """Process a single conversational turn against ProjectMemoryDB.

    - Records the user message in `messages`.
    - In 'project' mode: applies mutations with pre-mutation snapshots.
    - In 'lightweight' mode: answers read-only without mutations.
    - Uses TechnicalConsultant to generate evidence-backed replies and citations.
    - Stores the assistant message with mutations and citations, enabling undo.
    """
    db.require_project(project_id)
    thread = db.get_thread(project_id, thread_id)
    if not thread:
        raise LookupError(f"Thread {thread_id!r} not found in project {project_id!r}")

    active_mode = mode or thread.get("mode") or "project"
    if active_mode not in ("project", "lightweight"):
        raise ValueError(f"Invalid mode {active_mode!r}; must be 'project' or 'lightweight'")

    user_msg = db.add_message(
        project_id=project_id,
        thread_id=thread_id,
        role="user",
        content=user_message,
    )

    consultant = TechnicalConsultant(db, project_id)
    citations: list[dict] = []
    discrepancies: list[dict] = []
    mutation_summaries: list[str] = []

    # Snapshot containers for 1-click reversible undo
    scenario_snapshots: dict[str, dict] = {}
    created_decisions: list[str] = []
    superseded_decisions: list[dict] = []

    def snapshot_scenario(sc_id: str):
        if sc_id not in scenario_snapshots:
            curr = db.get_scenario_by_id(project_id, sc_id)
            scenario_snapshots[sc_id] = {
                "scenario_id": sc_id,
                "existed": curr is not None,
                "previous": dict(curr) if curr else None,
            }

    if active_mode == "project":
        # Combine explicitly passed claims and natural language extractions
        all_claims = list(claims or [])
        extracted = extract_claims_from_text(user_message)
        for ec in extracted:
            # Avoid redundant duplicates if explicit claim already addresses same scenario/req
            already = False
            for ac in all_claims:
                if ec.get("scenario_num") and ac.get("scenario_num") == ec.get("scenario_num"):
                    already = True
                    break
                if ec.get("req_id") and ac.get("req_id") == ec.get("req_id"):
                    already = True
                    break
            if not already:
                all_claims.append(ec)

        # Apply claims deterministically
        for c in all_claims:
            ctype = c.get("type")

            # 1. Blocker updates
            if ctype == "blocker" or c.get("blocker"):
                num = c.get("scenario_num")
                if num is not None:
                    sc = db.get_scenario(project_id, int(num))
                    if sc:
                        snapshot_scenario(sc["id"])
                        db.upsert_scenario(
                            project_id,
                            {
                                "id": sc["id"],
                                "scenario_num": sc["scenario_num"],
                                "status": "Blocked",
                                "blockers": c.get("blocker"),
                            },
                        )
                        mutation_summaries.append(
                            f"Marked SC-{sc['scenario_num']:03d} Blocked: {c.get('blocker')}"
                        )

            # 2. Status claims (e.g. Done, Verified)
            elif ctype == "status" or c.get("reported_status"):
                num = c.get("scenario_num")
                if num is not None:
                    sc = db.get_scenario(project_id, int(num))
                    if sc:
                        snapshot_scenario(sc["id"])
                        res = reconcile_chat_claim(
                            db,
                            project_id,
                            {
                                "text": c.get("text") or user_message,
                                "speaker": c.get("speaker") or "developer",
                                "scenario_num": int(num),
                                "reported_status": c.get("reported_status") or "Done",
                                "verification_evidence_id": c.get("verification_evidence_id"),
                                "source_reference": f"thread:{thread_id}",
                            },
                            emit_warnings=False,
                        )
                        applied = res["applied_status"]
                        note = " (Verified with evidence)" if res.get("verified") else " (Capped at Impl_Reported)"
                        mutation_summaries.append(
                            f"Updated SC-{sc['scenario_num']:03d} to {applied}{note}"
                        )
                        if res.get("discrepancies"):
                            discrepancies.extend(res["discrepancies"])

            # 3. Requirement / Decision updates
            elif ctype == "requirement_decision" or c.get("subject_entity"):
                subject = c.get("subject_entity") or subject_for(c.get("req_id", "GENERAL"))
                assertion = c.get("assertion") or user_message
                is_approval = c.get("is_approval", False)
                speaker = c.get("speaker") or "stakeholder"

                # Record raw chat evidence in evidence_vault
                ev_id = f"CHAT-TURN-{uuid.uuid4().hex[:12]}"
                db.add_evidence(
                    project_id=project_id,
                    evidence_id=ev_id,
                    source_type="CHAT",
                    source_reference=f"thread:{thread_id}#{user_msg['message_id']}",
                    content=c.get("text") or user_message,
                )

                # Snapshot active decisions before superseding
                active_before = db.get_decisions(project_id, subject, active_only=True)
                for ab in active_before:
                    superseded_decisions.append(
                        {
                            "decision_id": ab["id"],
                            "prev_valid_to": ab["valid_to"],
                            "prev_superseded_by": ab["superseded_by"],
                        }
                    )

                if is_approval:
                    dec_id = db.record_decision(
                        project_id=project_id,
                        subject_entity=subject,
                        decision_text=assertion,
                        epistemic_status="APPROVED_DECISION",
                        source_evidence_id=ev_id,
                        rationale=f"Approved decision recorded via thread {thread_id}",
                        created_by=speaker,
                    )
                    created_decisions.append(dec_id)
                    mutation_summaries.append(
                        f"Recorded APPROVED_DECISION on '{subject}': {assertion}"
                    )
                else:
                    res = reconcile_chat_claim(
                        db,
                        project_id,
                        {
                            "text": c.get("text") or user_message,
                            "speaker": speaker,
                            "subject_entity": subject,
                            "assertion": assertion,
                            "source_reference": f"thread:{thread_id}",
                        },
                        emit_warnings=False,
                    )
                    if res.get("claim_decision_id"):
                        created_decisions.append(res["claim_decision_id"])
                    mutation_summaries.append(
                        f"Recorded REPORTED_CLAIM on '{subject}': {assertion}"
                    )
                    if res.get("discrepancies"):
                        discrepancies.extend(res["discrepancies"])

    # Generate Evidence-Backed Assistant Reply
    status_report = consultant.get_project_status_report()
    next_scenarios = consultant.get_next_scenarios(limit=5)
    agenda = consultant.get_stakeholder_agenda()

    query_lower = user_message.lower()
    reply_sections: list[str] = []

    # 1. Acknowledge memory mutations if any took place
    if mutation_summaries:
        reply_sections.append("### Project Memory Updated\n")
        for m in mutation_summaries:
            reply_sections.append(f"- {m}")
        reply_sections.append("")

    if discrepancies:
        reply_sections.append("### Discrepancy Flagged\n")
        for d in discrepancies:
            reply_sections.append(
                f"- **{d['subject_entity']}**: Claim *\"{d['claim_text']}\"* contradicts documented fact *\"{d['fact_text']}\"*."
            )
        reply_sections.append("")

    # 2. Tailored content based on user query intent
    if any(k in query_lower for k in ("handoff", "agent spec", "generate spec")):
        sc_nums = extract_scenario_nums(user_message)
        if not sc_nums:
            sc_nums = [s["scenario_num"] for s in next_scenarios[:2]]
        spec_refs = [f"SC-{n:03d}" for n in sc_nums]
        if spec_refs:
            spec_md = consultant.generate_agent_handoff_spec(spec_refs)
            reply_sections.append(spec_md)
        else:
            reply_sections.append("No actionable scenarios available for handoff spec generation.")

    elif any(k in query_lower for k in ("agenda", "question", "discrepanc", "nsi", "stakeholder")):
        reply_sections.append("### Stakeholder Agenda & Clarifications\n")
        if agenda:
            for idx, item in enumerate(agenda[:5], 1):
                reply_sections.append(f"**{idx}. [{item['type']}] {item['subject']}**\n- {item['question']}")
                for ev in item.get("evidence", [])[:1]:
                    citations.append(ev)
                    reply_sections.append(f"  *Evidence:* `{ev['source_type']}` ({ev['source_reference']})")
        else:
            reply_sections.append("No open discrepancies or pending stakeholder questions. All scenarios are clear.")

    elif any(k in query_lower for k in ("next", "what to do", "what should i work on", "recommend")):
        reply_sections.append("### Recommended Next Scenarios\n")
        if next_scenarios:
            for s in next_scenarios:
                req_badge = f" [Req: `{s['linked_req_id']}`]" if s.get("linked_req_id") else ""
                reply_sections.append(f"- **SC-{s['scenario_num']:03d}** (`{s['status']}`): {s['title']}{req_badge}")
                # Attach requirement citation if present
                req = db.get_requirement(project_id, s.get("linked_req_id"))
                if req:
                    cite = db.get_evidence(project_id, req.get("evidence_id"))
                    if cite and cite not in citations:
                        citations.append(cite)
        else:
            reply_sections.append("No unblocked scenarios ready for implementation.")

    elif any(k in query_lower for k in ("status", "current state", "overview", "progress", "summary")):
        reply_sections.append("### Project Status Overview\n")
        reply_sections.append(
            f"- **Total Scenarios:** {status_report['total_scenarios']}\n"
            f"- **Verified Complete:** {status_report['verified_pct']}%\n"
            f"- **Breakdown:** {status_report['by_status']}\n"
            f"- **Active Blockers:** {len(status_report['active_blockers'])}\n"
            f"- **Requirements:** {status_report['requirements']}\n"
            f"- **Open Discrepancies:** {status_report['open_discrepancies']}"
        )
        if status_report["active_blockers"]:
            reply_sections.append("\n**Current Blockers:**")
            for b in status_report["active_blockers"]:
                reply_sections.append(f"- {b['scenario']} ({b['title']}): {b['blockers']}")

    else:
        # Default response combining status summary & next step
        reply_sections.append("### Consultant Status & Recommendations\n")
        reply_sections.append(
            f"The project has **{status_report['total_scenarios']} scenarios** "
            f"({status_report['verified_pct']}% verified, {status_report['by_status'].get('Impl_Reported', 0)} implementation reported, "
            f"{status_report['by_status'].get('Blocked', 0)} blocked)."
        )
        if next_scenarios:
            top = next_scenarios[0]
            reply_sections.append(
                f"\n**Next recommended priority:** SC-{top['scenario_num']:03d} - *{top['title']}* (`{top['status']}`)."
            )

    reply = "\n".join(reply_sections).strip()

    # Package mutations for undo ribbon
    mutations_payload = None
    if active_mode == "project" and (scenario_snapshots or created_decisions or superseded_decisions or mutation_summaries):
        mutations_payload = {
            "summary": mutation_summaries,
            "scenarios": list(scenario_snapshots.values()),
            "created_decisions": created_decisions,
            "superseded_decisions": superseded_decisions,
        }

    asst_msg = db.add_message(
        project_id=project_id,
        thread_id=thread_id,
        role="assistant",
        content=reply,
        mutations_json=mutations_payload,
        citations_json=citations,
    )

    return {
        "user_message_id": user_msg["message_id"],
        "assistant_message_id": asst_msg["message_id"],
        "reply": reply,
        "mutations": mutation_summaries,
        "discrepancies": discrepancies,
        "citations": citations,
    }
