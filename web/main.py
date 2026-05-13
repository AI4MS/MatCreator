"""Lightweight FastAPI server that exposes agent graph data to the frontend.

Runs on port 8001 alongside the ADK backend (port 8000).

Endpoints
---------
GET /api/agent-graph/{session_id}
    Returns the JSON graph file for the session, or an empty graph if not found.
GET /api/workspace/files?path=<path>
    Serves any file from the workspace root (absolute or relative path).
    Returns 403 if the path escapes the workspace root.
GET /api/sessions/{session_id}/files
    Lists all files under the session's working directory.

The vite dev server proxies /api/* here and /run_sse + /apps/* to the ADK server.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Allow importing from agents/ and src/ when running as script
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.MatCreator.workspace import get_session_workdir, get_workspace_root  # noqa: E402

app = FastAPI(title="MatCreator Graph API", version="1.0.0")
APP_NAME = "MatCreator"
SESSION_DB_PATH = ROOT / "agents" / "MatCreator" / ".adk" / "session.db"
DEFAULT_ADMIN_USERS = {"admin"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _admin_users() -> set[str]:
    raw_value = os.environ.get("MATCREATOR_ADMIN_USERS")
    if raw_value is None:
        return DEFAULT_ADMIN_USERS.copy()

    return {
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    }


def _is_admin(user_id: str) -> bool:
    return user_id in _admin_users()


def _session_row_to_summary(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "appName": row["app_name"],
        "userId": row["user_id"],
        "createTime": row["create_time"],
        "lastUpdateTime": row["update_time"],
    }


def _query_session_summaries(user_id: str | None = None) -> list[dict]:
    if not SESSION_DB_PATH.exists():
        return []

    where_clause = "WHERE app_name = ?"
    params: tuple[str, ...] = (APP_NAME,)
    if user_id is not None:
        where_clause += " AND user_id = ?"
        params = (APP_NAME, user_id)

    try:
        with sqlite3.connect(SESSION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT app_name, user_id, id, create_time, update_time
                FROM sessions
                {where_clause}
                ORDER BY update_time DESC
                """,
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read sessions: {exc}")

    return [_session_row_to_summary(row) for row in rows]


def _load_json_field(raw_value: str | None, fallback):
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return fallback


@app.get("/api/session-access/{user_id}")
async def get_session_access(user_id: str) -> JSONResponse:
    return JSONResponse({"user_id": user_id, "is_admin": _is_admin(user_id)})


@app.get("/api/users/{user_id}/sessions")
async def list_user_sessions(user_id: str) -> JSONResponse:
    return JSONResponse(_query_session_summaries(user_id))


@app.get("/api/users/{user_id}/sessions/{session_id}")
async def get_user_session(user_id: str, session_id: str) -> JSONResponse:
    if not SESSION_DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        with sqlite3.connect(SESSION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            session = conn.execute(
                """
                SELECT app_name, user_id, id, state, create_time, update_time
                FROM sessions
                WHERE app_name = ? AND user_id = ? AND id = ?
                """,
                (APP_NAME, user_id, session_id),
            ).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")

            event_rows = conn.execute(
                """
                SELECT event_data
                FROM events
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                ORDER BY timestamp ASC
                """,
                (APP_NAME, user_id, session_id),
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read session: {exc}")

    summary = _session_row_to_summary(session)
    summary["state"] = _load_json_field(session["state"], {})
    summary["events"] = [
        _load_json_field(row["event_data"], {})
        for row in event_rows
    ]
    return JSONResponse(summary)


@app.get("/api/admin/sessions")
async def list_all_sessions(user_id: str = Query(..., description="Current signed-in user")) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return JSONResponse(_query_session_summaries())


@app.get("/api/agent-graph/{session_id}")
async def get_agent_graph(session_id: str) -> JSONResponse:
    graph_path = get_workspace_root() / "agent_graphs" / f"{session_id}.json"
    if not graph_path.exists():
        return JSONResponse({"session_id": session_id, "nodes": {}, "edges": [], "updated_at": None})
    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return JSONResponse({"session_id": session_id, "nodes": {}, "edges": [], "updated_at": None})
    return JSONResponse(data)


@app.get("/api/workspace/files")
async def serve_workspace_file(path: str = Query(..., description="Absolute or workspace-relative file path")) -> FileResponse:
    ws_root = get_workspace_root().resolve()
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
    if not resolved.is_relative_to(ws_root):
        raise HTTPException(status_code=403, detail="Access denied: path is outside workspace")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(resolved)


@app.get("/api/structure/view")
async def view_structure(path: str = Query(..., description="Absolute or workspace-relative structure file path")) -> JSONResponse:
    from io import StringIO

    try:
        from ase.io import read as ase_read
        from ase.io import write as ase_write
    except ImportError:
        raise HTTPException(status_code=500, detail="ASE is not installed")

    ws_root = get_workspace_root().resolve()
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
    if not resolved.is_relative_to(ws_root):
        raise HTTPException(status_code=403, detail="Access denied: path is outside workspace")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        atoms = ase_read(str(resolved))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse structure: {exc}")

    buf = StringIO()
    ase_write(buf, atoms, format="xyz")
    xyz_data = buf.getvalue()

    return JSONResponse({
        "xyz": xyz_data,
        "formula": atoms.get_chemical_formula(),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
        "cell": atoms.cell.tolist() if atoms.pbc.any() else None,
    })


@app.get("/api/sessions/{session_id}/files")
async def list_session_files(session_id: str) -> JSONResponse:
    session_dir = get_session_workdir(session_id)
    if not session_dir.exists():
        return JSONResponse({"files": []})
    files = [
        {"name": f.name, "path": str(f), "size": f.stat().st_size}
        for f in sorted(session_dir.rglob("*"))
        if f.is_file()
    ]
    return JSONResponse({"files": files})


# Serve built frontend in production
_dist = Path(__file__).parent / "vite-frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
