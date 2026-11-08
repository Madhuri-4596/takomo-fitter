"""ElevenLabs Agents integration, server side.

The API key never leaves the server: the browser gets a short-lived signed URL
(valid 15 minutes to start a conversation). The fitting webhook tool is
authenticated with a shared secret stored as an ElevenLabs workspace secret.

Configuration (environment or .env in the project root):
  ELEVENLABS_API_KEY      server-side only
  ELEVENLABS_AGENT_ID     written by tools/setup_elevenlabs_agent.py
  AGENT_TOOL_SECRET       bearer secret the agent's webhook tool sends to /api/agent/fit
"""
from __future__ import annotations

import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.elevenlabs.io"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


class VoiceError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_AGENT_ID"))


def _get(path: str, params: dict | None = None, timeout=10) -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("Voice is not set up on this server (no ElevenLabs API key).", 503)
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise VoiceError(f"ElevenLabs refused the request ({e.code}).", 502)
    except (urllib.error.URLError, TimeoutError):
        raise VoiceError("Couldn't reach ElevenLabs. Check the connection and try again.", 504)


def signed_url() -> str:
    agent = os.environ.get("ELEVENLABS_AGENT_ID")
    if not agent:
        raise VoiceError("Voice is not set up on this server (no agent id).", 503)
    return _get("/v1/convai/conversation/get-signed-url", {"agent_id": agent})["signed_url"]


def conversation_token() -> str:
    """WebRTC token for a voice conversation (better echo cancellation and noise suppression than WebSocket audio)."""
    agent = os.environ.get("ELEVENLABS_AGENT_ID")
    if not agent:
        raise VoiceError("Voice is not set up on this server (no agent id).", 503)
    return _get("/v1/convai/conversation/token", {"agent_id": agent})["token"]


_privacy_cache: dict = {}


def privacy() -> dict:
    """The agent's actual retention settings, read from ElevenLabs, for the consent notice."""
    now = time.time()
    if _privacy_cache.get("at", 0) > now - 600:
        return _privacy_cache["value"]
    agent = _get(f"/v1/convai/agents/{os.environ['ELEVENLABS_AGENT_ID']}")
    p = (agent.get("platform_settings") or {}).get("privacy") or {}
    value = {"record_voice": p.get("record_voice", True), "retention_days": p.get("retention_days", -1),
             "delete_audio": p.get("delete_audio", False), "zero_retention_mode": p.get("zero_retention_mode", False),
             "fit_tool_mode": os.environ.get("FIT_TOOL_MODE", "client")}
    _privacy_cache.update(at=now, value=value)
    return value


def check_tool_secret(authorization: str | None) -> bool:
    secret = os.environ.get("AGENT_TOOL_SECRET")
    if not secret or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[7:].encode(), secret.encode())
