import uvloop
uvloop.install()

import asyncio
import importlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.engine import resolve_host_address

ROOT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = ROOT_DIR / "static"
GATEWAY_DIR = ROOT_DIR / "plugins"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
)
server_log = logging.getLogger("A360LogsSeek")


async def bootstrap_plugin_registry(application: FastAPI) -> None:
    if not GATEWAY_DIR.exists():
        GATEWAY_DIR.mkdir(parents=True)
        return
    pending_tasks = []
    for plugin_path in sorted(GATEWAY_DIR.glob("*.py")):
        if plugin_path.stem.startswith("_"):
            continue
        qualified_name = f"plugins.{plugin_path.stem}"
        try:
            loaded_module = importlib.import_module(qualified_name)
            if hasattr(loaded_module, "register"):
                pending_tasks.append(loaded_module.register(application))
            else:
                server_log.warning(f"Plugin '{plugin_path.stem}' has no register() entry point — skipped")
        except Exception as load_error:
            server_log.error(f"Failed to load plugin '{plugin_path.stem}': {load_error}")
    if pending_tasks:
        await asyncio.gather(*pending_tasks)


@asynccontextmanager
async def lifespan(application: FastAPI):
    bound_port = int(os.getenv("PORT", 8000))
    host_address = resolve_host_address()
    await bootstrap_plugin_registry(application)
    server_log.info(f"A360LogsSeek v2.3.68 — live at http://{host_address}:{bound_port}")
    server_log.info(f"API docs — http://{host_address}:{bound_port}/docs")
    yield
    server_log.info("A360LogsSeek — graceful shutdown complete")


application = FastAPI(
    title="A360LogsSeek",
    description="A360LogsSeek — Blazing-fast async credential & log search API powered by uvloop and ripgrep.",
    version="2.3.68",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


@application.get("/", include_in_schema=False)
async def render_homepage() -> FileResponse:
    homepage_path = ASSETS_DIR / "index.html"
    if not homepage_path.exists():
        return JSONResponse(status_code=404, content={"error": "index.html not found in static directory"})
    return FileResponse(homepage_path, media_type="text/html")


@application.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail or "Resource not found — visit /docs"},
    )


@application.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": "Malformed request parameters — visit /docs for schema"},
    )


@application.middleware("http")
async def request_shield_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as unhandled:
        server_log.exception(f"Unhandled exception at {request.url.path}: {unhandled}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server fault — visit /docs"},
        )


if __name__ == "__main__":
    server_host = "0.0.0.0"
    server_port = int(os.getenv("PORT", 8000))
    worker_count = int(os.getenv("WORKERS", 1))
    hot_reload = os.getenv("RELOAD", "false").lower() == "true"
    display_address = resolve_host_address()

    server_log.info(f"Booting A360LogsSeek — http://{display_address}:{server_port}")

    uvicorn.run(
        "main:application",
        host=server_host,
        port=server_port,
        loop="uvloop",
        http="httptools",
        reload=hot_reload,
        workers=worker_count if not hot_reload else 1,
        access_log=True,
        log_level="info",
    )