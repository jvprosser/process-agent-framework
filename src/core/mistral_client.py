"""Mistral client pinned to the local inference server."""

from __future__ import annotations

import os

from mistralai.client import Mistral

LOCAL_SERVER_URL = "http://localhost:8000/v1"
LOCAL_API_KEY = "dummy_local_key"
LOCAL_MODEL = "mistral"


def local_server_url() -> str:
    """Local inference URL. MISTRAL_LOCAL_URL overrides the vLLM default."""
    configured = os.environ.get("MISTRAL_LOCAL_URL", "").strip()
    return configured or LOCAL_SERVER_URL


def local_client() -> Mistral:
    """Open a client for the local server. There is no cloud API fallback."""
    return Mistral(api_key=LOCAL_API_KEY, server_url=local_server_url())


def local_model() -> str:
    """Model name the local server is serving."""
    return os.environ.get("MISTRAL_MODEL", LOCAL_MODEL)
