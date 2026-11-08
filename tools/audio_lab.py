"""Noisy-environment voice tests against the REAL ElevenLabs agent (uses agent minutes).

The browser's microphone is replaced by a synthetic stream: the golfer's lines (Windows text-to-speech)
are played only after the agent has asked and stopped talking, over a continuous background bed
(nearby voices or keyboard clicks). Each scenario reports TRANSCRIPTION separately from ANSWERS:

  transcription  what the agent heard vs the words actually said (word error rate)
  answers        whether the right value was staged, read back and saved, and no value was invented

Scenarios: clean, nearby voices, keyboard, pause inside a number, self-correction, talking over the agent,
speaker echo (the agent's own voice fed back into the mic).

  .venv/Scripts/python tools/audio_lab.py [--only clean,voices] [--recordings DIR]

--recordings DIR: use your own WAV recordings as background beds (e.g. range.wav, garage.wav): each file
becomes an extra "nearby voices"-style scenario, so settings can be tuned on your real environment.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(tempfile.gettempdir()) / "takomo_audio_lab"
RATE = 48000
BASE = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
        "speed_or_carry": "speed", "speed_mph": 75, "characteristics": ["forgiveness"], "flight": "mid",
        "tempo": "moderate", "glove": "M"}          # height and wrist-to-floor left for the agent to ask

GOLFER = {
    "wtf35": "My wrist to floor is thirty five inches.",
    "height_pause": "I'm five foot <break time='1500ms'/> ten.",
    "height": "I'm five foot ten in my golf shoes.",
    "correction": "Thirty four inches. No, sorry, thirty five inches.",
    "yes": "Yes, that's right.",
}
SAID = {k: re.sub(r"<[^>]+>", " ", v) for k, v in GOLFER.items()}
BABBLE = ("Did you see the match last night? I think we should grab lunch after this bucket. "
          "Seven o'clock tee time tomorrow, don't be late. My driver has been terrible all week, honestly. "
          "Can you pass me a few more balls? The wind is picking up over there. Twelve dollars for a bucket now.")

# scenario: background bed, gain, golfer lines in order, expected profile values
SCENARIOS = {
    "clean":      {"bed": None, "lines": ["height", "wtf35"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "voices":     {"bed": "babble", "gain": 0.35, "lines": ["height", "wtf35"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "keyboard":   {"bed": "keyboard", "gain": 0.5, "lines": ["height", "wtf35"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "pause":      {"bed": None, "lines": ["height_pause", "wtf35"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "correction": {"bed": None, "lines": ["height", "correction"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "overlap":    {"bed": None, "lines": ["height", "wtf35"], "overlap": True, "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
    "echo":       {"bed": None, "echo": 0.6, "lines": ["height", "wtf35"], "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}},
}


# ------------------------------------------------------------------ audio fixtures
def tts(name: str, text: str, voice: str = "Microsoft David Desktop", rate: int = 0) -> Path:
    out = LAB / f"{name}.wav"
    if out.exists():
        return out
    ssml = f"<speak version='1.0' xml:lang='en-US'>{text}</speak>"
    ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.Rate = {rate}; "
          f"$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
          f"$s.SetOutputToWaveFile('{out}', $f); $s.SpeakSsml(\"{ssml}\"); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    return out


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def write_wav(p: Path, x: np.ndarray):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def fixtures() -> dict[str, Path]:
    LAB.mkdir(exist_ok=True)
    clips = {k: tts(k, v) for k, v in GOLFER.items()}
    if not (LAB / "babble.wav").exists():            # two other voices talking at once, offset
        a = read_wav(tts("babble_a", BABBLE, "Microsoft Ravi"))
        b = read_wav(tts("babble_b", BABBLE[::-1].split(".")[0][::-1] + ". " + BABBLE, "Microsoft Zira Desktop", 1))
        n = max(len(a), len(b) + RATE)
        mix = np.zeros(n); mix[:len(a)] += a; mix[RATE:RATE + len(b)] += 0.8 * b
        write_wav(LAB / "babble.wav", mix * 0.6)
    if not (LAB / "keyboard.wav").exists():          # typing: short broadband clicks, 6-10 per second
        rng = np.random.default_rng(3)
        x = np.zeros(RATE * 30)
        t = 0
        while t < len(x) - RATE // 10:
            n = int(RATE * rng.uniform(0.004, 0.012))
            click = rng.normal(0, 1, n) * np.exp(-np.linspace(0, 6, n))
            x[t:t + n] += click * rng.uniform(0.3, 0.8)
            t += int(RATE * rng.uniform(0.08, 0.17))
        write_wav(LAB / "keyboard.wav", x * 0.5)
    clips["babble"], clips["keyboard"] = LAB / "babble.wav", LAB / "keyboard.wav"
    return clips


# ------------------------------------------------------------------ the synthetic microphone (runs in the page)
INIT = """
window.__lab = { ctx: null, dest: null, buffers: {}, bed: null, echo: null, plays: [] };
const realGUM = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (c) => {
  if (!c || !c.audio) return realGUM(c);
  const L = window.__lab;
  if (!L.ctx) { L.ctx = new AudioContext({ sampleRate: 48000 }); L.dest = L.ctx.createMediaStreamDestination(); }
  await L.ctx.resume();
  return new MediaStream([L.dest.stream.getAudioTracks()[0].clone()]);
};
window.__lab.load = async (name, url) => {
  const L = window.__lab;
  if (!L.ctx) { L.ctx = new AudioContext({ sampleRate: 48000 }); L.dest = L.ctx.createMediaStreamDestination(); }
  const buf = await (await fetch(url)).arrayBuffer();
  L.buffers[name] = await L.ctx.decodeAudioData(buf);
  return L.buffers[name].duration;
};
window.__lab.play = (name, gain = 1, loop = false) => {
  const L = window.__lab, src = L.ctx.createBufferSource(), g = L.ctx.createGain();
  src.buffer = L.buffers[name]; src.loop = loop; g.gain.value = gain;
  src.connect(g).connect(L.dest); src.start();
  L.plays.push({ name, at: performance.now() });
  return { duration: L.buffers[name].duration, src };
};
window.__lab.startBed = (name, gain) => { window.__lab.bed = window.__lab.play(name, gain, true).src; };
// speaker echo: the agent's own voice (any <audio> element the SDK attaches) fed back into the mic
window.__lab.startEcho = (gain) => {
  const L = window.__lab;
  const hook = () => document.querySelectorAll("audio").forEach((a) => {
    if (a.__labbed || !a.srcObject) return;
    a.__labbed = true;
    const s = L.ctx.createMediaStreamSource(a.srcObject), g = L.ctx.createGain();
    g.gain.value = gain; s.connect(g).connect(L.dest); L.echo = true;
  });
  hook(); L.echoTimer = setInterval(hook, 500);
};
"""


NUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
NUM.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "twenty": 20})


def norm(text: str) -> list[str]:
    """Lower-case words with spoken numbers as digits, so '5'10"' == 'five foot ten', '35' == 'thirty five'."""
    t = text.lower().replace("-", " ")
    t = re.sub(r"(\d+)\s*'\s*(\d+)\s*\"?", r"\1 foot \2", t)
    t = re.sub(r"(\d+)\s*\"", r"\1 inches", t)
    words, out = re.sub(r"[^a-z0-9 ]", " ", t).split(), []
    for w in words:
        if w in NUM and out and out[-1].isdigit() and int(out[-1]) % 10 == 0 and int(out[-1]) >= 20 and NUM[w] < 10:
            out[-1] = str(int(out[-1]) + NUM[w])
        else:
            out.append(str(NUM[w]) if w in NUM else w)
    return out


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), int)
    d[:, 0], d[0, :] = range(len(r) + 1), range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r), len(h)] / max(1, len(r))


