"""The fitter's voice: ElevenLabs text-to-speech for the frontend's spoken lines (instead of the browser's robotic voice).

Returns MP3 audio plus the start time of every word, so the caption can highlight each word as it is said.
Lines are cached on disk by text and voice, so the frontend's repeated lines cost nothing and play at once.
The API key stays on the server.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("FITTER_TTS_CACHE") or ("/tmp/tts" if os.environ.get("VERCEL")
             else Path(__file__).resolve().parent.parent / "data" / "tts"))
MAX_CHARS = 500
VOICE_ID = os.environ.get("FITTER_VOICE_ID", "iP95p4xoKVk53GoZ742B")       # Chris: charming, down-to-earth, conversational (same as the call)
MODEL_ID = os.environ.get("FITTER_TTS_MODEL", "eleven_v3")                 # ElevenLabs' most expressive model, same family as the call
SETTINGS = {"stability": 0.35, "similarity_boost": 0.8, "use_speaker_boost": True}   # lower stability: more natural variation


class TTSError(RuntimeError):
    pass


def _word_starts(text: str, chars: list[str], starts: list[float], total: float) -> list[float]:
    """Start time of each space-separated word, matching how the frontend splits its caption."""
    words = text.split(" ")
    if "".join(chars) == text:
        pos, out = 0, []
        for w in words:
            out.append(round(starts[min(pos, len(starts) - 1)], 3))
            pos += len(w) + 1
        return out
    # the service normalised the text: spread the words over the audio by character count
    out, pos, n = [], 0, max(1, len(text))
    for w in words:
        out.append(round(total * pos / n, 3))
        pos += len(w) + 1
    return out


def speak(text: str) -> dict:
    text = " ".join(str(text).split())[:MAX_CHARS]
    if not text:
        raise TTSError("Nothing to say.")
    from . import voice                                 # reads .env (key stays on the server)
    voice.load_env()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise TTSError("The voice isn't configured on this server.")
    h = hashlib.sha256(json.dumps([text, VOICE_ID, MODEL_ID, SETTINGS], sort_keys=True).encode()).hexdigest()[:32]
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{h}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    body = json.dumps({"text": text, "model_id": MODEL_ID, "voice_settings": SETTINGS}).encode()
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/with-timestamps", data=body,
                                 method="POST", headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        raise TTSError(f"The voice service said no ({e.code}).") from None
    except (urllib.error.URLError, TimeoutError):
        raise TTSError("The voice service didn't answer.") from None
    a = d.get("alignment") or {}
    ends = a.get("character_end_times_seconds") or [0.0]
    out = {"audio": d["audio_base64"], "mime": "audio/mpeg", "duration": ends[-1],
           "word_starts": _word_starts(text, a.get("characters") or [], a.get("character_start_times_seconds") or [0.0], ends[-1])}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, f)
    return out


def check_audio(b64: str) -> int:
    return len(base64.b64decode(b64))
