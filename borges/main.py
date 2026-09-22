"""FastAPI application factory: local-only middleware, API routers, static SPA."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .api import ROUTERS
from .config import Config
from .services import Services

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"}


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = Services(config)
        app.state.services = services
        services.start()
        logging.getLogger("borges").info("Borges's Hoard %s — data in %s", __version__, config.data_dir)
        try:
            yield
        finally:
            services.stop()

    app = FastAPI(title="Borges's Hoard", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.config = config

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"error": "Only local access is allowed."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"http://{request.headers.get('host')}" and origin not in DEV_ORIGINS:
            return JSONResponse({"error": "Origin not allowed."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Cross-site requests are not allowed."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        issues = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'input'}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"error": issues}, status_code=400)

    for router in ROUTERS:
        app.include_router(router)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": "Not found."}, status_code=404)
        candidate = (STATIC_DIR / path).resolve() if path else None
        if candidate and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"error": "The client is not built yet: run `npm install && npm run build`."}, status_code=503)

    return app
