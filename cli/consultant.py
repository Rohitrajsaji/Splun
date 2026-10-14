"""AI Project Consultant CLI.

Usage (from the repo root):
    .venv/bin/python -m cli.consultant --project pay init --name "Payments"
    .venv/bin/python -m cli.consultant --project pay ingest-brd docs/BRD.md
    .venv/bin/python -m cli.consultant --project pay ingest-excel 80_Scenarios.xlsx
    .venv/bin/python -m cli.consultant --project pay scan-code ./src
    .venv/bin/python -m cli.consultant --project pay status
    .venv/bin/python -m cli.consultant --project pay next --limit 5
    .venv/bin/python -m cli.consultant --project pay agenda
    .venv/bin/python -m cli.consultant --project pay handoff --scenarios SC-010,SC-011

``--db`` defaults to ``$CONSULTANT_DB`` or ``.consultant/memory.db``;
``--project`` defaults to ``$CONSULTANT_PROJECT``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

if __package__ in (None, ""):  # allow `python cli/consultant.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine.consultant import TechnicalConsultant  # noqa: E402
from core.ingestion.brd_parser import ingest_brd_markdown  # noqa: E402
from core.ingestion.code_scanner import scan_python_codebase  # noqa: E402
from core.ingestion.excel_parser import ingest_scenarios_excel  # noqa: E402
from core.storage.db import ProjectMemoryDB, ProjectNotFoundError  # noqa: E402

DEFAULT_DB = os.environ.get("CONSULTANT_DB_PATH", os.environ.get("CONSULTANT_DB", str(Path(__file__).resolve().parents[1] / "consultant.db")))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="consultant", description="AI Project Consultant (deterministic)")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite path")
    p.add_argument("--project", default=os.environ.get("CONSULTANT_PROJECT"), help="Project ID")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text (status/next/agenda)")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Register the project")
    init.add_argument("--name", required=True)
    init.add_argument("--repo-path")

    sub.add_parser("ingest-brd", help="Ingest a Markdown BRD").add_argument("path")
    sub.add_parser("ingest-excel", help="Ingest a scenario spreadsheet").add_argument("path")
    sub.add_parser("scan-code", help="Scan a Python codebase").add_argument("root")

    sub.add_parser("status", help="Project overview and scenario breakdown")
    nxt = sub.add_parser("next", help="Next unblocked scenarios")
    nxt.add_argument("--limit", type=int, default=5)
    sub.add_parser("agenda", help="Clarification questions for the team/NSI")
    handoff = sub.add_parser("handoff", help="Generate agent Markdown spec")
    handoff.add_argument("--scenarios", required=True, help="Comma-separated, e.g. SC-010,SC-011")
    return p


def _print_status(r: dict) -> None:
    print(f"Project: {r['project_name']} ({r['project_id']})")
    print(f"Scenarios: {r['total_scenarios']}  |  Verified: {r['verified_pct']}%  |  "
          f"Requirements: {r['requirements']}  |  Open claim discrepancies: {r['open_discrepancies']}")
    print("")
    print("Status breakdown:")
    width = max(len(s) for s in r["by_status"])
    for status, count in r["by_status"].items():
        print(f"  {status.ljust(width)}  {count:>4}")
    print("")
    if r["active_blockers"]:
        print(f"Active blockers ({len(r['active_blockers'])}):")
        for b in r["active_blockers"]:
            print(f"  - {b['scenario']} {b['title']} [{b['status']}]: {b['blockers'] or 'no detail recorded'}")
    else:
        print("Active blockers: none")


def _print_next(items: list[dict]) -> None:
    if not items:
        print("No unblocked Unstarted/Ready_For_Impl scenarios.")
        return
    print("Next unblocked scenarios:")
    for s in items:
        req = f" ({s['linked_req_id']})" if s.get("linked_req_id") else ""
        print(f"  - SC-{s['scenario_num']:03d} [{s['status']}] {s['title']}{req}")


def _print_agenda(items: list[dict]) -> None:
    if not items:
        print("Agenda is empty: no open discrepancies, clarifications or blockers.")
        return
    print(f"Stakeholder agenda ({len(items)} items):")
    for i, item in enumerate(items, 1):
        print(f"\n{i}. [{item['type']}] {item['subject']}")
        print(f"   Q: {item['question']}")
        for ev in item["evidence"]:
            first = ev["content"].splitlines()[0] if ev["content"] else ""
            print(f"   Evidence: {ev['source_type']} {ev['source_reference']}: {first}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.project:
        print("error: --project (or $CONSULTANT_PROJECT) is required", file=sys.stderr)
        return 2

    if args.db != ":memory:":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    db = ProjectMemoryDB(args.db)
    try:
        if args.command == "init":
            db.create_project(args.project, args.name, args.repo_path)
            print(f"Project '{args.project}' registered in {args.db}")
            return 0
        if args.command == "ingest-brd":
            r = ingest_brd_markdown(db, args.project, args.path)
            print(f"Ingested {r['requirements_ingested']} requirements: {', '.join(r['req_ids'])}")
            return 0
        if args.command == "ingest-excel":
            r = ingest_scenarios_excel(db, args.project, args.path)
            print(f"Ingested {r['rows_ingested']} scenario rows")
            return 0
        if args.command == "scan-code":
            r = scan_python_codebase(db, args.project, args.root)
            print(f"Scanned {r['files_scanned']} files, {r['symbols_found']} symbols, "
                  f"{len(r['errors'])} parse errors")
            for e in r["errors"]:
                print(f"  ! {e['file']}: {e['error']}")
            return 0

        consultant = TechnicalConsultant(db, args.project)
        if args.command == "status":
            r = consultant.get_project_status_report()
            print(json.dumps(r, indent=2)) if args.json else _print_status(r)
        elif args.command == "next":
            r = consultant.get_next_scenarios(args.limit)
            print(json.dumps(r, indent=2, default=str)) if args.json else _print_next(r)
        elif args.command == "agenda":
            r = consultant.get_stakeholder_agenda()
            print(json.dumps(r, indent=2, default=str)) if args.json else _print_agenda(r)
        elif args.command == "handoff":
            refs = [s.strip() for s in args.scenarios.split(",") if s.strip()]
            print(consultant.generate_agent_handoff_spec(refs), end="")
        return 0
    except (ProjectNotFoundError, LookupError, ValueError, FileNotFoundError, NotADirectoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
