"""FastAPI application entrypoint for FreshRoute: LastWindow."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.persistence.database import init_db, recover_interrupted_jobs, get_db_path, get_connection


def create_app() -> FastAPI:
    init_db()
    recover_interrupted_jobs()

    app = FastAPI(
        title="FreshRoute: LastWindow API",
        version="1.0.0",
        description="Intelligent Decision Support for Perishable Produce Distribution (PS 03)",
    )

    origins = os.environ.get(
        "FRESHROUTE_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    from app.auth import configure_auth
    configure_auth(app)

    queue_database = str(Path(get_db_path()).resolve())

    @app.on_event("startup")
    def resume_queued_work():
        conn = get_connection(queue_database)
        try:
            queued = conn.execute("SELECT 1 FROM jobs WHERE status='queued' LIMIT 1").fetchone()
        finally:
            conn.close()
        if queued:
            from app.jobs.queue import enqueue
            enqueue(queue_database)

    @app.on_event("shutdown")
    def stop_queued_work():
        from app.jobs.queue import shutdown_queue
        shutdown_queue(queue_database)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "INVALID_REQUEST", "message": str(exc.detail)}})

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="assets")

        @app.get("/", include_in_schema=False)
        def frontend():
            return FileResponse(dist / "index.html")
    return app


app = create_app()
