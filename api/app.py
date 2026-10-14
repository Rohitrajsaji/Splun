"""FastAPI Backend for AI Project Consultant.

Exposes REST APIs for:
- Projects management
- Multi-source knowledge ingestion (Excel, Markdown BRD, Python AST Code)
- Multi-thread chat with reversible memory ribbon
- Live sidecar state (status report, scenarios, decisions, agenda, next actions)
- Agent handoff spec generation
- Serving the 3-column Projects Tab web UI
"""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.engine.chat_processor import process_chat_turn
from core.engine.consultant import TechnicalConsultant
from core.engine.coordinator import AutonomousCoordinator
from core.engine.exporter import export_portable_memory_bundle
from core.ingestion.brd_parser import ingest_brd_markdown
from core.ingestion.code_scanner import scan_python_codebase
from core.ingestion.demo_seeder import seed_warehouse72_demo
from core.ingestion.excel_parser import ingest_scenarios_excel
from core.ingestion.git_verifier import verify_scenarios_from_git
from core.storage.db import ProjectMemoryDB, ProjectNotFoundError

STATIC_DIR = Path(__file__).parent / "static"


# --------------------------------------------------------------------------- #
# Request Schemas
# --------------------------------------------------------------------------- #
class CreateProjectRequest(BaseModel):
    project_id: str
    name: str
    repo_path: Optional[str] = None


class CreateThreadRequest(BaseModel):
    title: str = "New Thread"
    mode: str = "project"  # 'project' or 'lightweight'


class ChatTurnRequest(BaseModel):
    message: str
    mode: Optional[str] = None
    claims: Optional[list[dict]] = None


class HandoffRequest(BaseModel):
    scenarios: list[str] | str


class VerifyGitRequest(BaseModel):
    diff_text: Optional[str] = None
    commit_sha: str = "HEAD"
    repo_path: Optional[str] = None
    scenario_ids: Optional[list[str]] = None


class ExportBundleRequest(BaseModel):
    output_dir: Optional[str] = None


class IngestExcelRequest(BaseModel):
    file_path: str


class IngestBrdRequest(BaseModel):
    content: Optional[str] = None
    file_path: Optional[str] = None
    source_name: Optional[str] = "BRD.md"


class IngestCodeRequest(BaseModel):
    root_dir: Optional[str] = None
    filename: Optional[str] = None
    code: Optional[str] = None


# --------------------------------------------------------------------------- #
# Sidecar Helper
# --------------------------------------------------------------------------- #
def get_sidecar_state(db: ProjectMemoryDB, project_id: str) -> dict:
    consultant = TechnicalConsultant(db, project_id)
    return {
        "status_report": consultant.get_project_status_report(),
        "scenarios": db.get_scenarios(project_id),
        "active_decisions": db.get_decisions(project_id, active_only=True),
        "stakeholder_agenda": consultant.get_stakeholder_agenda(),
        "next_scenarios": consultant.get_next_scenarios(limit=10),
    }


