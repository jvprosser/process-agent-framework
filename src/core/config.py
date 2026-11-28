"""Process-wide switches. Domain packs do not read these."""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes"}


def fact_assertions_enabled() -> bool:
    """Direct fact overrides are off unless FACT_ASSERTIONS_ENABLED is set."""
    return os.environ.get("FACT_ASSERTIONS_ENABLED", "").strip().casefold() in _TRUTHY
