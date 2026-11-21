"""Mistral client pinned to the configured inference server."""

from __future__ import annotations

import json
import os
import re
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
    if _JWT_PATH.is_file():
        return WORKBENCH_SERVER_URL
    return LOCAL_SERVER_URL


def local_api_key() -> str:
    """Bearer token for the configured inference server.

    vLLM accepts the dummy key. Cloudera AI Inference expects the access_token
    in /tmp/jwt. A Knox HTML error page is not a token.
    """
    for candidate in (
        os.environ.get("MISTRAL_API_KEY", ""),
        _jwt_access_token(),
        os.environ.get("CDP_TOKEN", ""),
        os.environ.get("CDSW_API_KEY", ""),
    ):
        token = _bearer(candidate)
        if token:
            return token
    url = local_server_url()
    if url == WORKBENCH_SERVER_URL or "cloudera.site" in url:
        raise RuntimeError(_token_problem())
    return LOCAL_API_KEY


def _bearer(value: str) -> str:
    """A header-safe token. HTML and other multi-line pages are rejected."""
    token = (value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token or any(ch.isspace() for ch in token) or "<" in token:
        return ""
    return token


def _jwt_access_token() -> str:
    if not _JWT_PATH.is_file():
        return ""
    raw = _JWT_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _bearer(raw)
    if isinstance(payload, dict):
        token = payload.get("access_token") or payload.get("token") or ""
        return _bearer(str(token))
    return ""


def _token_problem() -> str:
    raw = ""
    if _JWT_PATH.is_file():
        raw = _JWT_PATH.read_text(encoding="utf-8", errors="replace")
    for source in (raw, os.environ.get("CDP_TOKEN", ""), os.environ.get("MISTRAL_API_KEY", "")):
        match = re.search(r"/[A-Za-z0-9_./-]*knoxtoken/api/v2/token", source)
        if match:
            return (
                "The inference credential is a Knox error page, not an access token. "
                f"Knox returned 404 for {match.group(0)}."
            )
    return "No access token is available. /tmp/jwt does not contain an access_token."


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
