"""Autonomous Project Coordinator Engine.

Analyzes the relational task ledger, BRD requirements, code AST symbols, and epistemic
decision graph to:
1. Build deterministic Execution Batches clustered by shared root cause (linked requirement
   or overlapping code symbols) so coding agents can implement related scenarios together.
2. Isolate ambiguous, blocked, or contradictory scenarios into an Escalations queue with
   exact stakeholder questions and evidence citations, guaranteeing agents never guess.
3. Prepare self-contained worktree execution packets and promote scenarios to 'Verified'
   only upon exit-code-0 test execution backed by immutable TEST_LOG evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from core.engine.consultant import TechnicalConsultant
from core.engine.exporter import export_portable_memory_bundle
from core.engine.reconciler import find_active_discrepancies
from core.storage.db import COMPLETION_STATUSES, ProjectMemoryDB


class AutonomousCoordinator:
    def __init__(self, db: ProjectMemoryDB, project_id: str):
        db.require_project(project_id)
        self.db = db
        self.project_id = project_id
        self.consultant = TechnicalConsultant(db, project_id)

    def build_execution_plan(self) -> dict:
        """Analyze all scenarios and partition into completed, escalations, and batches."""
        scenarios = self.db.get_scenarios(self.project_id)
        discrepancies = find_active_discrepancies(self.db, self.project_id)

        completed: list[dict] = []
        escalations: list[dict] = []
        candidates: list[dict] = []

        for s in scenarios:
            sc_id = s["id"]
            status = s["status"]

            # 1. Already verified
            if status == "Verified":
                completed.append(
                    {
                        "scenario_id": sc_id,
                        "scenario_num": s["scenario_num"],
                        "title": s["title"],
                        "status": status,
                        "verification_evidence_id": s.get("verification_evidence_id"),
                    }
                )
                continue

            # 2. Check for active blockers or clarification needed
            if status == "Blocked" or (s.get("blockers") and s.get("blockers").strip()):
                escalations.append(
                    {
                        "scenario_id": sc_id,
                        "scenario_num": s["scenario_num"],
                        "title": s["title"],
                        "status": "Blocked",
                        "reason": f"Active blocker: {s.get('blockers') or 'Status set to Blocked'}",
                        "stakeholder_question": f"SC-{s['scenario_num']:03d} '{s['title']}' is blocked by: {s.get('blockers') or 'Dependencies'}. Who owns unblocking this?",
                        "evidence_id": None,
                    }
                )
                continue

            if status == "Clarification_Needed":
                escalations.append(
                    {
                        "scenario_id": sc_id,
                        "scenario_num": s["scenario_num"],
                        "title": s["title"],
                        "status": "Clarification_Needed",
                        "reason": "Clarification explicitly requested on scenario",
                        "stakeholder_question": f"SC-{s['scenario_num']:03d} '{s['title']}' requires requirement clarification before work can proceed.",
                        "evidence_id": None,
                    }
                )
                continue

            # 3. Check triangulation for BRD/code discrepancy
            tri = self.consultant.triangulate_scenario(sc_id)
            if tri.get("discrepancy_detected"):
                conflicts = tri.get("conflicts", [])
                conflict_summary = (
                    f"Code symbol '{conflicts[0]['symbol']}' is {conflicts[0]['code_indicates']} but BRD requires {conflicts[0]['brd_constraint']}"
                    if conflicts
                    else "Discrepancy detected between BRD requirement and implementation"
                )
                questions = tri.get("clarification_questions", [])
                q_text = " ".join(questions) if questions else f"Please resolve conflict between BRD and code for {s['linked_req_id']}."
                evidence_id = tri["requirement_evidence"][0]["evidence_id"] if tri.get("requirement_evidence") else None

                escalations.append(
                    {
                        "scenario_id": sc_id,
                        "scenario_num": s["scenario_num"],
                        "title": s["title"],
                        "status": "Contradiction_Detected",
                        "reason": conflict_summary,
                        "stakeholder_question": q_text,
                        "evidence_id": evidence_id,
                        "conflicts": conflicts,
                    }
                )
                continue

            # 4. Check if scenario is subject to an active claim discrepancy
            has_claim_discrepancy = False
            if s.get("linked_req_id"):
                req_subject = f"requirement:{s['linked_req_id']}"
                for d in discrepancies:
                    if d.get("subject_entity") == req_subject:
                        escalations.append(
                            {
                                "scenario_id": sc_id,
                                "scenario_num": s["scenario_num"],
                                "title": s["title"],
                                "status": "Claim_Discrepancy",
                                "reason": f"Active contradiction on {s['linked_req_id']}: claim '{d['claim_text']}' vs fact '{d['fact_text']}'",
                                "stakeholder_question": f"Should {s['linked_req_id']} follow fact '{d['fact_text']}' or reported claim '{d['claim_text']}'?",
                                "evidence_id": d.get("fact_evidence_id"),
                            }
                        )
                        has_claim_discrepancy = True
                        break

            if has_claim_discrepancy:
                continue

            # Unblocked and unambiguous -> Ready candidate for execution batching
            candidates.append(s)

        # Cluster candidates into Execution Batches by shared root cause
        # Strategy:
        # Priority A: Group by linked_req_id (e.g., REQ-001, REQ-002)
        # Priority B: Group by primary code_symbol if no linked_req_id
        # Priority C: Group remaining into a general batch
        req_groups: dict[str, list[dict]] = {}
        sym_groups: dict[str, list[dict]] = {}
        unlinked: list[dict] = []

        for s in candidates:
            req_id = s.get("linked_req_id")
            symbols = s.get("code_symbols")
            if req_id and req_id.strip():
                req_groups.setdefault(req_id.strip(), []).append(s)
            elif symbols:
                sym_list = symbols if isinstance(symbols, list) else [s.strip() for s in symbols.split(",") if s.strip()]
                if sym_list:
                    primary_sym = sym_list[0]
                    sym_groups.setdefault(primary_sym, []).append(s)
                else:
                    unlinked.append(s)
            else:
                unlinked.append(s)

        batches: list[dict] = []

        # 1. Batches from shared requirements
        for req_id, group in sorted(req_groups.items()):
            req_data = self.db.get_requirement(self.project_id, req_id)
            req_title = req_data.get("title") if req_data else req_id
            b_id = f"BATCH-{req_id.replace('-', '')}"
            batches.append(
                {
                    "batch_id": b_id,
                    "title": f"Batch: {req_id} - {req_title}",
                    "root_cause_type": "linked_requirement",
                    "root_cause_key": req_id,
                    "scenario_count": len(group),
                    "scenario_ids": [g["id"] for g in group],
                    "scenarios": [
                        {
                            "id": g["id"],
                            "scenario_num": g["scenario_num"],
                            "title": g["title"],
                            "status": g["status"],
                        }
                        for g in group
                    ],
                    "recommended_action": f"Implement shared business rule {req_id} ({req_title}) across {len(group)} scenarios",
                }
            )

        # 2. Batches from shared code symbols
        for sym_name, group in sorted(sym_groups.items()):
            clean_sym = sym_name.replace(".", "_")
            b_id = f"BATCH-SYM-{clean_sym.upper()[:16]}"
            batches.append(
                {
                    "batch_id": b_id,
                    "title": f"Batch: Shared Symbol '{sym_name}'",
                    "root_cause_type": "code_symbol",
                    "root_cause_key": sym_name,
                    "scenario_count": len(group),
                    "scenario_ids": [g["id"] for g in group],
                    "scenarios": [
                        {
                            "id": g["id"],
                            "scenario_num": g["scenario_num"],
                            "title": g["title"],
                            "status": g["status"],
                        }
                        for g in group
                    ],
                    "recommended_action": f"Update shared component '{sym_name}' across {len(group)} related scenarios",
                }
            )

        # 3. Unlinked / General scenarios clustered in chunks of up to 10
        if unlinked:
            chunk_size = 10
            for i in range(0, len(unlinked), chunk_size):
                chunk = unlinked[i : i + chunk_size]
                chunk_num = (i // chunk_size) + 1
                b_id = f"BATCH-GENERAL-{chunk_num:02d}"
                batches.append(
                    {
                        "batch_id": b_id,
                        "title": f"Batch: General Tasks #{chunk_num}",
                        "root_cause_type": "standalone",
                        "root_cause_key": f"general-{chunk_num}",
                        "scenario_count": len(chunk),
                        "scenario_ids": [g["id"] for g in chunk],
                        "scenarios": [
                            {
                                "id": g["id"],
                                "scenario_num": g["scenario_num"],
                                "title": g["title"],
                                "status": g["status"],
                            }
                            for g in chunk
                        ],
                        "recommended_action": f"Execute standalone tasks across {len(chunk)} scenarios",
                    }
                )

        return {
            "project_id": self.project_id,
            "total_scenarios": len(scenarios),
            "completed_count": len(completed),
            "escalated_count": len(escalations),
            "ready_batches_count": len(batches),
            "completed": completed,
            "escalations": escalations,
            "batches": batches,
        }

    def execute_batch_in_worktree(
        self,
        batch_id: str,
        scenario_ids: list[str],
        repo_path: Optional[str] = None,
        worktree_dir: Optional[str] = None,
        test_command: Optional[list[str]] = None,
    ) -> dict:
        """Prepares an execution packet in the target directory and optionally runs
        the verification test suite to promote scenarios to 'Verified' upon passing."""
        if not scenario_ids:
            raise ValueError("scenario_ids cannot be empty")

        # Determine target worktree directory
        if worktree_dir:
            target_path = Path(worktree_dir).resolve()
        elif repo_path:
            target_path = Path(repo_path).resolve()
        else:
            target_path = Path(".consultant") / "worktrees" / self.project_id / batch_id
            target_path = target_path.resolve()

        target_path.mkdir(parents=True, exist_ok=True)

        # 1. Generate HANDOFF_SPEC.md in the worktree
        handoff_md = self.consultant.generate_agent_handoff_spec(scenario_ids)
        spec_file = target_path / "HANDOFF_SPEC.md"
        spec_file.write_text(handoff_md, encoding="utf-8")

        # 2. Compile portable .ai-memory bundle in the worktree
        export_portable_memory_bundle(self.db, self.project_id, target_path)

        # 3. If test_command is provided, run verification
        if test_command:
            try:
                proc = subprocess.run(
                    test_command,
                    cwd=str(target_path),
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:
                return {
                    "success": False,
                    "promoted_to_verified": False,
                    "error": f"Failed to execute test command: {exc}",
                    "worktree_dir": str(target_path),
                }

            test_output = (
                f"=== TEST COMMAND: {' '.join(test_command)} ===\n"
                f"Exit Code: {proc.returncode}\n\n"
                f"--- STDOUT ---\n{proc.stdout}\n\n"
                f"--- STDERR ---\n{proc.stderr}\n"
            )
            raw_bytes = test_output.encode("utf-8")
            digest = hashlib.sha256(raw_bytes).hexdigest()[:12]
            evidence_id = f"TEST-LOG-{batch_id}-{digest}"
            source_ref = f"test:{' '.join(test_command)}"

            with self.db.transaction():
                self.db.add_evidence(
                    project_id=self.project_id,
                    evidence_id=evidence_id,
                    source_type="TEST_LOG",
                    source_reference=source_ref,
                    content=test_output,
                    byte_range=f"0-{len(raw_bytes)}",
                )

                if proc.returncode == 0:
                    # Promote each scenario to Verified
                    for sc_ref in scenario_ids:
                        sc = self.consultant.resolve_scenario(sc_ref)
                        self.db.upsert_scenario(
                            self.project_id,
                            {
                                "id": sc["id"],
                                "scenario_num": sc["scenario_num"],
                                "status": "Verified",
                                "verification_evidence_id": evidence_id,
                                "blockers": None,
                            },
                        )

                    return {
                        "success": True,
                        "promoted_to_verified": True,
                        "evidence_id": evidence_id,
                        "exit_code": 0,
                        "scenarios_verified": scenario_ids,
                        "worktree_dir": str(target_path),
                        "stdout": proc.stdout,
                    }
                else:
                    # Tests failed: keep status at Impl_Reported (or current)
                    for sc_ref in scenario_ids:
                        sc = self.consultant.resolve_scenario(sc_ref)
                        if sc["status"] not in ("Verified", "Blocked"):
                            self.db.upsert_scenario(
                                self.project_id,
                                {
                                    "id": sc["id"],
                                    "scenario_num": sc["scenario_num"],
                                    "status": "Impl_Reported",
                                },
                            )

                    return {
                        "success": False,
                        "promoted_to_verified": False,
                        "evidence_id": evidence_id,
                        "exit_code": proc.returncode,
                        "error": proc.stderr or proc.stdout,
                        "worktree_dir": str(target_path),
                    }

        return {
            "success": True,
            "promoted_to_verified": False,
            "packet_created": True,
            "worktree_dir": str(target_path),
            "files": ["HANDOFF_SPEC.md", ".ai-memory/PROJECT_STATE.md", ".ai-memory/DECISION_LOG.md", ".ai-memory/SKILL.md"],
        }
