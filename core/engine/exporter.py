"""Portable .ai-memory/ Artifact Compiler.

Deterministically exports the project memory state into three portable Markdown files:
1. .ai-memory/PROJECT_STATE.md: Live tracker, progress metrics, blockers, next priorities.
2. .ai-memory/DECISION_LOG.md: Bi-temporal decisions grouped by epistemic status with evidence citations.
3. .ai-memory/SKILL.md: Standing instructions, guardrails, and active discrepancy warnings for external coding agents.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.engine.consultant import TechnicalConsultant
from core.engine.reconciler import find_active_discrepancies
from core.storage.db import ProjectMemoryDB

EPISTEMIC_ORDER = (
    "DOCUMENTED_FACT",
    "APPROVED_DECISION",
    "VERIFIED_OUTCOME",
    "REPORTED_CLAIM",
    "INFERENCE",
    "PROPOSAL",
)


def _format_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def generate_project_state_md(consultant: TechnicalConsultant, project: dict) -> str:
    sr = consultant.get_project_status_report()
    next_scs = consultant.get_next_scenarios(limit=10)

    lines = [
        f"# Project State: {project['name']}",
        "",
        f"- **Project ID:** `{project['project_id']}`",
        f"- **Repository Path:** `{project.get('repo_path') or 'none'}`",
        f"- **Generated At:** `{_format_timestamp()}`",
        "",
        "## Executive Summary",
        "",
        f"- **Total Scenarios:** {sr['total_scenarios']}",
        f"- **Verified Complete:** {sr['verified_pct']}%",
        f"- **Requirements On Record:** {sr['requirements']}",
        f"- **Active Blockers:** {len(sr['active_blockers'])}",
        f"- **Open Discrepancies:** {sr['open_discrepancies']}",
        "",
        "### Scenario Status Breakdown",
        "",
        "| Status | Count | Proportion |",
        "|---|---|---|",
    ]

    total = sr["total_scenarios"] or 1
    for st, count in sr["by_status"].items():
        pct = round(100.0 * count / total, 1)
        lines.append(f"| `{st}` | {count} | {pct}% |")

    lines += [
        "",
        "## Active Blockers",
        "",
    ]
    if sr["active_blockers"]:
        lines += [
            "| Scenario | Title | Status | Blocker Reason | Linked Req |",
            "|---|---|---|---|---|",
        ]
        for b in sr["active_blockers"]:
            lines.append(
                f"| `{b['scenario']}` | {b['title']} | `{b['status']}` | {b['blockers'] or '-'} | `{b.get('linked_req_id') or '-'}` |"
            )
    else:
        lines.append("_No active blockers recorded._")

    lines += [
        "",
        "## Next Recommended Scenarios",
        "",
    ]
    if next_scs:
        lines += [
            "| Scenario | Priority | Status | Linked Req | Title |",
            "|---|---|---|---|---|",
        ]
        for idx, s in enumerate(next_scs, 1):
            sc_label = f"SC-{int(s['scenario_num']):03d}"
            lines.append(
                f"| `{sc_label}` | #{idx} | `{s['status']}` | `{s.get('linked_req_id') or '-'}` | {s['title']} |"
            )
    else:
        lines.append("_No unblocked scenarios ready for immediate implementation._")

    lines += [
        "",
        "## Scenario Ledger",
        "",
        "| Scenario | Title | Status | Linked Req | Blockers | Verification Evidence |",
        "|---|---|---|---|---|---|",
    ]
    scenarios = consultant.db.get_scenarios(project["project_id"])
    for s in scenarios:
        sc_label = f"SC-{int(s['scenario_num']):03d}"
        lines.append(
            f"| `{sc_label}` | {s['title']} | `{s['status']}` | `{s.get('linked_req_id') or '-'}` | "
            f"{s.get('blockers') or '-'} | `{s.get('verification_evidence_id') or '-'}` |"
        )

    return "\n".join(lines).rstrip() + "\n"


def generate_decision_log_md(db: ProjectMemoryDB, project_id: str, project: dict) -> str:
    decisions = db.get_decisions(project_id, active_only=True)

    by_status: dict[str, list[dict]] = {}
    for d in decisions:
        by_status.setdefault(d["epistemic_status"], []).append(d)

    lines = [
        f"# Epistemic Decision Log: {project['name']}",
        "",
        f"- **Project ID:** `{project_id}`",
        f"- **Active Decisions:** {len(decisions)}",
        f"- **Generated At:** `{_format_timestamp()}`",
        "",
        "> Bi-temporal epistemic memory ledger. Every documented fact, reported claim, and "
        "approved decision cites its immutable evidence vault source.",
        "",
    ]

    for status_key in EPISTEMIC_ORDER:
        group = by_status.get(status_key, [])
        if not group:
            continue

        lines += [
            f"## {status_key.replace('_', ' ').title()}s ({len(group)})",
            "",
            "| Subject Entity | Decision / Assertion | Rationale | Created By | Source Reference | Evidence ID |",
            "|---|---|---|---|---|---|",
        ]
        for d in group:
            ev = db.get_evidence(project_id, d["source_evidence_id"])
            source_ref = ev["source_reference"] if ev else d["source_evidence_id"]
            rationale = (d.get("rationale") or "-").replace("\n", " ")
            lines.append(
                f"| `{d['subject_entity']}` | {d['decision_text']} | {rationale} | "
                f"`{d.get('created_by') or 'unknown'}` | `{source_ref}` | `{d['source_evidence_id']}` |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate_skill_md(
    db: ProjectMemoryDB,
    project_id: str,
    project: dict,
    consultant: TechnicalConsultant,
) -> str:
    discrepancies = find_active_discrepancies(db, project_id)
    agenda = consultant.get_stakeholder_agenda()
    sr = consultant.get_project_status_report()

    lines = [
        "---",
        f"name: {project_id}-consultant-guardrails",
        f"description: Standing guardrails, epistemic authority rules, and discrepancy warnings for {project['name']}.",
        "---",
        "",
        f"# Standing Instructions: {project['name']} Agent Guardrails",
        "",
        "## Core Principles for Coding Agents",
        "1. **Code Beats Model:** Do not guess or assume requirement intent. Always consult `.ai-memory/DECISION_LOG.md`.",
        "2. **Controlled Authority Gate:** A task or scenario is NEVER considered complete without a verified test run or git commit diff. Unverified claims remain capped at `Impl_Reported`.",
        "3. **Evidence Citations:** When changing schemas or logic, always refer to the specific requirement ID (e.g., `REQ-001`).",
        "",
    ]

    lines += [
        "## Active Discrepancy Warnings (Do Not Violate)",
        "",
    ]

    triangulation_discrepancies = [item for item in agenda if item.get("type") == "BRD_CODE_DISCREPANCY"]
    if discrepancies or triangulation_discrepancies:
        lines.append(
            "> [!CAUTION]\n"
            "> The following requirements or code symbols have active unresolved discrepancies. "
            "Implement to the documented BRD fact unless an approved deviation exists. Do NOT invent middle grounds."
        )
        lines.append("")

        for idx, d in enumerate(discrepancies, 1):
            lines += [
                f"### {idx}. Conflict on `{d['subject_entity']}`",
                f"- **Documented Fact:** {d['fact_text']} (evidence `{d['fact_evidence_id']}`)",
                f"- **Contradicting Claim:** {d['claim_text']} (by `{d.get('claimed_by') or 'unknown'}`)",
                "- **Agent Instruction:** Follow the Documented Fact until an APPROVED_DECISION is formally recorded.",
                "",
            ]

        for idx, t in enumerate(triangulation_discrepancies, len(discrepancies) + 1):
            lines += [
                f"### {idx}. BRD vs Code AST Mismatch on `{t['subject']}`",
                f"- **Issue:** {t['question']}",
                "- **Agent Instruction:** Do not relax mandatory constraints in code to nullable without explicit stakeholder sign-off.",
                "",
            ]
    else:
        lines.append("_No open requirement discrepancies detected. Project memory is consistent._\n")

    lines += [
        "## Current Project Snapshot",
        f"- Scenarios: {sr['total_scenarios']} total ({sr['verified_pct']}% verified)",
        f"- Blockers: {len(sr['active_blockers'])} active",
        "",
    ]

    return "\n".join(lines).rstrip() + "\n"


def export_portable_memory_bundle(
    db: ProjectMemoryDB,
    project_id: str,
    output_dir: str | Path,
) -> dict:
    """Compile and export `.ai-memory/` bundle into output_dir."""
    db.require_project(project_id)
    project = db.get_project(project_id)
    consultant = TechnicalConsultant(db, project_id)

    target_dir = Path(output_dir).resolve() / ".ai-memory"
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Compile PROJECT_STATE.md
    state_content = generate_project_state_md(consultant, project)
    state_file = target_dir / "PROJECT_STATE.md"
    state_file.write_text(state_content, encoding="utf-8")

    # 2. Compile DECISION_LOG.md
    decision_content = generate_decision_log_md(db, project_id, project)
    decision_file = target_dir / "DECISION_LOG.md"
    decision_file.write_text(decision_content, encoding="utf-8")

    # 3. Compile SKILL.md
    skill_content = generate_skill_md(db, project_id, project, consultant)
    skill_file = target_dir / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    return {
        "output_dir": str(target_dir),
        "files": {
            "PROJECT_STATE.md": str(state_file),
            "DECISION_LOG.md": str(decision_file),
            "SKILL.md": str(skill_file),
        },
        "stats": {
            "scenarios": len(db.get_scenarios(project_id)),
            "active_decisions": len(db.get_decisions(project_id, active_only=True)),
            "discrepancies": len(find_active_discrepancies(db, project_id)),
        },
    }
