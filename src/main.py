"""Process runtime HTTP app.

Cloudera AI applications take a Python file, not a shell command.
Set the application Script field to serve.py at the project root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _source_dir() -> Path:
    """Directory that contains api/ and ui/. Notebooks do not set __file__."""
    file_name = globals().get("__file__")
    if file_name:
        return Path(file_name).resolve().parent
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "api" / "chat.py").is_file():
            return candidate
        nested = candidate / "src"
        if (nested / "api" / "chat.py").is_file():
            return nested
    raise RuntimeError(
        "Cannot locate src/. Set the Cloudera application script to serve.py."
    )


_SRC = _source_dir()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.action import router as action_router
from api.chat import router


def _allowed_ui_origins() -> set[str]:
    raw = os.getenv("PROCESS_AGENT_UI_ORIGIN", "")
    return {part.strip().rstrip("/") for part in raw.split(",") if part.strip()}


def _request_origin(scope) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"origin":
            return value.decode("latin-1").strip().rstrip("/")
    return ""


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-allow-methods", b"POST, OPTIONS"),
        (b"access-control-allow-headers", b"Content-Type"),
        (b"vary", b"Origin"),
    ]


class UiCorsMiddleware:
    """Allow one separate UI application to call this API from the browser."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        origin = _request_origin(scope)
        allowed = bool(origin) and origin in _allowed_ui_origins()
        if allowed and scope["method"] == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": _cors_headers(origin),
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message):
            if allowed and message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(_cors_headers(origin))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cors)


app = FastAPI(title="Process agent")
app.add_middleware(UiCorsMiddleware)
app.include_router(router)
app.include_router(action_router)
app.mount(
    "/",
    StaticFiles(directory=str(_SRC / "ui"), html=True),
    name="ui",
)


def main() -> None:
    """Bind the UI on 127.0.0.1 and CDSW_APP_PORT (default 8080)."""
    import asyncio
    import os

    import uvicorn

    host = "127.0.0.1"
    port = int(os.getenv("CDSW_APP_PORT", "8080"))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        config = uvicorn.Config(app, host=host, port=port)
        server = uvicorn.Server(config)
        task = loop.create_task(server.serve())
        task.add_done_callback(_report_server_task)
        print(f"Serving on http://{host}:{port}")
        return
    uvicorn.run(app, host=host, port=port)


def _report_server_task(task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        print(f"Server stopped: {error}")


if __name__ == "__main__" and globals().get("__file__"):
    main()