# ------------------------------------------------------------------ one scenario
def run(page, url: str, name: str, sc: dict, clips: dict) -> dict:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_function("window.Fitter && Fitter.token()", timeout=20000)
    page.evaluate("v => Fitter.api('/api/session/profile', {method: 'PUT', json: {values: v}}).then(Fitter.applyState)", BASE)
    for k, p in clips.items():
        page.evaluate("([n, u]) => __lab.load(n, u)", [k, f"/lab/{p.name}"])
    pre = page.evaluate("Fitter.state()")["profile"]
    if any(pre.get(k) is not None for k in sc["expect"]):
        raise RuntimeError(f"session not fresh: {[k for k in sc['expect'] if pre.get(k) is not None]} already set")
    page.evaluate("Fitter.showScreen('chat')")
    page.click("#start-voice")
    page.wait_for_function("document.querySelector('#voice-status').textContent === 'Listening'", timeout=30000)
    if sc.get("bed"):
        page.evaluate("([n, g]) => __lab.startBed(n, g)", [sc["bed"], sc.get("gain", 0.4)])
    if sc.get("echo"):
        page.evaluate("g => __lab.startEcho(g)", sc["echo"])

    agent_n = lambda: page.locator("#transcript li.agent").count()
    status = lambda: page.inner_text("#voice-status")

    def wait_turn(prev: int, timeout=45):
        """Until the agent has said something new and has been listening for 1.2 s."""
        end, quiet_since = time.time() + timeout, None
        while time.time() < end:
            if agent_n() > prev and status() == "Listening":
                quiet_since = quiet_since or time.time()
                if time.time() - quiet_since > 1.2:
                    return True
            else:
                quiet_since = None
            page.wait_for_timeout(250)
        return False

    log = {"scenario": name, "events": []}
    answer = {"height": sc["lines"][0], "wrist": sc["lines"][1]}
    said = []

    def speak(clip: str, over_agent=False):
        prev = agent_n()
        if over_agent:                                  # barge in 0.8 s after the agent starts talking
            end = time.time() + 20
            while status() != "Speaking" and time.time() < end:
                page.wait_for_timeout(100)
            page.wait_for_timeout(800)
        d = page.evaluate("n => __lab.play(n).duration", clip)
        said.append(clip)
        page.wait_for_timeout(int(d * 1000) + 300)
        wait_turn(prev)

    wait_turn(0, 40)                                   # greeting + first question
    for turn in range(10):
        st = page.evaluate("Fitter.state()")
        if all(st["profile"].get(k) is not None for k in sc["expect"]) and not st["staged_for_review"]:
            break
        last = page.locator("#transcript li.agent").last.inner_text() if agent_n() else ""
        if st["staged_for_review"] and re.search(r"right\??|correct\??", last, re.I):
            speak("yes")
        elif re.search(r"wrist", last, re.I) and st["profile"].get("wrist_to_floor_cm") is None:
            if re.search(r"explain|know what", last, re.I) and not re.search(r"inches|measurement\?$", last, re.I):
                log["events"].append(f"agent offered an explanation: {last}")
            speak(answer["wrist"], sc.get("overlap") and turn > 0)
        elif re.search(r"height|tall", last, re.I) and st["profile"].get("height_cm") is None:
            speak(answer["height"], sc.get("overlap") and turn > 0)
        else:                                           # a clarification or something unexpected: answer what's missing
            log["events"].append(f"agent said: {last}")
            pending = "height" if st["profile"].get("height_cm") is None else "wrist"
            speak(answer[pending] if answer[pending] != "height_pause" else "height")
    st = page.evaluate("Fitter.state()")
    users = page.locator("#transcript li.user").all_inner_texts()
    agents = page.locator("#transcript li.agent").all_inner_texts()
    # transcription: for each golfer line actually spoken, the best-matching transcript line
    t_err = {c: min((wer(SAID[c], u) for u in users), default=1.0) for c in dict.fromkeys(said)}
    got = {k: st["profile"].get(k) for k in sc["expect"]}
    answers_ok = all(got[k] is not None and abs(got[k] - v) < 0.6 for k, v in sc["expect"].items())
    invented = {k: v for k, v in st["profile"].items() if k not in BASE and k not in sc["expect"] and v not in (None, "")
                and k not in ("score_value",)}
    page.evaluate("document.querySelector('#end').click()")
    page.wait_for_timeout(800)
    return {**log, "transcription_wer": {k: round(float(v), 2) for k, v in t_err.items()}, "heard": users, "agent": agents,
            "saved": got, "answers_correct": answers_ok, "invented_values": invented,
            "staged_left": list(st["staged_for_review"]), "interruptions": page.evaluate("window.Voice?.stats?.().interruptions ?? null")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--only", help="comma-separated scenario names")
    ap.add_argument("--recordings", help="folder of WAV recordings from your real environment, used as background beds")
    a = ap.parse_args()
    clips = fixtures()
    scenarios = dict(SCENARIOS)
    if a.recordings:
        for f in sorted(Path(a.recordings).glob("*.wav")):
            clips[f.stem] = f
            scenarios[f"rec:{f.stem}"] = {"bed": f.stem, "gain": 1.0, "lines": ["height", "wtf35"],
                                          "expect": {"height_cm": 177.8, "wrist_to_floor_cm": 88.9}}
    if a.only:
        scenarios = {k: v for k, v in scenarios.items() if k in a.only.split(",")}
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        for name, sc in scenarios.items():
            print(f"--- {name}", flush=True)
            # a fresh browser context = a fresh fitting session, so no answer carries over between scenarios
            ctx = b.new_context(permissions=["microphone"])
            ctx.add_init_script(INIT)
            ctx.route("**/lab/*.wav", lambda r: r.fulfill(path=str(clips[Path(r.request.url).stem])))
            page = ctx.new_page()
            try:
                r = run(page, a.url, name, sc, clips)
            except Exception as e:                       # one failing scenario shouldn't hide the others
                r = {"scenario": name, "error": str(e)[:300]}
            ctx.close()
            results.append(r)
            print(json.dumps(r, indent=1)[:2500], flush=True)
        b.close()
    out = LAB / "results.json"
    out.write_text(json.dumps(results, indent=1))
    print("\nSUMMARY  (transcription WER per golfer line | answers correct | invented values | interruptions)")
    for r in results:
        if "error" in r:
            print(f"{r['scenario']:12} ERROR {r['error'][:120]}")
        else:
            print(f"{r['scenario']:12} WER {r['transcription_wer']} | answers {'OK' if r['answers_correct'] else 'WRONG ' + str(r['saved'])}"
                  f" | invented {r['invented_values'] or 'none'} | interruptions {r['interruptions']}")
    print("details:", out)


if __name__ == "__main__":
    main()
