"""Mistral client pinned to the configured inference server."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mistralai.client import Mistral

LOCAL_SERVER_URL = "http://localhost:8000/v1"
LOCAL_API_KEY = "dummy_local_key"
LOCAL_MODEL = "mistral"
# OpenAI-compatible base URL from the workbench. The SDK appends /v1/chat/completions.
WORKBENCH_SERVER_URL = (
    "https://ml-64288d82-5dd.go01-dem.ylcu-atmi.cloudera.site"
    "/namespaces/serving-default/endpoints/goes---nemotron-3-super-120b/v1"
)
WORKBENCH_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_JWT_PATH = Path("/tmp/jwt")


def local_server_url() -> str:
    """Inference base URL. MISTRAL_LOCAL_URL overrides the workbench endpoint."""
    configured = os.environ.get("MISTRAL_LOCAL_URL", "").strip()
    if configured:
        return configured
    if _jwt_access_token():
        return WORKBENCH_SERVER_URL
    return LOCAL_SERVER_URL


def local_api_key() -> str:
    """Bearer token for the configured inference server.

    vLLM accepts the dummy key. Cloudera AI Inference expects the workbench
    JWT from /tmp/jwt. CDSW_API_KEY is not a token that gateway accepts.
    """
    configured = os.environ.get("MISTRAL_API_KEY", "").strip()
    if configured:
        return configured
    if local_server_url() == LOCAL_SERVER_URL:
        return LOCAL_API_KEY
    token = os.environ.get("CDP_TOKEN", "").strip() or _jwt_access_token()
    if token:
        return token
    cdsw = os.environ.get("CDSW_API_KEY", "").strip()
    return cdsw or LOCAL_API_KEY


def _jwt_access_token() -> str:
    if not _JWT_PATH.is_file():
        return ""
    raw = _JWT_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict):
        token = payload.get("access_token") or payload.get("token") or ""
        return str(token).strip()
    return ""


def local_client() -> Mistral:
    """Open a client for the configured server. There is no cloud API fallback."""
    return Mistral(api_key=local_api_key(), server_url=_sdk_server_url(local_server_url()))


def local_model() -> str:
    """Model id the endpoint is serving. MISTRAL_MODEL overrides it."""
    configured = os.environ.get("MISTRAL_MODEL", "").strip()
    if configured:
        return configured
    if local_server_url() == WORKBENCH_SERVER_URL:
        return WORKBENCH_MODEL
    return LOCAL_MODEL


def _sdk_server_url(url: str) -> str:
    """The SDK joins this host with /v1/chat/completions, so drop a trailing /v1."""
    trimmed = url.rstrip("/")
    if trimmed.endswith("/v1"):
        return trimmed[: -len("/v1")]
    return trimmed
