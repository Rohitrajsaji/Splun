.PHONY: help install run test seed mcp verify-ui clean

PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
PYTEST ?= .venv/bin/pytest

help:
	@echo "SPLUN // CONSULTANT - Available targets:"
	@echo "  make install    - Create virtual environment and install dependencies"
	@echo "  make run        - Start the FastAPI web application on 127.0.0.1:8000"
	@echo "  make test       - Run full test suite with pytest"
	@echo "  make seed       - Seed Warehouse 72 (80-scenario) benchmark into consultant.db"
	@echo "  make mcp        - Start MCP stdio server targeting consultant.db"
	@echo "  make verify-ui  - Run WebKit browser layout and geometry verification"
	@echo "  make clean      - Remove SQLite database files, caches, and test artifacts"

install:
	uv venv --python 3.11 .venv || python3 -m venv .venv
	uv pip install --python $(PYTHON) -r requirements.txt || $(PYTHON) -m pip install -r requirements.txt

run:
	$(UVICORN) api.app:app --host 127.0.0.1 --port 8000

test:
	$(PYTEST) -v

seed:
	$(PYTHON) core/ingestion/demo_seeder.py

mcp:
	$(PYTHON) cli/mcp_server.py --db consultant.db

verify-ui:
	$(PYTHON) scripts/verify_layout.py

clean:
	rm -f consultant.db consultant.db-wal consultant.db-shm consultant.db-journal
	rm -rf .pytest_cache __pycache__ */__pycache__ */*/__pycache__
	rm -f output.txt
