"""Deterministic Markdown BRD ingestion.

Recognised requirement starts (IDs look like ``REQ-001``, ``BR-12``, ``FR-3``)::

    ### REQ-001: Field Validation          <- heading form (any level)
    - **REQ-002**: Dual approval           <- bullet form
    - REQ-003 - Payment reference

Inside a requirement block, these property lines are parsed (bold optional)::

    Constraint: Mandatory | Optional
    Field: `customer_email`               (comma-separated for several)
    Priority: High
    Acceptance Criteria:                  (inline value, or following bullets)
      - criterion one

Remaining non-blank lines form the requirement ``statement``. If no explicit
``Constraint:`` line exists, the level is inferred from RFC-2119 keywords
(MUST/SHALL/REQUIRED/MANDATORY -> mandatory, OPTIONAL/MAY/NOT REQUIRED ->
optional) and ``constraint_source`` is set to ``'keyword'``.

Each requirement is stored as verbatim BRD evidence with an exact line range
and byte span, as a ``DOCUMENTED_FACT`` decision on subject
``requirement:<REQ-ID>``, and as a structured row in ``requirements``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.storage.db import ProjectMemoryDB

REQ_ID = r"[A-Z][A-Z0-9]*-\d+"
_SEP = r"(?:\s*[:\-\u2013\u2014.]\s*|\s+)"
HEADING_RE = re.compile(rf"^(#{{1,6}})\s+\**({REQ_ID})\**{_SEP}?(.*?)\s*#*\s*$")
BULLET_RE = re.compile(rf"^(\s*)(?:[-*+]|\d+[.)])\s+\**({REQ_ID})\**{_SEP}?(.*)$")
ANY_HEADING_RE = re.compile(r"^(#{1,6})\s")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
PROPERTY_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?\**\s*(constraint|fields?|priority|acceptance criteria|acceptance)"
    r"\s*\**\s*:\s*\**\s*(.*?)\s*\**\s*$",
    re.IGNORECASE,
)
BULLET_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_][\w.]*)`")

_OPTIONAL_KW = re.compile(r"\b(not required|not mandatory|optional)\b", re.IGNORECASE)
_MANDATORY_KW = re.compile(r"\b(must|shall|required|mandatory)\b", re.IGNORECASE)
_MAY_KW = re.compile(r"\bmay\b", re.IGNORECASE)


def subject_for(req_id: str) -> str:
    """Canonical decision subject for a requirement."""
    return f"requirement:{req_id}"


@dataclass
class ParsedRequirement:
    req_id: str
    title: str
    statement: str
    line_start: int  # 1-based, inclusive
    line_end: int
    byte_start: int  # inclusive
    byte_end: int    # exclusive
    verbatim: str
    constraint_level: Optional[str] = None
    constraint_source: Optional[str] = None
    fields: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: Optional[str] = None


def _clean(text: str) -> str:
    return text.strip().strip("*").strip()


def _normalize_constraint(value: str) -> Optional[str]:
    v = value.lower()
    if _OPTIONAL_KW.search(v):
        return "optional"
    if _MANDATORY_KW.search(v):
        return "mandatory"
    return v.strip() or None


def _infer_constraint(text: str) -> Optional[str]:
    if _OPTIONAL_KW.search(text):
        return "optional"
    if _MANDATORY_KW.search(text):
        return "mandatory"
    if _MAY_KW.search(text):
        return "optional"
    return None


def _match_start(line: str):
    m = HEADING_RE.match(line)
    if m:
        return "heading", len(m.group(1)), m.group(2), _clean(m.group(3))
    m = BULLET_RE.match(line)
    if m:
        return "bullet", len(m.group(1).expandtabs(4)), m.group(2), _clean(m.group(3))
    return None


def _indent(line: str) -> int:
    return len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())


def parse_brd_markdown(content: str) -> list[ParsedRequirement]:
    lines = content.splitlines(keepends=True)
    byte_offsets = [0]
    for ln in lines:
        byte_offsets.append(byte_offsets[-1] + len(ln.encode("utf-8")))
    raw = content.encode("utf-8")

    # Identify which lines are inside fenced code blocks.
    in_fence = [False] * len(lines)
    fenced = False
    for i, ln in enumerate(lines):
        if FENCE_RE.match(ln):
            in_fence[i] = True
            fenced = not fenced
            continue
        in_fence[i] = fenced

    starts = []
    for i, ln in enumerate(lines):
        if in_fence[i]:
            continue
        hit = _match_start(ln.rstrip("\r\n"))
        if hit:
            starts.append((i, *hit))

    results: list[ParsedRequirement] = []
    seen: dict[str, int] = {}
    for n, (i, form, level, req_id, title) in enumerate(starts):
        if req_id in seen:
            raise ValueError(f"Duplicate requirement id {req_id} at lines {seen[req_id]} and {i + 1}")
        seen[req_id] = i + 1
        next_start = starts[n + 1][0] if n + 1 < len(starts) else len(lines)

        end = i
        for j in range(i + 1, next_start):
            text = lines[j].rstrip("\r\n")
            if form == "bullet":
                if text.strip() and _indent(text) <= level:
                    break
                if not in_fence[j] and ANY_HEADING_RE.match(text):
                    break
            elif form == "heading":
                if not in_fence[j]:
                    h = ANY_HEADING_RE.match(text)
                    if h and len(h.group(1)) <= level:
                        break
            end = j
        while end > i and not lines[end].strip():
            end -= 1

        body = [lines[j].rstrip("\r\n") for j in range(i + 1, end + 1)]
        statement_parts: list[str] = []
        req = ParsedRequirement(
            req_id=req_id, title=title or req_id, statement="", line_start=i + 1,
            line_end=end + 1, byte_start=byte_offsets[i], byte_end=0, verbatim="",
        )
        in_acceptance = False
        for text in body:
            if not text.strip():
                continue
            prop = PROPERTY_RE.match(text)
            if prop:
                key, value = prop.group(1).lower(), _clean(prop.group(2))
                in_acceptance = False
                if key == "constraint":
                    req.constraint_level = _normalize_constraint(value)
                    req.constraint_source = "explicit"
                elif key in ("field", "fields"):
                    req.fields.extend(
                        v.strip().strip("`").strip() for v in value.split(",") if v.strip().strip("`")
                    )
                elif key == "priority":
                    req.priority = value or None
                else:  # acceptance
                    if value:
                        req.acceptance_criteria.append(value)
                    else:
                        in_acceptance = True
                continue
            item = BULLET_ITEM_RE.match(text)
            if in_acceptance and item:
                req.acceptance_criteria.append(_clean(item.group(1)))
                continue
            in_acceptance = False
            statement_parts.append(_clean(item.group(1)) if item else text.strip())

        req.statement = " ".join(statement_parts) or req.title
        if req.constraint_level is None:
            inferred = _infer_constraint(f"{req.title} {req.statement}")
            if inferred:
                req.constraint_level, req.constraint_source = inferred, "keyword"
        if not req.fields:
            for ident in BACKTICK_IDENT_RE.findall(f"{req.title} {req.statement}"):
                if ident not in req.fields:
                    req.fields.append(ident)

        last = lines[end]
        trailing = len(last.encode("utf-8")) - len(last.rstrip("\r\n").encode("utf-8"))
        req.byte_end = byte_offsets[end + 1] - trailing
        req.verbatim = raw[req.byte_start:req.byte_end].decode("utf-8")
        results.append(req)
    return results


def ingest_brd_markdown(
    db: ProjectMemoryDB,
    project_id: str,
    file_path: str,
    content: Optional[str] = None,
) -> dict:
    """Parse a Markdown BRD and persist every requirement.

    If ``content`` is given, ``file_path`` is used only as the source label.
    Re-ingesting an unchanged requirement is a no-op; a changed requirement
    gets new evidence and a new DOCUMENTED_FACT that supersedes the old fact
    (but never an APPROVED_DECISION or other record type).
    """
    db.require_project(project_id)
    if content is None:
        content = Path(file_path).read_text(encoding="utf-8")
    label = Path(file_path).name

    parsed = parse_brd_markdown(content)
    req_ids, evidence_ids, decision_ids = [], [], []
    with db.transaction():
        for req in parsed:
            digest = hashlib.sha256(req.verbatim.encode("utf-8")).hexdigest()[:12]
            evidence_id = f"BRD:{label}:{req.req_id}:{digest}"
            source_reference = f"{label}#L{req.line_start}-L{req.line_end}"
            db.add_evidence(
                project_id, evidence_id, "BRD", source_reference, req.verbatim,
                byte_range=f"{req.byte_start}-{req.byte_end}",
            )

            existing = db.get_requirement(project_id, req.req_id)
            decision_id = None
            if existing and existing["evidence_id"] == evidence_id:
                # Unchanged text: keep the original fact even if an approval has
                # since superseded it, so re-ingest never re-opens resolved items.
                decision_id = existing["decision_id"]
            if decision_id is None:
                decision_id = db.record_decision(
                    project_id,
                    subject_for(req.req_id),
                    req.statement,
                    "DOCUMENTED_FACT",
                    evidence_id,
                    rationale=f"{req.title} [constraint: {req.constraint_level or 'unspecified'}]",
                    created_by=f"brd:{label}",
                    supersede_statuses={"DOCUMENTED_FACT"},
                )

            db.upsert_requirement(
                project_id,
                {
                    "req_id": req.req_id,
                    "title": req.title,
                    "statement": req.statement,
                    "constraint_level": req.constraint_level,
                    "constraint_source": req.constraint_source,
                    "fields": req.fields,
                    "acceptance_criteria": req.acceptance_criteria,
                    "priority": req.priority,
                    "source_reference": source_reference,
                    "evidence_id": evidence_id,
                    "decision_id": decision_id,
                },
            )
            req_ids.append(req.req_id)
            evidence_ids.append(evidence_id)
            decision_ids.append(decision_id)

    return {
        "requirements_ingested": len(parsed),
        "req_ids": req_ids,
        "evidence_ids": evidence_ids,
        "decision_ids": decision_ids,
    }
