# Contributing to Splun // Consultant

Thank you for your interest in contributing to Splun. Splun is an open-source, deterministic project memory engine, specification triangulation platform, and autonomous coordination framework designed for AI coding workflows.

---

## 1. Development Principles

All contributions must adhere to the engineering guidelines codified in [GEMINI.md](GEMINI.md) and [DESIGN.md](DESIGN.md):
- **Deterministic Code Over Free-form Guessing**: Rely on explicit SQLite schemas, exact AST parsing, and byte-offset evidence spans rather than LLM guesswork.
- **Strict UI Design System**: All web UI elements must comply with [DESIGN.md](DESIGN.md). No arbitrary styling, no bright saturated accents, no decorative gradients, and strictly zero emojis anywhere in UI or backend messaging.
- **Automated Verification Gate**: Every pull request must pass the full test suite (`.venv/bin/pytest -v`) and WebKit geometry verification (`.venv/bin/python scripts/verify_layout.py`).

---

## 2. Environment Setup

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or standard `python3 -m venv`

### Installation
Clone the repository and set up your virtual environment:

```bash
git clone https://github.com/Rohitrajsaji/Splun.git
cd Splun

# Using uv (fastest)
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# Or using make
make install
```

If you plan to run browser layout verification tests, install Playwright browsers:

```bash
.venv/bin/playwright install webkit
```

---

## 3. Running Splun Locally

### Web Application
```bash
make run
# Opens on http://127.0.0.1:8000
```
On fresh clone, the database starts empty. You can click **Load Warehouse 72 Demo** in the top bar or run `make seed` to populate the 80-scenario benchmark.

### Command Line Interface
```bash
.venv/bin/python cli/consultant.py --help
.venv/bin/python cli/consultant.py status --project warehouse-72
```

### Model Context Protocol (MCP) Server
```bash
make mcp
# Or: .venv/bin/python cli/mcp_server.py --db consultant.db
```

---

## 4. Testing & Verification

Before submitting any changes, execute the automated verification suite:

```bash
# 1. Run unit, integration, and contract tests
make test

# 2. Run browser geometry and responsive layout audit
make verify-ui

# 3. Clean runtime database and temporary artifacts before committing
make clean
```

All 46+ tests must pass with exit code `0`.

---

## 5. Coding & Pull Request Standards

1. **Branch Naming**: Use descriptive branch names: `feature/xyz`, `fix/issue-description`, `refactor/subsystem`.
2. **Commit Hygiene**: Ensure commit messages are clear and imperative (e.g., `Add AST symbol extraction for async functions`).
3. **Zero Secrets & Portability**: Do not commit machine-specific paths, credentials, `.env` files, or binary SQLite databases.
4. **Documentation**: Update relevant sections of `README.md` or `DESIGN.md` if changing user-facing contracts, CLI arguments, or schema definitions.
