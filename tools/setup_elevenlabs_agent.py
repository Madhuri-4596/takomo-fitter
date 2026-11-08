"""Create the ElevenLabs agent and its tools for The Fitter in Your Pocket.

  # put ELEVENLABS_API_KEY=... in .env first
  .venv/Scripts/python tools/setup_elevenlabs_agent.py                       # client-tool fitting (local dev)
  .venv/Scripts/python tools/setup_elevenlabs_agent.py --public-url https://fitter.example.com   # webhook fitting

With --public-url, get_fit_recommendation is a webhook tool that calls
<public-url>/api/agent/fit with a bearer secret (stored as an ElevenLabs workspace
secret) and the fitting session id (a secret dynamic variable, never shown to the LLM).
Without it, the browser runs the same fitting call as a client tool.

Writes ELEVENLABS_AGENT_ID, AGENT_TOOL_SECRET and FIT_TOOL_MODE to .env.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.voice import API, ENV_FILE, load_env  # noqa: E402

FIELDS = ["age", "sex", "handedness", "rounds_per_year", "score", "seven_iron_speed", "seven_iron_carry",
          "flight_current", "flight_desired", "characteristics", "flight", "tempo", "height", "wrist_to_floor", "glove",
          "score_representative", "lose_shots", "ball_start", "ball_curve", "ball_height", "current_clubs"]

CLIENT_TOOLS = [
    {"name": "get_fitting_state",
     "description": "Returns the golfer's confirmed profile, answers staged for review, confirmed measurements, "
                    "missing fields and the latest recommendation (with whether it is out of date). Call at the start "
                    "and after reconnecting.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "propose_profile_update",
     "description": "Stage spoken answers for the golfer to review and confirm on screen. Does NOT save them. Returns, "
                    "per answer, either a read-back text or a clarification question to ask.",
     "parameters": {"type": "object", "required": ["updates"], "properties": {
         "updates": {"type": "array", "description": "Answers the golfer just gave.",
                     "items": {"type": "object", "description": "One answer.", "required": ["field", "value"],
                               "properties": {
                                   "field": {"type": "string", "enum": FIELDS, "description": "Which answer."},
                                   "value": {"type": "string", "description": "The value exactly as heard, e.g. '5 11', '34', '160', 'M/L', 'forgiveness, distance'."},
                                   "unit": {"type": "string", "description": "cm, in, ft_in, m, mph, kmh, yards. Empty if not said."},
                                   "kind": {"type": "string", "description": "score: typical|best|handicap. seven_iron_speed: club|ball|driver. seven_iron_carry: carry|total. Empty if not said."},
                                   "heard_text": {"type": "string", "description": "The golfer's words, for the review card."}}}}}}},
    {"name": "save_profile_answer",
     "description": "Save answers the golfer just gave, in one step (the default). The server parses the value; if it is "
                    "clear it is saved at once and you get a short read_back to echo in passing. If it is ambiguous you get "
                    "a clarification question to ask instead, and nothing is saved.",
     "parameters": {"type": "object", "required": ["updates"], "properties": {
         "updates": {"type": "array", "description": "Answers the golfer just gave.",
                     "items": {"type": "object", "description": "One answer.", "required": ["field", "value"],
                               "properties": {
                                   "field": {"type": "string", "enum": FIELDS, "description": "Which answer."},
                                   "value": {"type": "string", "description": "The value exactly as heard, e.g. '5 11', '34', '160', 'M/L', 'forgiveness, distance'."},
                                   "unit": {"type": "string", "description": "cm, in, ft_in, m, mph, kmh, yards. Empty if not said."},
                                   "kind": {"type": "string", "description": "score: typical|best|handicap. seven_iron_speed: club|ball|driver. seven_iron_carry: carry|total. Empty if not said."},
                                   "heard_text": {"type": "string", "description": "The golfer's words."}}}}}}},
    {"name": "confirm_profile_update",
     "description": "Save staged answers AFTER the golfer has clearly said yes to your read-back. Only staged answers can be "
                    "saved. Leave fields empty to save all staged answers.",
     "parameters": {"type": "object", "required": [], "properties": {
         "fields": {"type": "array", "description": "Staged fields to save; empty means all.",
                    "items": {"type": "string", "description": "A field name.", "enum": FIELDS}}}}},
    {"name": "confirm_measurement",
     "description": "Save the camera measurement waiting on screen AFTER you read the values back and the golfer clearly "
                    "said yes. Then the app moves on to the swing check.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "open_measurement_step",
     "description": "Opens the measurement step with the live camera and on-screen instructions.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_measurement_status",
     "description": "Returns the actual camera state: permission, whether the camera is running, whether the printed "
                    "sheet is detected, issues observed in the image, countdown, and measurement status and values.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]
FIT_DESCRIPTION = ("Runs the Takomo fitting engine on the golfer's CONFIRMED answers and measurements. Returns the spec "
                   "(head, shaft, flex, length, lie, grip, handedness, stock or custom), the reasons, close calls and the "
                   "next useful check. The result is also shown on screen.")


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed: {e.code} {e.read().decode(errors='replace')[:800]}")


# Hands-free conversation in a noisy place (driving range, garage, garden).
# turn_eagerness starts at "normal"; "patient" is the variant to compare when golfers pause inside numbers.
BACKCHANNEL = ["yeah", "yes", "yep", "okay", "ok", "mm", "mhm", "uh huh", "right", "sure", "got it"]
ASR_KEYWORDS = ["Takomo", "7-iron", "seven iron", "carry", "club speed", "wrist-to-floor", "wrist to floor", "glove",
                "M/L", "midsize", "upright", "flat", "lie", "201 MKII", "201T", "101 MKII", "301 CB", "301 MB",
                "KBS Tour", "Tour Lite", "graphite", "steel", "stiff", "regular", "senior", "inches", "centimetres", "yards"]


MAX_CONVERSATION_S = 1800
# the greeting depends on what the app already knows (nothing yet, some answers, a reconnect): the app sends it
FIRST_MESSAGE = "{{greeting}}"
PLACEHOLDERS = {"secret__fitting_session": "none",
                "greeting": "Hi, I'm Takomo's fitting assistant. Let's find your fit. What do you usually shoot over 18 holes?"}


def audio_settings(profile: str = "normal") -> dict:
    return {
        "agent": {"disable_first_message_interruptions": True},      # background noise can't cut off the greeting
        "turn": {
            "turn_eagerness": profile,                                 # normal | patient
            "turn_timeout": 15,                                        # silence before "are you still there?": room to set up the camera
            "spelling_patience": "auto",                               # extra patience while numbers are being said
            "turn_model": "turn_v3",
            "interruption_ignore_terms": BACKCHANNEL,                  # "yeah", "mm" while the fitter talks: no interruption
            "interruption_ignore_term_languages": ["en"],
            "merge_with_default_ignore_terms": True,
            "transcribe_on_disabled_interruptions": True,
            "noisy_environment_detection": True,
            "noisy_environment_message": "Sorry, I'm picking up other voices around you. A headset mic or a quieter "
                                         "spot would help, or you can type to me instead.",
        },
        "vad": {"background_voice_detection": True},                  # ElevenLabs background-speech detection
        "asr": {"keywords": ASR_KEYWORDS},
        # a full fitting (talk, measure, three swings, questions) runs past ElevenLabs' 10-minute default
        "conversation": {"max_duration_seconds": MAX_CONVERSATION_S},
    }


def write_env(values: dict):
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    keep = [ln for ln in lines if ln.split("=", 1)[0].strip() not in values]
    keep += [f"{k}={v}" for k, v in values.items()]
    ENV_FILE.write_text("\n".join(keep) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public-url", help="public https URL of this app, enables the webhook fitting tool")
    ap.add_argument("--voice-id", default=os.environ.get("ELEVENLABS_VOICE_ID", "iP95p4xoKVk53GoZ742B"),
                    help="voice (default: Chris, a charming, down-to-earth, conversational male voice)")
    ap.add_argument("--llm", default="gemini-2.5-flash")
    ap.add_argument("--retention-days", type=int, default=30, help="days ElevenLabs keeps transcripts")
    ap.add_argument("--update-prompt", action="store_true", help="only push agent/prompt.md to the existing agent")
    ap.add_argument("--audio", choices=["normal", "patient"],
                    help="only apply the noisy-environment audio settings to the existing agent, with this turn eagerness")
    a = ap.parse_args()
    load_env()
    if not os.environ.get("ELEVENLABS_API_KEY"):
        sys.exit("Set ELEVENLABS_API_KEY in .env first.")
    if a.audio:
        agent_id = os.environ.get("ELEVENLABS_AGENT_ID") or sys.exit("No ELEVENLABS_AGENT_ID in .env.")
        cfg = audio_settings(a.audio)
        for _ in range(4):                       # drop settings this workspace's plan doesn't include, and say so
            req = urllib.request.Request(f"{API}/v1/convai/agents/{agent_id}", method="PATCH",
                                         data=json.dumps({"conversation_config": cfg}).encode(),
                                         headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=30).read()
                break
            except urllib.error.HTTPError as e:
                detail = json.loads(e.read() or b"{}").get("detail", {})
                param = detail.get("param", "") if isinstance(detail, dict) else ""
                if e.code != 403 or not param.startswith("conversation_config."):
                    sys.exit(f"PATCH failed: {e.code} {detail}")
                section, key = param.split(".")[1:3]
                print(f"not available on this plan, skipped: {section}.{key} ({detail.get('message')})")
                cfg[section].pop(key, None)
                if key == "noisy_environment_detection":
                    cfg[section].pop("noisy_environment_message", None)
        cc = call("GET", f"/v1/convai/agents/{agent_id}")["conversation_config"]
        print("turn:", {k: cc["turn"].get(k) for k in ("turn_eagerness", "spelling_patience", "turn_model",
                                                       "noisy_environment_detection", "interruption_ignore_terms")})
        print("vad:", cc.get("vad"), "| asr keywords:", len((cc.get("asr") or {}).get("keywords") or []),
              "| first message uninterruptible:", cc["agent"].get("disable_first_message_interruptions"))
        return
    if a.update_prompt:
        agent_id = os.environ.get("ELEVENLABS_AGENT_ID") or sys.exit("No ELEVENLABS_AGENT_ID in .env; run without --update-prompt first.")
        text = (ROOT / "agent" / "prompt.md").read_text(encoding="utf-8")
        # send only the prompt text: the PATCH merges, so llm and tool_ids stay as they are
        call("PATCH", f"/v1/convai/agents/{agent_id}", {"conversation_config": {"agent": {
            "prompt": {"prompt": text}, "first_message": FIRST_MESSAGE,
            "dynamic_variables": {"dynamic_variable_placeholders": PLACEHOLDERS}}}})
        # keep the client tools' definitions (e.g. new answer fields) in step with this file
        listed = call("GET", "/v1/convai/tools")
        by_name = {t["tool_config"]["name"]: t["id"] for t in listed.get("tools", []) if t["tool_config"]["type"] == "client"}
        for t in CLIENT_TOOLS:
            cfg = {"type": "client", "expects_response": True, "response_timeout_secs": 20, **t}
            if t["name"] in by_name:
                call("PATCH", f"/v1/convai/tools/{by_name[t['name']]}", {"tool_config": cfg})
                print("tool updated", t["name"])
            else:
                by_name[t["name"]] = call("POST", "/v1/convai/tools", {"tool_config": cfg})["id"]
                print("tool created", t["name"])
        current = call("GET", f"/v1/convai/agents/{agent_id}")["conversation_config"]["agent"]["prompt"].get("tool_ids") or []
        wanted = list(dict.fromkeys(current + [by_name[t["name"]] for t in CLIENT_TOOLS]))
        if wanted != current:
            call("PATCH", f"/v1/convai/agents/{agent_id}", {"conversation_config": {"agent": {"prompt": {"tool_ids": wanted}}}})
            print("agent tools attached:", len(wanted))
        print(f"prompt updated on {agent_id}")
        return

    # reuse tools from an earlier run (matched by name and type) instead of creating duplicates
    listed = call("GET", "/v1/convai/tools")
    existing = {(t["tool_config"]["name"], t["tool_config"]["type"]): t["id"] for t in listed.get("tools", [])}

    def make_tool(cfg: dict) -> str:
        key = (cfg["name"], cfg["type"])
        if key in existing:
            print("reusing", cfg["type"], "tool", cfg["name"], existing[key])
            return existing[key]
        tid = call("POST", "/v1/convai/tools", {"tool_config": cfg})["id"]
        print("created", cfg["type"], "tool", cfg["name"], tid)
        return tid

    tool_ids = [make_tool({"type": "client", "expects_response": True, "response_timeout_secs": 20, **t}) for t in CLIENT_TOOLS]

    env = {}
    if a.public_url:
        tool_secret = secrets.token_urlsafe(32)
        secret_id = call("POST", "/v1/convai/secrets",
                         {"type": "new", "name": "takomo_fitter_tool_secret", "value": f"Bearer {tool_secret}"})["secret_id"]
        cfg = {"type": "webhook", "name": "get_fit_recommendation", "description": FIT_DESCRIPTION,
               "response_timeout_secs": 30,
               "api_schema": {"url": a.public_url.rstrip("/") + "/api/agent/fit", "method": "POST",
                              "request_headers": {"Authorization": {"secret_id": secret_id},
                                                  "X-Fitting-Session": {"variable_name": "secret__fitting_session"}}}}
        env.update(AGENT_TOOL_SECRET=tool_secret, FIT_TOOL_MODE="webhook")
    else:
        cfg = {"type": "client", "name": "get_fit_recommendation", "description": FIT_DESCRIPTION,
               "expects_response": True, "response_timeout_secs": 30,
               "parameters": {"type": "object", "properties": {}, "required": []}}
        env.update(FIT_TOOL_MODE="client")
    tool_ids.append(make_tool(cfg))

    agent = {
        "name": "Takomo fitting assistant (DynamicDuo)",
        "tags": ["takomo", "hackathon"],
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "en",
                "prompt": {"prompt": (ROOT / "agent" / "prompt.md").read_text(encoding="utf-8"), "llm": a.llm,
                           "temperature": 0.6, "tool_ids": tool_ids,
                           # the golfer can end the call by voice ("hang up", "bye"): ElevenLabs' built-in tool
                           "built_in_tools": {"end_call": {"name": "end_call", "params": {"system_tool_type": "end_call"},
                                              "description": "End the call when the golfer clearly asks to, after a one-sentence goodbye."}}},
                "dynamic_variables": {"dynamic_variable_placeholders": PLACEHOLDERS},
                **audio_settings()["agent"],
            },
            # noisy-environment detection is plan-dependent: enable it with --audio once the workspace has it
            "turn": {k: v for k, v in audio_settings()["turn"].items() if not k.startswith("noisy_environment")},
            "vad": audio_settings()["vad"],
            "asr": audio_settings()["asr"],
            # English agents must use a turbo or flash v2 model
            # ElevenLabs' expressive v3 conversational model at full 44.1 kHz: the most human-sounding agent voice
            "tts": {"voice_id": a.voice_id, "model_id": "eleven_v3_conversational", "expressive_mode": True,
                    "agent_output_audio_format": "pcm_44100", "optimize_streaming_latency": 2,
                    "stability": 0.35, "similarity_boost": 0.8, "speed": 1.0},
            "conversation": {"max_duration_seconds": MAX_CONVERSATION_S, "client_events": [
                "conversation_initiation_metadata", "audio", "interruption", "user_transcript",
                "tentative_user_transcript", "agent_response", "agent_response_correction", "client_tool_call",
                "agent_tool_request", "agent_tool_response", "vad_score", "ping"]},
        },
        "platform_settings": {
            "auth": {"enable_auth": True},           # signed URLs only: the API key stays on our server
            # the "Type instead" mode starts a text-only conversation, which the SDK sends as an override
            "overrides": {"conversation_config_override": {"conversation": {"text_only": True}}},
            "privacy": {"record_voice": False, "delete_audio": True, "retention_days": a.retention_days},
        },
    }
    agent_id = call("POST", "/v1/convai/agents/create", agent)["agent_id"]
    env["ELEVENLABS_AGENT_ID"] = agent_id
    write_env(env)
    print(f"agent {agent_id} created; .env updated ({', '.join(env)})")


if __name__ == "__main__":
    main()
