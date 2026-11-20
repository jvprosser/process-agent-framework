"""Process runtime HTTP app.

Cloudera AI applications take a Python file, not a shell command.
Set the application Script field to serve.py at the project root.
"""

from __future__ import annotations

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

app = FastAPI(title="Process agent")
app.include_router(router)
app.include_router(action_router)
app.mount(
    "/",
    StaticFiles(directory=str(_SRC / "ui"), html=True),
    name="ui",
)


def main() -> None:
    """Bind the UI on 127.0.0.1 and CDSW_APP_PORT (default 8080)."""
    import os

    import uvicorn

    port = int(os.getenv("CDSW_APP_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__" and globals().get("__file__"):
    main()
