"""Deterministic, project-isolated memory store backed by SQLite.

``ProjectMemoryDB`` owns ``schema.sql`` and exposes the only sanctioned write
paths into the project memory:

* ``projects``        - multi-project registry
* ``scenarios``       - relational scenario/task ledger
* ``decisions``       - bi-temporal decision & epistemic graph
* ``evidence_vault``  - verbatim FTS5 evidence store

Every read and write is scoped by ``project_id``; no method can observe or
mutate another project's rows.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = os.environ.get("CONSULTANT_DB_PATH", os.environ.get("CONSULTANT_DB", str(REPO_ROOT / "consultant.db")))

# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
SCENARIO_STATUSES = (
    "Unstarted",
    "Clarification_Needed",
    "Blocked",
    "Ready_For_Impl",
    "Impl_Reported",
    "Verified",
)
# Statuses that assert "the work is finished". Only evidence can turn one of
# these into ``Verified``; otherwise the authority gate caps it at
# ``Impl_Reported``.
COMPLETION_STATUSES = frozenset({"Impl_Reported", "Verified"})

EPISTEMIC_STATUSES = (
    "DOCUMENTED_FACT",
    "REPORTED_CLAIM",
    "INFERENCE",
    "PROPOSAL",
    "APPROVED_DECISION",
    "VERIFIED_OUTCOME",
)

EVIDENCE_SOURCE_TYPES = frozenset({"BRD", "EXCEL", "CHAT", "GIT_DIFF", "TEST_LOG", "CODE_AST"})
# Evidence kinds that are allowed to back a ``Verified`` scenario status.
VERIFICATION_SOURCE_TYPES = frozenset({"GIT_DIFF", "TEST_LOG"})

_STATUS_ALIASES: dict[str, str] = {}
for _canonical, _aliases in {
    "Unstarted": ("unstarted", "notstarted", "open", "todo", "new", "backlog"),
    "Clarification_Needed": (
        "clarificationneeded",
        "needsclarification",
        "clarification",
        "pendingclarification",
        "query",
    ),
    "Blocked": ("blocked", "onhold"),
    "Ready_For_Impl": (
        "readyforimpl",
        "readyforimplementation",
        "ready",
        "inprogress",
        "wip",
    ),
    "Impl_Reported": (
        "implreported",
        "implemented",
        "done",
        "complete",
        "completed",
        "fixed",
        "resolved",
        "closed",
        "devcomplete",
    ),
    "Verified": ("verified", "tested", "qapassed", "passed"),
}.items():
    for _alias in _aliases:
        _STATUS_ALIASES[_alias] = _canonical

_SCENARIO_FIELDS = (
    "id",
    "scenario_num",
    "title",
    "description",
    "status",
    "blockers",
    "linked_req_id",
    "code_symbols",
    "verification_evidence_id",
)


class ProjectNotFoundError(LookupError):
    """Raised when an operation references an unregistered ``project_id``."""


def normalize_status(raw: Any) -> str:
    """Map a free-text status (e.g. ``"Done"``, ``"Not Started"``) onto the
    canonical scenario vocabulary. Blank -> ``Unstarted``. Unknown -> ValueError.
    """
    if raw is None:
        return "Unstarted"
    text = str(raw).strip()
    if not text:
        return "Unstarted"
    if text in SCENARIO_STATUSES:
        return text
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    try:
        return _STATUS_ALIASES[key]
    except KeyError:
        raise ValueError(
            f"Unknown scenario status {raw!r}; expected one of {SCENARIO_STATUSES}"
        ) from None


def _now() -> str:
    """UTC timestamp, microsecond precision, lexicographically sortable."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class ProjectMemoryDB:
    """SQLite-backed project memory with strict ``project_id`` isolation."""

    def __init__(self, db_path: Optional[str | Path] = None, schema_path: str | Path = SCHEMA_PATH):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        self.db_path = str(db_path)
        # isolation_level=None -> we control transactions explicitly.
        self._conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._tx_depth = 0
        self._conn.executescript(Path(schema_path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ #
    # Lifecycle / transactions
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProjectMemoryDB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Re-entrant atomic block. Only the outermost block commits; any
        exception rolls back everything done inside it."""
        if self._tx_depth == 0:
            self._conn.execute("BEGIN")
        self._tx_depth += 1
        try:
            yield
        except BaseException:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self._conn.rollback()
            raise
        else:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self._conn.commit()

    # ------------------------------------------------------------------ #
    # Projects
    # ------------------------------------------------------------------ #
    def create_project(self, project_id: str, name: str, repo_path: Optional[str] = None) -> str:
        if not project_id or not name:
            raise ValueError("project_id and name are required")
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO projects (project_id, name, repo_path, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name = excluded.name, repo_path = excluded.repo_path
                """,
                (project_id, name, repo_path, _now()),
            )
        return project_id

    def get_project(self, project_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY created_at ASC, project_id ASC").fetchall()
        return [dict(r) for r in rows]

    def require_project(self, project_id: str) -> None:
        if self.get_project(project_id) is None:
            raise ProjectNotFoundError(f"Unknown project_id {project_id!r}")

    # ------------------------------------------------------------------ #
    # Scenarios
    # ------------------------------------------------------------------ #
    @staticmethod
    def scenario_id_for(project_id: str, scenario_num: int) -> str:
        """Deterministic, project-namespaced scenario primary key."""
        return f"{project_id}:SC-{int(scenario_num):03d}"

    def upsert_scenario(self, project_id: str, scenario_data: dict) -> str:
        """Insert or partially update a scenario. Keys omitted on update keep
        their stored values. Returns the scenario id."""
        unknown = set(scenario_data) - set(_SCENARIO_FIELDS)
        if unknown:
            raise ValueError(f"Unknown scenario fields: {sorted(unknown)}")

        with self.transaction():
            self.require_project(project_id)
            if "scenario_num" not in scenario_data and "id" not in scenario_data:
                raise ValueError("scenario_data requires 'scenario_num' or 'id'")

            scenario_id = scenario_data.get("id") or self.scenario_id_for(
                project_id, scenario_data["scenario_num"]
            )
            existing_row = self._conn.execute(
                "SELECT * FROM scenarios WHERE id = ?", (scenario_id,)
            ).fetchone()
            if existing_row is not None and existing_row["project_id"] != project_id:
                raise PermissionError(
                    f"Scenario {scenario_id!r} belongs to a different project"
                )

            merged: dict[str, Any] = dict(existing_row) if existing_row else {}
            merged.update(scenario_data)
            merged["id"] = scenario_id

            if merged.get("scenario_num") is None:
                raise ValueError("scenario_num is required")
            merged["scenario_num"] = int(merged["scenario_num"])
            if merged["scenario_num"] <= 0:
                raise ValueError("scenario_num must be a positive integer")
            if not merged.get("title"):
                raise ValueError("title is required")

            merged["status"] = normalize_status(merged.get("status"))
            if merged["status"] == "Verified" and not self.is_valid_verification_evidence(
                project_id, merged.get("verification_evidence_id")
            ):
                raise ValueError(
                    "Status 'Verified' requires a verification_evidence_id pointing "
                    "to GIT_DIFF or TEST_LOG evidence in this project"
                )

            symbols = merged.get("code_symbols")
            if isinstance(symbols, (list, tuple)):
                merged["code_symbols"] = json.dumps(list(symbols))

            self._conn.execute(
                """
                INSERT INTO scenarios (id, project_id, scenario_num, title, description,
                    status, blockers, linked_req_id, code_symbols,
                    verification_evidence_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scenario_num = excluded.scenario_num,
                    title = excluded.title,
                    description = excluded.description,
                    status = excluded.status,
                    blockers = excluded.blockers,
                    linked_req_id = excluded.linked_req_id,
                    code_symbols = excluded.code_symbols,
                    verification_evidence_id = excluded.verification_evidence_id,
                    updated_at = excluded.updated_at
                """,
                (
                    scenario_id,
                    project_id,
                    merged["scenario_num"],
                    merged["title"],
                    merged.get("description"),
                    merged["status"],
                    merged.get("blockers"),
                    merged.get("linked_req_id"),
                    merged.get("code_symbols"),
                    merged.get("verification_evidence_id"),
                    _now(),
                ),
            )
        return scenario_id

    @staticmethod
    def _scenario_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        symbols = data.get("code_symbols")
        if isinstance(symbols, str) and symbols.startswith("["):
            try:
                data["code_symbols"] = json.loads(symbols)
            except json.JSONDecodeError:
                pass
        return data

    def get_scenarios(self, project_id: str, status: Optional[str] = None) -> list[dict]:
        self.require_project(project_id)
        sql = "SELECT * FROM scenarios WHERE project_id = ?"
        params: list[Any] = [project_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(normalize_status(status))
        sql += " ORDER BY scenario_num, id"
        return [self._scenario_row(r) for r in self._conn.execute(sql, params)]

    def get_scenario(self, project_id: str, scenario_num: int) -> Optional[dict]:
        self.require_project(project_id)
        row = self._conn.execute(
            "SELECT * FROM scenarios WHERE project_id = ? AND scenario_num = ? "
            "ORDER BY id LIMIT 1",
            (project_id, int(scenario_num)),
        ).fetchone()
        return self._scenario_row(row) if row else None

    def get_scenario_by_id(self, project_id: str, scenario_id: str) -> Optional[dict]:
        self.require_project(project_id)
        row = self._conn.execute(
            "SELECT * FROM scenarios WHERE project_id = ? AND id = ?",
            (project_id, scenario_id),
        ).fetchone()
        return self._scenario_row(row) if row else None

    # ------------------------------------------------------------------ #
    # Requirements registry
    # ------------------------------------------------------------------ #
    _REQUIREMENT_JSON = ("fields", "acceptance_criteria")

    def upsert_requirement(self, project_id: str, req: dict) -> str:
        with self.transaction():
            self.require_project(project_id)
            self._conn.execute(
                """
                INSERT INTO requirements (project_id, req_id, title, statement,
                    constraint_level, constraint_source, fields, acceptance_criteria,
                    priority, source_reference, evidence_id, decision_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, req_id) DO UPDATE SET
                    title = excluded.title,
                    statement = excluded.statement,
                    constraint_level = excluded.constraint_level,
                    constraint_source = excluded.constraint_source,
                    fields = excluded.fields,
                    acceptance_criteria = excluded.acceptance_criteria,
                    priority = excluded.priority,
                    source_reference = excluded.source_reference,
                    evidence_id = excluded.evidence_id,
                    decision_id = excluded.decision_id,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id, req["req_id"], req["title"], req["statement"],
                    req.get("constraint_level"), req.get("constraint_source"),
                    json.dumps(list(req.get("fields") or [])),
                    json.dumps(list(req.get("acceptance_criteria") or [])),
                    req.get("priority"), req["source_reference"], req["evidence_id"],
                    req["decision_id"], _now(),
                ),
            )
        return req["req_id"]

    def _requirement_row(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        for key in self._REQUIREMENT_JSON:
            data[key] = json.loads(data[key]) if data.get(key) else []
        return data

    def get_requirement(self, project_id: str, req_id: Optional[str]) -> Optional[dict]:
        if not req_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM requirements WHERE project_id = ? AND req_id = ?",
            (project_id, req_id),
        ).fetchone()
        return self._requirement_row(row) if row else None

    def get_requirements(self, project_id: str) -> list[dict]:
        self.require_project(project_id)
        rows = self._conn.execute(
            "SELECT * FROM requirements WHERE project_id = ? ORDER BY req_id", (project_id,)
        )
        return [self._requirement_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Code symbol registry
    # ------------------------------------------------------------------ #
    def replace_code_symbols(self, project_id: str, symbols: list[dict]) -> int:
        """Replace the project's symbol snapshot atomically. Evidence for older
        scans remains in the vault."""
        with self.transaction():
            self.require_project(project_id)
            self._conn.execute("DELETE FROM code_symbols WHERE project_id = ?", (project_id,))
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO code_symbols (project_id, file_path, qualname,
                    symbol_name, kind, parent, line_start, line_end, byte_range,
                    details, evidence_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_id, s["file_path"], s["qualname"], s["symbol_name"], s["kind"],
                        s.get("parent"), s["line_start"], s["line_end"], s.get("byte_range"),
                        json.dumps(s.get("details") or {}, sort_keys=True, default=str), s["evidence_id"],
                    )
                    for s in symbols
                ],
            )
        return len(symbols)

    @staticmethod
    def _symbol_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["details"] = json.loads(data["details"]) if data.get("details") else {}
        return data

    def get_code_symbols(
        self,
        project_id: str,
        kind: Optional[str] = None,
        parent: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> list[dict]:
        self.require_project(project_id)
        sql = "SELECT * FROM code_symbols WHERE project_id = ?"
        params: list[Any] = [project_id]
        for col, val in (("kind", kind), ("parent", parent), ("file_path", file_path)):
            if val is not None:
                sql += f" AND {col} = ?"
                params.append(val)
        sql += " ORDER BY file_path, line_start, qualname"
        return [self._symbol_row(r) for r in self._conn.execute(sql, params)]

    def find_code_symbols(self, project_id: str, name: str) -> list[dict]:
        """Exact match on bare name (``email``) or qualified name (``Customer.email``)."""
        self.require_project(project_id)
        rows = self._conn.execute(
            "SELECT * FROM code_symbols WHERE project_id = ? AND (symbol_name = ? OR qualname = ?) "
            "ORDER BY file_path, line_start, qualname",
            (project_id, name, name),
        )
        return [self._symbol_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Decisions (bi-temporal)
    # ------------------------------------------------------------------ #
    def record_decision(
        self,
        project_id: str,
        subject_entity: str,
        decision_text: str,
        epistemic_status: str,
        source_evidence_id: str,
        rationale: Optional[str] = None,
        created_by: Optional[str] = None,
        *,
        supersede_statuses: Optional[Iterable[str]] = None,
    ) -> str:
        """Record a decision. Any currently-active decision on the same
        ``(project_id, subject_entity)`` is closed (``valid_to`` set) and linked
        to the new one via ``superseded_by``; nothing is deleted.

        ``supersede_statuses`` restricts which active records get superseded
        (default: all). The reconciler uses this so a REPORTED_CLAIM can never
        retire a DOCUMENTED_FACT.
        """
        if epistemic_status not in EPISTEMIC_STATUSES:
            raise ValueError(
                f"Invalid epistemic_status {epistemic_status!r}; expected one of {EPISTEMIC_STATUSES}"
            )
        if not subject_entity or not decision_text:
            raise ValueError("subject_entity and decision_text are required")

        with self.transaction():
            self.require_project(project_id)
            if self.get_evidence(project_id, source_evidence_id) is None:
                raise ValueError(
                    f"source_evidence_id {source_evidence_id!r} not found in this project's evidence vault"
                )

            now = _now()
            new_id = f"DEC-{uuid.uuid4().hex}"

            sql = (
                "SELECT id FROM decisions WHERE project_id = ? AND subject_entity = ? "
                "AND valid_to IS NULL"
            )
            params: list[Any] = [project_id, subject_entity]
            if supersede_statuses is not None:
                statuses = list(supersede_statuses)
                if not statuses:
                    sql += " AND 0"
                else:
                    sql += f" AND epistemic_status IN ({','.join('?' * len(statuses))})"
                    params.extend(statuses)
            to_supersede = [r["id"] for r in self._conn.execute(sql, params)]

            self._conn.execute(
                """
                INSERT INTO decisions (id, project_id, subject_entity, decision_text,
                    rationale, epistemic_status, source_evidence_id, valid_from,
                    valid_to, superseded_by, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (new_id, project_id, subject_entity, decision_text, rationale,
                 epistemic_status, source_evidence_id, now, created_by),
            )
            if to_supersede:
                self._conn.execute(
                    f"UPDATE decisions SET valid_to = ?, superseded_by = ? "
                    f"WHERE project_id = ? AND id IN ({','.join('?' * len(to_supersede))})",
                    [now, new_id, project_id, *to_supersede],
                )
        return new_id

    def get_decision(self, project_id: str, decision_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? AND id = ?",
            (project_id, decision_id),
        ).fetchone()
        return dict(row) if row else None

    def get_decisions(
        self,
        project_id: str,
        subject_entity: Optional[str] = None,
        active_only: bool = False,
    ) -> list[dict]:
        self.require_project(project_id)
        sql = "SELECT * FROM decisions WHERE project_id = ?"
        params: list[Any] = [project_id]
        if subject_entity is not None:
            sql += " AND subject_entity = ?"
            params.append(subject_entity)
        if active_only:
            sql += " AND valid_to IS NULL"
        sql += " ORDER BY valid_from, rowid"
        return [dict(r) for r in self._conn.execute(sql, params)]

    # ------------------------------------------------------------------ #
    # Evidence vault (FTS5)
    # ------------------------------------------------------------------ #
    def add_evidence(
        self,
        project_id: str,
        evidence_id: str,
        source_type: str,
        source_reference: str,
        content: str,
        byte_range: Optional[str] = None,
    ) -> str:
        """Store verbatim evidence. Evidence is immutable: re-adding the same
        id with identical content is a no-op; different content raises."""
        if source_type not in EVIDENCE_SOURCE_TYPES:
            raise ValueError(
                f"Invalid source_type {source_type!r}; expected one of {sorted(EVIDENCE_SOURCE_TYPES)}"
            )
        if not evidence_id or not isinstance(content, str) or not content:
            raise ValueError("evidence_id and non-empty content are required")

        with self.transaction():
            self.require_project(project_id)
            existing = self.get_evidence(project_id, evidence_id)
            if existing is not None:
                if (existing["content"], existing["source_type"]) == (content, source_type):
                    return evidence_id
                raise ValueError(
                    f"Evidence {evidence_id!r} already exists with different content (evidence is immutable)"
                )
            self._conn.execute(
                """
                INSERT INTO evidence_vault (evidence_id, project_id, source_type,
                    source_reference, content, byte_range, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (evidence_id, project_id, source_type, source_reference, content,
                 byte_range, _now()),
            )
        return evidence_id

    def get_evidence(self, project_id: str, evidence_id: Optional[str]) -> Optional[dict]:
        if not evidence_id:
            return None
        row = self._conn.execute(
            "SELECT rowid AS _rowid, * FROM evidence_vault WHERE project_id = ? AND evidence_id = ?",
            (project_id, evidence_id),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data.pop("_rowid", None)
        return data

    def count_evidence(self, project_id: str, source_type: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM evidence_vault WHERE project_id = ?"
        params: list[Any] = [project_id]
        if source_type is not None:
            sql += " AND source_type = ?"
            params.append(source_type)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def is_valid_verification_evidence(self, project_id: str, evidence_id: Optional[str]) -> bool:
        evidence = self.get_evidence(project_id, evidence_id)
        return evidence is not None and evidence["source_type"] in VERIFICATION_SOURCE_TYPES

    def search_evidence(
        self,
        project_id: str,
        query: str,
        limit: int = 20,
        match_any: bool = False,
    ) -> list[dict]:
        """BM25-ranked full-text search over evidence ``content`` for one
        project. The query is tokenised and each term quoted, so user input can
        never inject FTS5 syntax. Terms are ANDed unless ``match_any``."""
        self.require_project(project_id)
        terms = re.findall(r"\w+", query or "", flags=re.UNICODE)
        if not terms:
            return []
        joiner = " OR " if match_any else " AND "
        fts_query = joiner.join(f'content : "{t}"' for t in terms)
        rows = self._conn.execute(
            """
            SELECT evidence_id, project_id, source_type, source_reference, content,
                   byte_range, recorded_at, bm25(evidence_vault) AS score
            FROM evidence_vault
            WHERE evidence_vault MATCH ? AND project_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, project_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Multi-Thread Conversational Memory & Undoable Mutations
    # ------------------------------------------------------------------ #
    THREAD_MODES = ("project", "lightweight")

    def create_thread(
        self,
        project_id: str,
        title: str,
        mode: str = "project",
        thread_id: Optional[str] = None,
    ) -> dict:
        self.require_project(project_id)
        if mode not in self.THREAD_MODES:
            raise ValueError(f"Invalid thread mode {mode!r}; expected one of {self.THREAD_MODES}")
        if not title or not title.strip():
            title = "New Thread"
        tid = thread_id or f"THR-{uuid.uuid4().hex[:12]}"
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO threads (thread_id, project_id, title, mode, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tid, project_id, title.strip(), mode, _now()),
            )
        return self.get_thread(project_id, tid)

    def list_threads(self, project_id: str) -> list[dict]:
        self.require_project(project_id)
        rows = self._conn.execute(
            "SELECT * FROM threads WHERE project_id = ? ORDER BY created_at DESC, thread_id DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_thread(self, project_id: str, thread_id: str) -> Optional[dict]:
        self.require_project(project_id)
        row = self._conn.execute(
            "SELECT * FROM threads WHERE project_id = ? AND thread_id = ?",
            (project_id, thread_id),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _parse_message_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["undone"] = bool(data.get("undone", 0))
        mut_json = data.get("mutations_json")
        if mut_json:
            try:
                data["mutations"] = json.loads(mut_json)
            except Exception:
                data["mutations"] = None
        else:
            data["mutations"] = None

        cit_json = data.get("citations_json")
        if cit_json:
            try:
                data["citations"] = json.loads(cit_json)
            except Exception:
                data["citations"] = []
        else:
            data["citations"] = []
        return data

    def add_message(
        self,
        project_id: str,
        thread_id: str,
        role: str,
        content: str,
        mutations_json: Optional[str | dict] = None,
        citations_json: Optional[str | list] = None,
        message_id: Optional[str] = None,
    ) -> dict:
        self.require_project(project_id)
        if not self.get_thread(project_id, thread_id):
            raise LookupError(f"Thread {thread_id!r} not found in project {project_id!r}")
        if role not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid message role {role!r}")

        mid = message_id or f"MSG-{uuid.uuid4().hex[:12]}"
        mut_str = json.dumps(mutations_json) if isinstance(mutations_json, dict) else mutations_json
        cit_str = json.dumps(citations_json) if isinstance(citations_json, list) else citations_json

        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO messages (message_id, thread_id, project_id, role, content,
                                      mutations_json, citations_json, undone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (mid, thread_id, project_id, role, content, mut_str, cit_str, _now()),
            )
        return self.get_message(project_id, mid)

    def get_message(self, project_id: str, message_id: str) -> Optional[dict]:
        self.require_project(project_id)
        row = self._conn.execute(
            "SELECT * FROM messages WHERE project_id = ? AND message_id = ?",
            (project_id, message_id),
        ).fetchone()
        return self._parse_message_row(row) if row else None

    def get_thread_messages(self, project_id: str, thread_id: str) -> list[dict]:
        self.require_project(project_id)
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE project_id = ? AND thread_id = ? ORDER BY created_at ASC, rowid ASC",
            (project_id, thread_id),
        ).fetchall()
        return [self._parse_message_row(r) for r in rows]

    def undo_message_mutations(self, project_id: str, message_id: str) -> dict:
        """Deterministic undo of all memory updates enacted by an assistant message.
        Restores prior scenario fields, removes created decisions, and reactivates
        superseded decisions. Marks message.undone = 1. Fully idempotent."""
        self.require_project(project_id)
        row = self._conn.execute(
            "SELECT * FROM messages WHERE project_id = ? AND message_id = ?",
            (project_id, message_id),
        ).fetchone()
        if not row:
            raise LookupError(f"Message {message_id!r} not found in project {project_id!r}")

        msg = dict(row)
        if msg["undone"]:
            return {
                "success": True,
                "already_undone": True,
                "message_id": message_id,
                "restored_scenarios": [],
                "reverted_decisions": [],
            }

        mut_json = msg.get("mutations_json")
        payload = json.loads(mut_json) if mut_json else {}

        restored_scenarios: list[str] = []
        reverted_decisions: list[str] = []

        with self.transaction():
            # 1. Restore scenarios
            for item in payload.get("scenarios", []):
                sc_id = item["scenario_id"]
                existed = item.get("existed", True)
                if not existed:
                    self._conn.execute(
                        "DELETE FROM scenarios WHERE project_id = ? AND id = ?",
                        (project_id, sc_id),
                    )
                    restored_scenarios.append(f"{sc_id} (deleted newly created)")
                else:
                    prev = item.get("previous", {})
                    symbols = prev.get("code_symbols")
                    if isinstance(symbols, (list, tuple)):
                        symbols = json.dumps(list(symbols))
                    self._conn.execute(
                        """
                        UPDATE scenarios SET
                            scenario_num = ?,
                            title = ?,
                            description = ?,
                            status = ?,
                            blockers = ?,
                            linked_req_id = ?,
                            code_symbols = ?,
                            verification_evidence_id = ?,
                            updated_at = ?
                        WHERE project_id = ? AND id = ?
                        """,
                        (
                            prev["scenario_num"],
                            prev["title"],
                            prev.get("description"),
                            prev["status"],
                            prev.get("blockers"),
                            prev.get("linked_req_id"),
                            symbols,
                            prev.get("verification_evidence_id"),
                            _now(),
                            project_id,
                            sc_id,
                        ),
                    )
                    restored_scenarios.append(sc_id)

            # 2. Delete newly created decisions
            for dec_id in payload.get("created_decisions", []):
                self._conn.execute(
                    "DELETE FROM decisions WHERE project_id = ? AND id = ?",
                    (project_id, dec_id),
                )
                reverted_decisions.append(f"{dec_id} (deleted)")

            # 3. Un-supersede decisions that were superseded
            for item in payload.get("superseded_decisions", []):
                d_id = item["decision_id"]
                prev_valid_to = item.get("prev_valid_to")
                prev_superseded_by = item.get("prev_superseded_by")
                self._conn.execute(
                    "UPDATE decisions SET valid_to = ?, superseded_by = ? WHERE project_id = ? AND id = ?",
                    (prev_valid_to, prev_superseded_by, project_id, d_id),
                )
                reverted_decisions.append(f"{d_id} (restored active)")

            # 4. Mark message as undone
            self._conn.execute(
                "UPDATE messages SET undone = 1 WHERE message_id = ?",
                (message_id,),
            )

        return {
            "success": True,
            "already_undone": False,
            "message_id": message_id,
            "restored_scenarios": restored_scenarios,
            "reverted_decisions": reverted_decisions,
        }
