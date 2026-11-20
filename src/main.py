"""Process runtime HTTP app. Start with: python src/main.py"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
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


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("CDSW_APP_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port)
