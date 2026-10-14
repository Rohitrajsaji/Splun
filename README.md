# SPLUN // CONSULTANT

**Universal AI Project Memory, Specification Triangulation & Autonomous Coordinator**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-black?style=flat-square)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-black?style=flat-square)](LICENSE)
[![Zero-AI-Slop](https://img.shields.io/badge/design-zinc--dark-black?style=flat-square)](DESIGN.md)
[![MCP Compliant](https://img.shields.io/badge/protocol-MCP--2.0-black?style=flat-square)](cli/mcp_server.py)

---

## Overview

Modern AI coding agents (Claude Code, Cursor, Antigravity, Copilot Workspace) frequently suffer from **context amnesia** and **requirement drift** across sessions. As projects scale beyond hundreds of scenarios and complex requirements, models hallucinate outdated assumptions, overwrite architectural decisions, or falsely report tasks as complete.

**Splun** (`SPLUN // CONSULTANT`) solves this by acting as an authoritative, external project memory system. Rather than relying on fuzzy context windows, Splun combines a deterministic SQLite task ledger, a bi-temporal epistemic decision graph, an immutable full-text search evidence vault, a 3-way specification triangulation engine, and an autonomous coordinator that clusters unblocked tasks while escalating blocked requirements.

---

## Core Capabilities

### 1. Multi-Source Ingestion
- **Excel & Spreadsheet Trackers**: Ingests enterprise delivery spreadsheets (80+ scenarios, verification steps, pass/fail status) using `openpyxl`.
- **Markdown BRD Parsing**: Ingests business requirements documents with structural section and rule hierarchy.
- **Python AST Codebase Scanning**: Statically maps codebase classes, functions, and docstrings using the Python `ast` module.
- **Git Commit & Diff Verification**: Directly inspects `git show` patches and commit logs to verify code changes against referenced scenarios.

### 2. 3-Way Triangulation Engine
Splun continuously cross-references three distinct epistemic sources:
1. **The Specification** (What the BRD/Excel says must happen)
2. **The Codebase** (What the AST and syntax tree actually implement)
3. **The Developer Claim** (What the AI agent or engineer claims was done)

When an agent claims a feature is implemented, Splun cross-examines the code AST and BRD contract. If discrepancies exist (e.g., BRD specifies `mandatory`, but code defines `nullable=True`), Splun flags a `DiscrepancyWarning`, blocks unverified status promotions, and demands code-level evidence.

### 3. Epistemic Ledger & Decision Graph
Every project fact is tagged with an epistemic confidence state:
- `DOCUMENTED_FACT`: Sourced directly from authoritative specifications or verified code.
- `REPORTED_CLAIM`: Unverified statements made during chat or commit messages.
- `APPROVED_DECISION`: Explicit architectural commitments approved by stakeholders.
- `VERIFIED_OUTCOME`: Automated test or verification proof confirmed on disk.

### 4. Conversational Capture with 1-Click Undo
- **Dual Chat Modes**:
  - `Project Mode`: Mutations to project scenarios, statuses, and architectural decisions are tracked and written to the database.
  - `Lightweight Mode`: Read-only advisory mode for quick codebase queries without mutating state.
- **Pre-Mutation Memory Ribbon**: Every conversational mutation creates a reversible delta snapshot. Any unwanted change can be rolled back with a single click.

### 5. Universal Portability & MCP Integration
- **Portable `.ai-memory/` Compiler**: Compiles living project state into portable Markdown bundles (`PROJECT_STATE.md`, `DECISION_LOG.md`, `SKILL.md`) for version control.
- **Zero-Dependency JSON-RPC 2.0 MCP Server**: Exposes Splun capabilities via stdio to Antigravity, Claude Code, and Cursor (`cli/mcp_server.py`), providing tools like `consultant_query`, `consultant_status`, `consultant_triangulate`, and `consultant_propose_mutation`.

### 6. Autonomous Coordinator
The autonomous coordinator engine analyzes candidate scenarios:
- **Root Cause Clustering**: Groups unblocked scenarios by shared root causes (linked requirements, overlapping code symbols) into prioritized execution batches.
- **Escalation Protocol**: Scenarios blocked by conflicting requirements or active discrepancy warnings are automatically routed to stakeholder escalation agendas with precise questions and evidence spans.

---

## System Architecture

```
                                  +---------------------------------------+
                                  |         Web UI (Projects Tab)         |
                                  |  - 3-Column Responsive Dashboard      |
                                  |  - Epistemic Badges & Undo Ribbon     |
                                  |  - Real-Time Triangulation & Batches  |
                                  +-------------------+-------------------+
                                                      |
                                                      v
                                  +---------------------------------------+
                                  |         FastAPI REST Backend          |
                                  |   api/app.py                          |
                                  +---------+-------------------+---------+
                                            |                   |
                     +----------------------+                   +---------------------+
                     v                                                                v
+------------------------------------------+                      +------------------------------------------+
|       Autonomous Coordinator & Chat      |                      |           Ingestion Subsystem            |
|   core/engine/coordinator.py             |                      |   core/ingestion/excel_parser.py         |
|   core/engine/chat_processor.py          |                      |   core/ingestion/brd_parser.py           |
|   core/engine/reconciler.py              |                      |   core/ingestion/code_scanner.py         |
|   core/engine/consultant.py              |                      |   core/ingestion/git_verifier.py         |
+--------------------+---------------------+                      +--------------------+---------------------+
                     |                                                                 |
                     +------------------------------+----------------------------------+
                                                    |
                                                    v
                                  +---------------------------------------+
                                  |     Deterministic Storage Engine      |
                                  |   core/storage/db.py (SQLite + FTS5)  |
                                  |   - Projects & Scenarios Ledger       |
                                  |   - Threads & Messages Store          |
                                  |   - Epistemic Decision Graph          |
                                  |   - Immutable Evidence Vault          |
                                  |   - Pre-Mutation Undo Snapshots       |
                                  +-------------------+-------------------+
                                                      |
                               +----------------------+----------------------+
                               v                                             v
               +-------------------------------+             +-------------------------------+
               |    CLI Tool & MCP Server      |             |     Portable Memory Bundler   |
               |  cli/consultant.py            |             |  core/engine/exporter.py      |
               |  cli/mcp_server.py            |             |  (.ai-memory/ bundles)        |
               +-------------------------------+             +-------------------------------+
```

---

## Directory Layout

```
.
|-- DESIGN.md                 # UI design system tokens, 4px grid, anti-slop rules
|-- GEMINI.md                 # Project execution SOP and verification gates
|-- Makefile                  # Project automation targets (install, run, test, seed, mcp)
|-- pyproject.toml            # PEP 621 package specification
|-- requirements.txt          # Pinned runtime and development dependencies
|-- api/
|   |-- app.py                # FastAPI REST endpoints & static file mount
|   `-- static/
|       `-- index.html        # Responsive 3-column UI adhering strictly to DESIGN.md
|-- cli/
|   |-- consultant.py         # Terminal CLI entry point
|   `-- mcp_server.py         # JSON-RPC 2.0 Model Context Protocol stdio server
|-- core/
|   |-- engine/
|   |   |-- chat_processor.py # Conversational parser, triangulation & mutation engine
|   |   |-- consultant.py     # Specification triangulator & query resolver
|   |   |-- coordinator.py    # Autonomous execution batcher & escalation router
|   |   |-- exporter.py       # Portable .ai-memory artifact compiler
|   |   `-- reconciler.py     # Discrepancy detector & claim reconciler
|   |-- ingestion/
|   |   |-- brd_parser.py     # Markdown specification parser
|   |   |-- code_scanner.py   # Python AST code scanner
|   |   |-- demo_seeder.py    # 1-Click Warehouse 72 80-scenario benchmark seeder
|   |   |-- excel_parser.py   # openpyxl spreadsheet ingestion
|   |   `-- git_verifier.py   # Git commit diff verification
|   `-- storage/
|       |-- db.py             # SQLite database layer with automated migrations
|       `-- schema.sql        # Relational schema + FTS5 full-text search vault
|-- scripts/
|   |-- live_walkthrough.py   # End-to-end programmatic verification script
|   `-- verify_layout.py      # Playwright WebKit bounding-box and responsive audit
`-- tests/
    |-- test_sprint1.py       # Storage, schema, and openpyxl ingestion tests
    |-- test_sprint2.py       # Triangulation engine & CLI tests
    |-- test_sprint3.py       # Conversational memory, undo ribbon & API tests
    |-- test_sprint4.py       # Git verifier, .ai-memory exporter & MCP server tests
    `-- test_sprint5.py       # Autonomous coordinator & 80-scenario benchmark tests
```

---

## Quickstart

### 1. Installation
Clone the repository and install dependencies into a Python 3.11+ virtual environment:

```bash
git clone https://github.com/Rohitrajsaji/Splun.git
cd Splun

# Create virtual environment and install dependencies
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

### 2. Launch the Application
Start the local server:

```bash
make run
```

Open your browser to `http://127.0.0.1:8000`.

On first run, the database is completely empty (`0` projects). You can immediately:
- Click **`+ New Project`** to create a custom project from scratch.
- Click **`Load Warehouse 72 Demo`** (`#btn-load-demo`) in the top bar (or run `make seed` in your terminal) to load the complete 80-scenario, 7-batch enterprise warehouse benchmark.

### 3. CLI Usage
Inspect project status, query unblocked work, or generate agent handoffs directly from your shell:

```bash
# Display project scenarios and completion metrics
.venv/bin/python cli/consultant.py status --project warehouse-72

# Retrieve next unblocked scenarios ready for implementation
.venv/bin/python cli/consultant.py next --project warehouse-72 --limit 5

# View open stakeholder clarification questions and blockers
.venv/bin/python cli/consultant.py agenda --project warehouse-72

# Generate agent Markdown handoff specification
.venv/bin/python cli/consultant.py handoff --project warehouse-72 --scenarios SC-004,SC-005
```

### 4. Model Context Protocol (MCP) Integration
Splun includes an MCP server that integrates with Claude Code, Cursor, and Antigravity:

```bash
make mcp
```

To configure in your IDE or agent, copy `.mcp.json.example` to `.mcp.json`:

```json
{
  "mcpServers": {
    "project-consultant": {
      "command": ".venv/bin/python",
      "args": ["cli/mcp_server.py", "--db", "consultant.db"]
    }
  }
}
```

---

## Automated Verification Gate

Splun enforces verification through automated test suites and WebKit visual layout checks:

```bash
# Run full unit and integration test suite (46+ tests)
make test

# Run browser layout geometry audit across desktop, laptop, tablet, and mobile viewports
make verify-ui

# Clean temporary databases and test artifacts
make clean
```

---

## License

This project is licensed under the [MIT License](LICENSE).
