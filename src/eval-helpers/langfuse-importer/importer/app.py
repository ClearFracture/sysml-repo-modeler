from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .service import ImporterService

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
UI_DIST = ROOT_DIR / "ui" / "dist"


def create_app(data_dir: Path | None = None) -> FastAPI:
    service = ImporterService(data_dir or DATA_DIR)
    app = FastAPI(title="SysML Langfuse Importer", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "langfuse-importer", "version": __version__}

    @app.get("/api/config")
    def get_config() -> dict[str, object]:
        return service.get_config()

    @app.put("/api/config")
    def put_config(payload: dict[str, Any] = Body(default=None)) -> dict[str, object]:
        return service.update_config(payload or {})

    @app.get("/api/scans")
    def list_scans() -> dict[str, Any]:
        scans = service.list_scans()
        return {
            "runs": scans,
            "summary": _scan_summary(scans),
        }

    @app.post("/api/scans/refresh")
    def refresh_scans() -> dict[str, Any]:
        try:
            return service.refresh_scans()
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @app.post("/api/scans/{run_id}/push")
    def push_scan(run_id: str) -> dict[str, Any]:
        try:
            return service.push_scan(run_id)
        except RuntimeError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/scans/push-pending")
    def push_pending() -> dict[str, Any]:
        return service.push_pending()

    if UI_DIST.is_dir():
        assets_dir = UI_DIST / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(UI_DIST / "index.html")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not found.")
            candidate = UI_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

    app.state.service = service
    return app


def _scan_summary(scans: list[dict[str, Any]]) -> dict[str, int]:
    pending = sum(1 for scan in scans if scan.get("needsPush"))
    pushed = sum(1 for scan in scans if scan.get("pushStatus") == "success")
    failed = sum(1 for scan in scans if scan.get("pushStatus") == "failed")
    stale = sum(1 for scan in scans if scan.get("pushStatus") == "stale")
    return {
        "total": len(scans),
        "pending": pending,
        "pushed": pushed,
        "failed": failed,
        "stale": stale,
    }


app = create_app()
