"""Deterministic technical consultant over project memory.

Every answer here is computed from SQLite rows (scenarios, requirements,
code_symbols, decisions, evidence_vault). No LLM calls; the same memory state
always yields the same report, agenda and handoff spec.
"""

from __future__ import annotations

import re
from typing import Optional

from core.engine.reconciler import find_active_discrepancies
from core.ingestion.brd_parser import subject_for
from core.storage.db import COMPLETION_STATUSES, SCENARIO_STATUSES, ProjectMemoryDB

ACTIONABLE_STATUSES = ("Ready_For_Impl", "Unstarted")  # priority order
CONFLICTING_INDICATORS = frozenset({"nullable", "optional"})
OVERRIDE_STATUSES = frozenset({"APPROVED_DECISION"})


def _norm_ident(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _md_escape(text: Optional[str]) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _sc_label(scenario: dict) -> str:
    return f"SC-{int(scenario['scenario_num']):03d}"


class TechnicalConsultant:
    def __init__(self, db: ProjectMemoryDB, project_id: str):
        db.require_project(project_id)
        self.db = db
        self.project_id = project_id

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def has_active_blocker(scenario: dict) -> bool:
        if scenario["status"] == "Blocked":
            return True
        return bool((scenario.get("blockers") or "").strip()) and scenario["status"] not in COMPLETION_STATUSES

    def resolve_scenario(self, ref: str | int) -> dict:
        """Accepts a full id (``proj:SC-010``), ``SC-010``, ``SC10`` or ``10``."""
        text = str(ref).strip()
        scenario = self.db.get_scenario_by_id(self.project_id, text)
        if scenario:
            return scenario
        if ":" in text:
            prefix, text = text.rsplit(":", 1)
            if prefix != self.project_id:
                raise LookupError(f"Scenario {ref!r} does not belong to project {self.project_id!r}")
        m = re.fullmatch(r"(?:SC-?)?0*(\d+)", text, flags=re.IGNORECASE)
        if m:
            scenario = self.db.get_scenario(self.project_id, int(m.group(1)))
            if scenario:
                return scenario
        raise LookupError(f"Scenario {ref!r} not found in project {self.project_id!r}")

    def _evidence_citation(self, evidence_id: Optional[str]) -> Optional[dict]:
        ev = self.db.get_evidence(self.project_id, evidence_id)
        if ev is None:
            return None
        return {
            "evidence_id": ev["evidence_id"],
            "source_type": ev["source_type"],
            "source_reference": ev["source_reference"],
            "byte_range": ev["byte_range"],
            "content": ev["content"],
        }

    def _requirement_citation(self, req: Optional[dict]) -> list[dict]:
        cite = self._evidence_citation(req["evidence_id"]) if req else None
        return [cite] if cite else []

    def _related_symbols(self, scenario: dict, req: Optional[dict]) -> list[dict]:
        """Symbols named in ``scenario.code_symbols`` (classes expand to their
        fields) plus field symbols whose names match the requirement's fields."""
        found: dict[tuple[str, str], dict] = {}

        def add(sym: dict, reason: str) -> None:
            key = (sym["file_path"], sym["qualname"])
            if key not in found:
                found[key] = {**sym, "match_reason": reason}

        names = scenario.get("code_symbols") or []
        if isinstance(names, str):
            names = [n.strip() for n in names.split(",") if n.strip()]
        for name in names:
            for sym in self.db.find_code_symbols(self.project_id, name):
                add(sym, f"scenario.code_symbols:{name}")
                if sym["kind"] == "class":
                    for child in self.db.get_code_symbols(
                        self.project_id, parent=sym["qualname"], file_path=sym["file_path"]
                    ):
                        add(child, f"member of {sym['qualname']}")

        if req and req["fields"]:
            fields = self.db.get_code_symbols(self.project_id, kind="field")
            for spec in req["fields"]:
                if "." in spec:
                    target = _norm_ident(spec)
                    hits = [f for f in fields if _norm_ident(f["qualname"]) == target]
                else:
                    target = _norm_ident(spec)
                    hits = [f for f in fields if _norm_ident(f["symbol_name"]) == target]
                for sym in hits:
                    add(sym, f"{req['req_id']} field:{spec}")

        return sorted(found.values(), key=lambda s: (s["file_path"], s["line_start"], s["qualname"]))

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #
    def get_project_status_report(self) -> dict:
        project = self.db.get_project(self.project_id)
        scenarios = self.db.get_scenarios(self.project_id)
        by_status = {status: 0 for status in SCENARIO_STATUSES}
        for s in scenarios:
            by_status[s["status"]] += 1
        blockers = [
            {
                "scenario_id": s["id"],
                "scenario": _sc_label(s),
                "title": s["title"],
                "status": s["status"],
                "blockers": s.get("blockers"),
                "linked_req_id": s.get("linked_req_id"),
            }
            for s in scenarios
            if self.has_active_blocker(s)
        ]
        total = len(scenarios)
        return {
            "project_id": self.project_id,
            "project_name": project["name"],
            "total_scenarios": total,
            "by_status": by_status,
            "verified_pct": round(100.0 * by_status["Verified"] / total, 1) if total else 0.0,
            "active_blockers": blockers,
            "requirements": len(self.db.get_requirements(self.project_id)),
            "open_discrepancies": len(find_active_discrepancies(self.db, self.project_id)),
        }

    def get_next_scenarios(self, limit: int = 5) -> list[dict]:
        candidates = [
            s for s in self.db.get_scenarios(self.project_id)
            if s["status"] in ACTIONABLE_STATUSES and not self.has_active_blocker(s)
        ]
        candidates.sort(key=lambda s: (ACTIONABLE_STATUSES.index(s["status"]), s["scenario_num"]))
        return candidates[: max(0, int(limit))]

    def triangulate_scenario(self, scenario_id: str) -> dict:
        scenario = self.resolve_scenario(scenario_id)
        req = self.db.get_requirement(self.project_id, scenario.get("linked_req_id"))
        result: dict = {
            "scenario_id": scenario["id"],
            "scenario": _sc_label(scenario),
            "title": scenario["title"],
            "linked_req_id": scenario.get("linked_req_id"),
            "requirement": req,
            "brd_constraint": req["constraint_level"] if req else None,
            "requirement_evidence": self._requirement_citation(req),
            "code_symbols": [],
            "active_decisions": [],
            "approved_override": None,
            "conflicts": [],
            "discrepancy_detected": False,
            "clarification_questions": [],
            "notes": [],
        }
        if scenario.get("linked_req_id") and req is None:
            result["notes"].append(
                f"Linked requirement {scenario['linked_req_id']} not found in ingested BRD"
            )

        symbols = self._related_symbols(scenario, req)
        result["code_symbols"] = symbols

        subjects = [scenario["id"]] + ([subject_for(req["req_id"])] if req else [])
        decisions: list[dict] = []
        for subject in subjects:
            decisions.extend(self.db.get_decisions(self.project_id, subject, active_only=True))
        result["active_decisions"] = decisions

        if not req:
            return result

        # An APPROVED_DECISION overrides the BRD only if it is at least as new
        # as the current documented fact (a later BRD revision re-opens it).
        fact = self.db.get_decision(self.project_id, req["decision_id"])
        fact_from = fact["valid_from"] if fact else ""
        overrides = [
            d for d in decisions
            if d["epistemic_status"] in OVERRIDE_STATUSES
            and d["subject_entity"] == subject_for(req["req_id"])
            and d["valid_from"] >= fact_from
        ]
        if overrides:
            result["approved_override"] = overrides[-1]

        if req["constraint_level"] == "mandatory":
            for sym in symbols:
                if sym["kind"] not in ("field", "assignment"):
                    continue
                indicates = sym["details"].get("indicates")
                if indicates in CONFLICTING_INDICATORS:
                    result["conflicts"].append(
                        {
                            "symbol": sym["qualname"],
                            "file_path": sym["file_path"],
                            "line_start": sym["line_start"],
                            "line_end": sym["line_end"],
                            "code_indicates": indicates,
                            "indicators": sym["details"].get("indicators", []),
                            "code_evidence_id": sym["evidence_id"],
                            "brd_constraint": "mandatory",
                            "brd_reference": req["source_reference"],
                        }
                    )

        if result["conflicts"]:
            if result["approved_override"]:
                d = result["approved_override"]
                result["notes"].append(
                    f"BRD/code mismatch accepted by APPROVED_DECISION {d['id']}: {d['decision_text']}"
                )
            else:
                result["discrepancy_detected"] = True
                for c in result["conflicts"]:
                    result["clarification_questions"].append(
                        f"{req['req_id']} ('{req['title']}', {req['source_reference']}) states the field is "
                        f"MANDATORY, but `{c['symbol']}` in {c['file_path']}#L{c['line_start']} is "
                        f"{c['code_indicates']} ({', '.join(c['indicators'])}). Should the code enforce a "
                        f"required value, or should the BRD be amended to make it optional?"
                    )
                result["clarification_questions"].append(
                    f"If {req['req_id']} is relaxed to optional, who approves the change and what "
                    f"default/fallback behaviour applies when the value is missing?"
                )
        return result

    def get_stakeholder_agenda(self) -> list[dict]:
        agenda: list[dict] = []

        for d in find_active_discrepancies(self.db, self.project_id):
            citations = [c for c in (self._evidence_citation(d["fact_evidence_id"]),
                                     self._evidence_citation(d["claim_evidence_id"])) if c]
            agenda.append(
                {
                    "type": "CLAIM_DISCREPANCY",
                    "subject": d["subject_entity"],
                    "scenario_id": None,
                    "question": (
                        f"A reported claim on '{d['subject_entity']}' ({d['claimed_by'] or 'unknown'}: "
                        f"\"{d['claim_text']}\") contradicts the documented fact \"{d['fact_text']}\". "
                        f"Which is correct, and should an APPROVED_DECISION be recorded?"
                    ),
                    "evidence": citations,
                }
            )

        for s in self.db.get_scenarios(self.project_id):
            req = self.db.get_requirement(self.project_id, s.get("linked_req_id"))
            if req:
                tri = self.triangulate_scenario(s["id"])
                if tri["discrepancy_detected"]:
                    code_cites = [self._evidence_citation(c["code_evidence_id"]) for c in tri["conflicts"]]
                    agenda.append(
                        {
                            "type": "BRD_CODE_DISCREPANCY",
                            "subject": subject_for(req["req_id"]),
                            "scenario_id": s["id"],
                            "question": " ".join(tri["clarification_questions"]),
                            "evidence": self._requirement_citation(req) + [c for c in code_cites if c],
                        }
                    )

            if s["status"] == "Clarification_Needed":
                about = (f"{req['req_id']}: \"{req['statement']}\"" if req
                         else "(no linked requirement on record)")
                agenda.append(
                    {
                        "type": "CLARIFICATION_NEEDED",
                        "subject": s["id"],
                        "scenario_id": s["id"],
                        "question": (
                            f"{_sc_label(s)} '{s['title']}' needs clarification against {about}. "
                            f"Please confirm the expected behaviour and acceptance criteria."
                        ),
                        "evidence": self._requirement_citation(req),
                    }
                )
            elif s["status"] == "Blocked":
                agenda.append(
                    {
                        "type": "BLOCKED",
                        "subject": s["id"],
                        "scenario_id": s["id"],
                        "question": (
                            f"{_sc_label(s)} '{s['title']}' is blocked"
                            f"{' by: ' + s['blockers'] if s.get('blockers') else ''}. "
                            f"Who owns unblocking it, and by when?"
                        ),
                        "evidence": self._requirement_citation(req),
                    }
                )
        return agenda

    # ------------------------------------------------------------------ #
    # Agent handoff
    # ------------------------------------------------------------------ #
    def generate_agent_handoff_spec(self, scenario_ids: list[str]) -> str:
        if not scenario_ids:
            raise ValueError("scenario_ids must not be empty")
        scenarios = [self.resolve_scenario(ref) for ref in scenario_ids]
        project = self.db.get_project(self.project_id)

        out: list[str] = [
            f"# Agent Handoff Spec: {project['name']}",
            "",
            f"- **Project ID:** `{self.project_id}`",
        ]
        if project.get("repo_path"):
            out.append(f"- **Repository:** `{project['repo_path']}`")
        out += [
            f"- **Scenarios:** {', '.join(_sc_label(s) for s in scenarios)}",
            "",
            "> Generated deterministically from project memory. BRD text is quoted verbatim with "
            "source line ranges. `REPORTED_CLAIM` entries are unverified statements, not facts. "
            "A scenario is only `Verified` once a GIT_DIFF or TEST_LOG evidence id is attached.",
            "",
        ]

        for s in scenarios:
            tri = self.triangulate_scenario(s["id"])
            req = tri["requirement"]
            out += [
                "---",
                "",
                f"## {_sc_label(s)}: {s['title']}",
                "",
                f"- **Status:** `{s['status']}`",
                f"- **Linked requirement:** {s.get('linked_req_id') or 'none'}",
                f"- **Blockers:** {s.get('blockers') or 'none'}",
                "",
                "### Description",
                "",
                s.get("description") or "_No description recorded._",
                "",
                "### Requirement (BRD)",
                "",
            ]
            if req:
                cite = tri["requirement_evidence"][0] if tri["requirement_evidence"] else None
                out.append(
                    f"**{req['req_id']}: {req['title']}** | constraint: "
                    f"`{req['constraint_level'] or 'unspecified'}` | source: `{req['source_reference']}`"
                )
                out.append("")
                if cite:
                    out += [f"> {line}" if line else ">" for line in cite["content"].splitlines()]
                    out.append("")
            else:
                out += ["_No linked BRD requirement on record._", ""]

            out += ["### Known Code Symbols", ""]
            if tri["code_symbols"]:
                out += ["| Symbol | Kind | Location | Constraint signals |", "|---|---|---|---|"]
                for sym in tri["code_symbols"]:
                    signals = ", ".join(sym["details"].get("indicators") or []) or "-"
                    out.append(
                        f"| `{sym['qualname']}` | {sym['kind']} | "
                        f"`{sym['file_path']}#L{sym['line_start']}-L{sym['line_end']}` | "
                        f"{_md_escape(signals)} |"
                    )
            else:
                out.append("_No related code symbols found in the latest scan._")
            out.append("")

            out += ["### Active Decisions", ""]
            if tri["active_decisions"]:
                out += ["| Subject | Epistemic status | Decision | Evidence |", "|---|---|---|---|"]
                for d in tri["active_decisions"]:
                    out.append(
                        f"| `{d['subject_entity']}` | `{d['epistemic_status']}` | "
                        f"{_md_escape(d['decision_text'])} | `{d['source_evidence_id']}` |"
                    )
            else:
                out.append("_No active decisions._")
            out.append("")

            if tri["discrepancy_detected"]:
                out += [
                    "### Open Discrepancy: Do Not Guess",
                    "",
                    "The BRD and the code disagree and no APPROVED_DECISION resolves it. "
                    "Implement to the BRD unless the stakeholder answers otherwise:",
                    "",
                ]
                out += [f"- {q}" for q in tri["clarification_questions"]]
                out.append("")
            elif tri["approved_override"]:
                d = tri["approved_override"]
                out += [
                    "### Approved Deviation",
                    "",
                    f"- `{d['id']}`: {d['decision_text']} (evidence `{d['source_evidence_id']}`)",
                    "",
                ]

            out += ["### Acceptance Criteria", ""]
            criteria: list[str] = []
            if req:
                criteria += req["acceptance_criteria"] or [req["statement"]]
                if req["constraint_level"] == "mandatory" and not tri["approved_override"]:
                    criteria += [
                        f"`{c['symbol']}` ({c['file_path']}) must reject missing/null values "
                        f"(currently {c['code_indicates']})"
                        for c in tri["conflicts"]
                    ]
            criteria.append(
                f"Attach a GIT_DIFF or TEST_LOG evidence id so {_sc_label(s)} can move to `Verified`"
            )
            out += [f"- [ ] {c}" for c in criteria]
            out.append("")

        return "\n".join(out).rstrip() + "\n"
