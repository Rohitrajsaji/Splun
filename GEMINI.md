# Antigravity SOP: AI Project Consultant Build

## Operating Constraints
1. Execution Environment: Always run Python and pytest commands through `.venv/bin/python` and `.venv/bin/pytest`.
2. Token Discipline: Use `ctx_search` and `ctx_execute` (from context-mode) rather than dumping large file trees or dumps into context[cite: 7].
3. Code Beats Model: Prefer deterministic SQLite schemas, exact openpyxl spreadsheet parsing, and byte-offset evidence spans over free-form LLM guessing[cite: 10].
4. Automated Verification Gate: After implementing or changing any code, automatically execute `.venv/bin/pytest -v`. If any test fails, inspect the traceback, resolve the issue, and rerun until exit code is 0.