# --------------------------------------------------------------------------- #
# Application Factory
# --------------------------------------------------------------------------- #
def create_app(db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="AI Project Consultant", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    default_db = os.environ.get("CONSULTANT_DB_PATH", os.environ.get("CONSULTANT_DB", str(Path(__file__).resolve().parents[1] / "consultant.db")))
    resolved_db_path = db_path or default_db
    if resolved_db_path != ":memory:":
        Path(resolved_db_path).parent.mkdir(parents=True, exist_ok=True)

    # Attach shared database instance to app state
    app.state.db = ProjectMemoryDB(resolved_db_path)
    app.state.db_path = resolved_db_path

    def get_db() -> ProjectMemoryDB:
        return app.state.db

    # ----------------------------------------------------------------------- #
    # Project Endpoints
    # ----------------------------------------------------------------------- #
    @app.get("/api/projects")
    def list_projects(db: ProjectMemoryDB = Depends(get_db)):
        return db.list_projects()

    @app.post("/api/projects", status_code=status.HTTP_201_CREATED)
    def create_project(req: CreateProjectRequest, db: ProjectMemoryDB = Depends(get_db)):
        try:
            db.create_project(req.project_id, req.name, req.repo_path)
            return db.get_project(req.project_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, db: ProjectMemoryDB = Depends(get_db)):
        p = db.get_project(project_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
        return p

    # ----------------------------------------------------------------------- #
    # Knowledge Ingestion Endpoints
    # ----------------------------------------------------------------------- #
    @app.post("/api/projects/{project_id}/ingest/excel")
    async def ingest_excel_endpoint(
        project_id: str,
        request: Request,
        file: Optional[UploadFile] = File(None),
        db: ProjectMemoryDB = Depends(get_db),
    ):
        db.require_project(project_id)
        try:
            if file is not None:
                content = await file.read()
                stream = io.BytesIO(content)
                stream.name = file.filename or "uploaded.xlsx"
                res = ingest_scenarios_excel(db, project_id, stream)
            else:
                body = await request.json()
                file_path = body.get("file_path")
                if not file_path:
                    raise HTTPException(status_code=400, detail="file_path or uploaded file required")
                res = ingest_scenarios_excel(db, project_id, file_path)
            return {"success": True, "sidecar": get_sidecar_state(db, project_id), **res}
        except (ValueError, FileNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/ingest/brd")
    async def ingest_brd_endpoint(
        project_id: str,
        request: Request,
        file: Optional[UploadFile] = File(None),
        db: ProjectMemoryDB = Depends(get_db),
    ):
        db.require_project(project_id)
        try:
            if file is not None:
                raw_bytes = await file.read()
                content = raw_bytes.decode("utf-8")
                res = ingest_brd_markdown(db, project_id, file.filename or "BRD.md", content=content)
            else:
                body = await request.json()
                content = body.get("content")
                file_path = body.get("file_path")
                source_name = body.get("source_name") or "BRD.md"
                if content is not None:
                    res = ingest_brd_markdown(db, project_id, source_name, content=content)
                elif file_path:
                    res = ingest_brd_markdown(db, project_id, file_path)
                else:
                    raise HTTPException(status_code=400, detail="content or file_path required")
            return {"success": True, "sidecar": get_sidecar_state(db, project_id), **res}
        except (ValueError, FileNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/ingest/code")
    def ingest_code_endpoint(
        project_id: str,
        req: IngestCodeRequest,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        db.require_project(project_id)
        try:
            if req.code:
                # Write inline code snippet to project workspace
                ws_dir = Path(".consultant") / "workspaces" / project_id
                ws_dir.mkdir(parents=True, exist_ok=True)
                fn = req.filename or "models.py"
                target_file = ws_dir / fn
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(req.code, encoding="utf-8")
                res = scan_python_codebase(db, project_id, str(ws_dir))
            elif req.root_dir:
                res = scan_python_codebase(db, project_id, req.root_dir)
            else:
                raise HTTPException(status_code=400, detail="root_dir or code snippet required")
            return {"success": True, "sidecar": get_sidecar_state(db, project_id), **res}
        except (ValueError, FileNotFoundError, NotADirectoryError, LookupError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ----------------------------------------------------------------------- #
    # Thread & Message Endpoints
    # ----------------------------------------------------------------------- #
    @app.get("/api/projects/{project_id}/threads")
    def list_threads_endpoint(project_id: str, db: ProjectMemoryDB = Depends(get_db)):
        try:
            return db.list_threads(project_id)
        except ProjectNotFoundError:
            raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")

    @app.post("/api/projects/{project_id}/threads", status_code=status.HTTP_201_CREATED)
    def create_thread_endpoint(
        project_id: str,
        req: CreateThreadRequest,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            return db.create_thread(project_id, title=req.title, mode=req.mode)
        except (ValueError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/projects/{project_id}/threads/{thread_id}/messages")
    def get_messages_endpoint(
        project_id: str,
        thread_id: str,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            return db.get_thread_messages(project_id, thread_id)
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/projects/{project_id}/threads/{thread_id}/chat")
    def chat_turn_endpoint(
        project_id: str,
        thread_id: str,
        req: ChatTurnRequest,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            result = process_chat_turn(
                db=db,
                project_id=project_id,
                thread_id=thread_id,
                user_message=req.message,
                mode=req.mode,
                claims=req.claims,
            )
            sidecar = get_sidecar_state(db, project_id)
            return {**result, "sidecar": sidecar}
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/messages/{message_id}/undo")
    def undo_message_endpoint(
        project_id: str,
        message_id: str,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            undo_res = db.undo_message_mutations(project_id, message_id)
            sidecar = get_sidecar_state(db, project_id)
            return {**undo_res, "sidecar": sidecar}
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ----------------------------------------------------------------------- #
    # Live Sidecar & Agent Handoff Endpoints
    # ----------------------------------------------------------------------- #
    @app.get("/api/projects/{project_id}/sidecar")
    def sidecar_endpoint(project_id: str, db: ProjectMemoryDB = Depends(get_db)):
        try:
            return get_sidecar_state(db, project_id)
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/projects/{project_id}/handoff")
    def handoff_endpoint(
        project_id: str,
        req: HandoffRequest,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            consultant = TechnicalConsultant(db, project_id)
            if isinstance(req.scenarios, str):
                sc_list = [s.strip() for s in req.scenarios.split(",") if s.strip()]
            else:
                sc_list = req.scenarios
            md = consultant.generate_agent_handoff_spec(sc_list)
            return {"markdown": md}
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/verify/git")
    def verify_git_endpoint(
        project_id: str,
        req: VerifyGitRequest,
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            res = verify_scenarios_from_git(
                db=db,
                project_id=project_id,
                repo_path=req.repo_path,
                diff_text=req.diff_text,
                commit_sha=req.commit_sha,
                scenario_ids=req.scenario_ids,
            )
            sidecar = get_sidecar_state(db, project_id)
            return {**res, "sidecar": sidecar}
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (ValueError, RuntimeError, NotADirectoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/{project_id}/export")
    def export_bundle_endpoint(
        project_id: str,
        req: Optional[ExportBundleRequest] = Body(None),
        db: ProjectMemoryDB = Depends(get_db),
    ):
        try:
            out_dir = (req.output_dir if req and req.output_dir else None) or f".consultant/exports/{project_id}"
            return export_portable_memory_bundle(db, project_id, out_dir)
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ----------------------------------------------------------------------- #
    # Sprint 5: Autonomous Coordinator & Benchmark Demo Endpoints
    # ----------------------------------------------------------------------- #
    @app.get("/api/projects/{project_id}/coordinator/plan")
    def coordinator_plan_endpoint(project_id: str, db: ProjectMemoryDB = Depends(get_db)):
        try:
            coordinator = AutonomousCoordinator(db, project_id)
            return coordinator.build_execution_plan()
        except (LookupError, ProjectNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/projects/demo/warehouse72")
    def seed_warehouse72_demo_endpoint(db: ProjectMemoryDB = Depends(get_db)):
        try:
            res = seed_warehouse72_demo(db, project_id="warehouse-72")
            sidecar = get_sidecar_state(db, "warehouse-72")
            return {**res, "sidecar": sidecar}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ----------------------------------------------------------------------- #
    # Static Assets & Web UI
    # ----------------------------------------------------------------------- #
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.is_file():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>AI Project Consultant</h1><p>UI loading...</p>")

    return app


# Default app instance
app = create_app()
