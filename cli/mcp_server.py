"""Standard-Library Zero-Dependency Model Context Protocol (MCP) Server.

Exposes the AI Project Consultant capabilities over standard JSON-RPC 2.0 stdio.
Supports:
- initialize, notifications/initialized, tools/list, tools/call
- Tools:
  * consultant_status (project_id)
  * consultant_next_scenarios (project_id, limit)
  * consultant_agenda (project_id)
  * consultant_triangulate (project_id, scenario_id)
  * consultant_handoff (project_id, scenario_ids)
  * consultant_chat_update (project_id, thread_id, message)
  * consultant_verify_git (project_id, diff_text, commit_sha, scenario_ids)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine.chat_processor import process_chat_turn
from core.engine.consultant import TechnicalConsultant
from core.engine.coordinator import AutonomousCoordinator
from core.ingestion.git_verifier import verify_scenarios_from_git
from core.storage.db import ProjectMemoryDB

PROTOCOL_VERSION = "2024-11-05"

TOOLS_DEFINITIONS = [
    {
        "name": "consultant_status",
        "description": "Get overall project status report, scenario progress breakdown, and active blockers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "consultant_next_scenarios",
        "description": "Get next unblocked scenarios ready for implementation in priority order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
                "limit": {"type": "integer", "description": "Maximum scenarios to return", "default": 5},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "consultant_agenda",
        "description": "Get stakeholder agenda items, unresolved claim discrepancies, and blocked scenarios.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "consultant_triangulate",
        "description": "Triangulate a scenario against BRD requirements, code AST symbols, and decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
                "scenario_id": {"type": "string", "description": "Scenario ID (e.g., 'SC-010' or '10')"},
            },
            "required": ["project_id", "scenario_id"],
        },
    },
    {
        "name": "consultant_handoff",
        "description": "Generate agent Markdown handoff spec for Cursor, Claude Code, or Antigravity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
                "scenario_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Scenario IDs to bundle into the handoff spec",
                },
            },
            "required": ["project_id", "scenario_ids"],
        },
    },
    {
        "name": "consultant_chat_update",
        "description": "Send a chat turn to update conversational memory (status, blockers, decisions).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
                "thread_id": {"type": "string", "description": "Thread ID"},
                "message": {"type": "string", "description": "User update message"},
                "mode": {"type": "string", "description": "'project' or 'lightweight'", "default": "project"},
            },
            "required": ["project_id", "thread_id", "message"],
        },
    },
    {
        "name": "consultant_verify_git",
        "description": "Auto-verify scenarios using a git commit diff or patch text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
                "diff_text": {"type": "string", "description": "Unified diff text"},
                "commit_sha": {"type": "string", "description": "Git commit SHA", "default": "HEAD"},
                "repo_path": {"type": "string", "description": "Optional repository path on disk"},
                "scenario_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Explicit scenario IDs to verify",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "consultant_coordinator_plan",
        "description": "Build deterministic Autonomous Coordinator execution plan (batches and escalations).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The project ID"},
            },
            "required": ["project_id"],
        },
    },
]


def handle_jsonrpc_request(db: ProjectMemoryDB, req: dict) -> Optional[dict]:
    """Process a single JSON-RPC 2.0 request dictionary."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    # Handle notifications (no response needed)
    if method == "notifications/initialized":
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {
                    "name": "ai-project-consultant-mcp",
                    "version": "1.0.0",
                },
                "capabilities": {
                    "tools": {},
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS_DEFINITIONS,
            },
        }

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments") or {}

        try:
            res_data: Any = None

            if tool_name == "consultant_status":
                pid = args["project_id"]
                consultant = TechnicalConsultant(db, pid)
                res_data = consultant.get_project_status_report()

            elif tool_name == "consultant_next_scenarios":
                pid = args["project_id"]
                limit = int(args.get("limit", 5))
                consultant = TechnicalConsultant(db, pid)
                res_data = consultant.get_next_scenarios(limit)

            elif tool_name == "consultant_agenda":
                pid = args["project_id"]
                consultant = TechnicalConsultant(db, pid)
                res_data = consultant.get_stakeholder_agenda()

            elif tool_name == "consultant_triangulate":
                pid = args["project_id"]
                sid = args["scenario_id"]
                consultant = TechnicalConsultant(db, pid)
                res_data = consultant.triangulate_scenario(sid)

            elif tool_name == "consultant_handoff":
                pid = args["project_id"]
                sids = args["scenario_ids"]
                consultant = TechnicalConsultant(db, pid)
                res_data = {"markdown": consultant.generate_agent_handoff_spec(sids)}

            elif tool_name == "consultant_chat_update":
                pid = args["project_id"]
                tid = args["thread_id"]
                msg = args["message"]
                mode = args.get("mode", "project")
                res_data = process_chat_turn(db, pid, tid, msg, mode=mode)

            elif tool_name == "consultant_verify_git":
                pid = args["project_id"]
                res_data = verify_scenarios_from_git(
                    db=db,
                    project_id=pid,
                    repo_path=args.get("repo_path"),
                    diff_text=args.get("diff_text"),
                    commit_sha=args.get("commit_sha", "HEAD"),
                    scenario_ids=args.get("scenario_ids"),
                )

            elif tool_name == "consultant_coordinator_plan":
                pid = args["project_id"]
                coordinator = AutonomousCoordinator(db, pid)
                res_data = coordinator.build_execution_plan()

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Tool '{tool_name}' not found",
                    },
                }

            text_output = (
                res_data if isinstance(res_data, str)
                else json.dumps(res_data, indent=2, default=str)
            )

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": text_output,
                        }
                    ]
                },
            }

        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32602,
                    "message": f"Missing required parameter: {exc}",
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal tool execution error: {type(exc).__name__}: {exc}",
                },
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method '{method}' not found",
        },
    }


DEFAULT_DB = os.environ.get("CONSULTANT_DB_PATH", os.environ.get("CONSULTANT_DB", str(Path(__file__).resolve().parents[1] / "consultant.db")))


def run_stdio_server(db_path: Optional[str] = None) -> None:
    """Run standard input/output loop for JSON-RPC 2.0."""
    resolved_db = db_path or DEFAULT_DB
    if resolved_db != ":memory:":
        Path(resolved_db).parent.mkdir(parents=True, exist_ok=True)

    db = ProjectMemoryDB(resolved_db)

    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line_str = line.strip()
            if not line_str:
                continue

            # Support Content-Length header framing if client sends it
            if line_str.lower().startswith("content-length:"):
                try:
                    length = int(line_str.split(":", 1)[1].strip())
                    # Consume any intervening blank lines
                    while True:
                        h = sys.stdin.readline()
                        if h.strip() == "":
                            break
                    body = sys.stdin.read(length)
                    req = json.loads(body)
                except Exception:
                    continue
            else:
                try:
                    req = json.loads(line_str)
                except Exception:
                    continue

            response = handle_jsonrpc_request(db, req)
            if response is not None:
                resp_text = json.dumps(response)
                # Emit line-delimited response
                sys.stdout.write(resp_text + "\n")
                sys.stdout.flush()
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Project Consultant MCP Stdio Server")
    parser.add_argument("--db", default=DEFAULT_DB)
    args = parser.parse_args()
    run_stdio_server(args.db)


if __name__ == "__main__":
    main()
