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


def local_api_key() -> str:
    """Bearer token for the configured inference server.

    vLLM accepts the dummy key. Cloudera AI Inference is behind Knox and
    rejects that key, so a non-default server URL uses CDSW_API_KEY.
    """
    configured = os.environ.get("MISTRAL_API_KEY", "").strip()
    if configured:
        return configured
    if local_server_url() != LOCAL_SERVER_URL:
        cdsw = os.environ.get("CDSW_API_KEY", "").strip()
        if cdsw:
            return cdsw
    return LOCAL_API_KEY


def local_client() -> Mistral:
    """Open a client for the configured server. There is no cloud API fallback."""
    return Mistral(api_key=local_api_key(), server_url=local_server_url())


def local_model() -> str:
    """Model name the local server is serving."""
    return os.environ.get("MISTRAL_MODEL", LOCAL_MODEL)
