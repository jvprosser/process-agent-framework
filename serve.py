"""Cloudera AI application entry. Set the application Script field to serve.py."""

from __future__ import annotations

import sys
from pathlib import Path


def _project_root() -> Path:
    file_name = globals().get("__file__")
    here = Path(file_name).resolve().parent if file_name else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "src" / "main.py").is_file():
            return candidate
    raise RuntimeError("Cannot locate src/main.py. Set the application script to serve.py.")


_SRC = _project_root() / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from main import main

if __name__ == "__main__":
    main()
