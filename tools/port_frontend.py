"""Build the served page (app/static/fitter/fitter.html) from the frontend developer's prototypes/fitter.html.

Their file is never edited. This copies it and applies the marked [backend hook] lines that connect it to the
backend, so a new version of their page can be merged by re-running this script:

  git pull                                  # their latest prototypes/fitter.html
  .venv/Scripts/python tools/port_frontend.py

If their code moved, the script stops and names the hook it couldn't place, instead of guessing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "prototypes" / "fitter.html"
OUT = ROOT / "app" / "static" / "fitter" / "fitter.html"

HOOKS = [
    ("1 verdict from the engine",
     "function verdict(){\n  const p=S.profile;",
     "function verdict(){\n  // [backend hook 1] the real engine's verdict when the backend is connected (app/static/fitter/backend.js)\n"
     "  const real = window.FitterBackend && window.FitterBackend.verdict(S); if(real) return real;\n  const p=S.profile;"),
    ("2 fetch the fit first",
     "function startFit(){\n  show('fit');",
     "function startFit(){\n  // [backend hook 2] fetch the real fit for this golfer's answers before the verdict is shown\n"
     "  if(window.FitterBackend && !startFit.synced){ hint('Working out your fit…'); window.FitterBackend.sync(S).then(()=>{ startFit.synced=true; startFit(); startFit.synced=false; }, err=>{ hint(''); toast(err.message||String(err)); }); return; }\n"
     "  show('fit');"),
    ("3 read out the real values",
     "    ['head',  `Here’s your fit. Head first: the 201 MKII. ${v.head.why}`],\n"
     "    ['shaft', `Shaft: ${v.shaft.v.replace('·','')}. ${v.shaft.why}${v.shaft.conf==='hmm'?' That one’s a hmm.':''}`],\n"
     "    ['length',`Length: standard. ${v.length.why}`],\n"
     "    ['lie',   `Lie: ${v.lie.v.toLowerCase()}. ${v.lie.why}`],\n"
     "    ['grip',  `Grip: midsize, from the large glove. Sure on that.`],\n"
     "    [null,    `One close call: 201 versus 201T. Ask me anything, or we read the order back.`]",
     "    // [backend hook 3] read out the verdict's own values (was fixed text for the scripted golfer)\n"
     "    ['head',  `Here’s your fit. Head first: the ${v.head.v.replace(/^Iron /,'')}. ${v.head.why}`],\n"
     "    ['shaft', `Shaft: ${v.shaft.v.replace('·','')}. ${v.shaft.why}${v.shaft.conf==='hmm'?' That one’s a hmm.':''}`],\n"
     "    ['length',`Length: ${v.length.v.toLowerCase()}. ${v.length.why}`],\n"
     "    ['lie',   `Lie: ${v.lie.v.toLowerCase()}. ${v.lie.why}`],\n"
     "    ['grip',  `Grip: ${v.grip.v.toLowerCase()}. ${v.grip.why}`],\n"
     "    [null,    v.cc && v.cc.t ? `One close call: ${v.cc.t.replace(' vs ',' versus ')}. Ask me anything, or we read the order back.` : `No close calls. Ask me anything, or we read the order back.`]"),
    ("7 captions for the live assistant",
     "  c.say = function(t, done){\n    c.stop(); c.last=t; c.lastDone=done;",
     "  // [backend hook 7] show a line the real assistant is saying (its own ElevenLabs voice plays it)\n"
     "  c.show = function(t, done){ c.stop(); c.last=t; c.lastDone=null; hear.hidden=true; typeLine(t, done); };\n"
     "  c.say = function(t, done){\n"
     "    // [backend hook 7] while the real assistant is on the call, it does the talking: scripted lines stay silent\n"
     "    if(window.FitterAgent && window.FitterAgent.live()){ if(done) setTimeout(done, 0); return; }\n"
     "    c.stop(); c.last=t; c.lastDone=done;"),
    ("10 the ElevenLabs voice for spoken lines",
     "  function speakLine(t, done){\n    speechSynthesis.cancel();\n    const ws = words(t); root.classList.remove('done'); if(lbl) lbl.textContent='Speaking';",
     "  function speakLine(t, done){\n    speechSynthesis.cancel();\n    const ws = words(t); root.classList.remove('done'); if(lbl) lbl.textContent='Speaking';\n"
     "    // [backend hook 10] the ElevenLabs voice (same as the call) instead of the browser's robotic speech\n"
     "    if(window.FitterVoice){\n      const token = {}; c.utter = token; c.speaking = true;\n"
     "      window.FitterVoice.speak(t, {\n"
     "        onWord: i => { ws.forEach((w,j)=>{ w.classList.toggle('on', j===i); if(j<=i) w.classList.remove('todo'); }); follow(ws[i]); },\n"
     "        onEnd: () => { if(c.utter!==token) return; c.speaking=false; c.utter=null; finish(ws); done&&done(); },\n"
     "        onError: () => { if(c.utter!==token) return; c.speaking=false; c.utter=null; typeLine(t, done); }\n"
     "      });\n      return;\n    }"),
    ("10b stop the ElevenLabs voice",
     "    if(window.speechSynthesis && c.speaking){ speechSynthesis.cancel(); }",
     "    if(window.speechSynthesis && c.speaking){ speechSynthesis.cancel(); }\n"
     "    if(window.FitterVoice && c.speaking){ window.FitterVoice.stop(); }   // [backend hook 10]"),
    ("8 the live assistant takes the call",
     "  chatStep();\n}\nfunction chatStep(){",
     "  // [backend hook 8] the real assistant takes the call (voice, or typing if the mic was declined)\n"
     "  if(window.FitterAgent){ setTurn(null); hint(''); window.FitterAgent.start(S.mode); return; }\n"
     "  chatStep();\n}\nfunction chatStep(){"),
    ("11 real camera on the measure card",
     "  ioVisible(true); hint('');\n  measStep();\n}",
     "  ioVisible(true); hint('');\n"
     "  // [backend hook 11] real camera and body tracking on this card; the assistant asks the questions\n"
     "  if(window.FitterCamera){ window.FitterCamera.measure(); return; }\n  measStep();\n}"),
    ("12 real camera and tempo on the swing card",
     "                  : \"Five swings with your 7-iron, facing me, phone at hip height. Normal effort. I just watch, no tips.\");\n}",
     "                  : \"Five swings with your 7-iron, facing me, phone at hip height. Normal effort. I just watch, no tips.\");\n"
     "  if(window.FitterCamera){ hint(''); window.FitterCamera.swing(cont); }   // [backend hook 12] real camera, real tempo\n}"),
    ("13 clips read for real",
     "function runGate(file){",
     "function runGate(file){\n  if(window.FitterCamera){ window.FitterCamera.clip(file); return; }   // [backend hook 13] the clip is read for real"),
    ("4 expose the page to the backend scripts",
     "renderTiles(); renderMeasure(); renderVerdict(); renderYourSwing();\nstartHello();",
     "// [backend hook 4] let the backend scripts read the state and redraw (app/static/fitter/*.js)\n"
     "window.FitterUI = { S, UNIT, renderVerdict, renderYourSwing, renderTiles, renderMeasure, capFit, toast,\n"
     "  said, actions, hint, setTurn, startMeasure, startSwing, startFit, startAsk, hideSheets, renderHello, stopListening, screen: () => S.screen };\n"
     "renderTiles(); renderMeasure(); renderVerdict(); renderYourSwing();\nstartHello();"),
    ("9 load the backend scripts",
     "</script>\n</body>",
     "</script>\n"
     "<!-- [backend hook 9] the live assistant (ElevenLabs), body tracking, the voice, the engine, the camera -->\n"
     "<script src=\"https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.30.0/dist/lib.iife.js\"></script>\n"
     "<script src=\"/pose.js\"></script>\n<script src=\"/fitter/voice.js\"></script>\n<script src=\"/fitter/backend.js\"></script>\n"
     "<script src=\"/fitter/agent.js\"></script>\n<script src=\"/fitter/camera.js\"></script>\n</body>"),
]


def main():
    s = SRC.read_text(encoding="utf-8")
    for name, old, new in HOOKS:
        n = s.count(old)
        if n != 1:
            sys.exit(f"hook {name}: found {n} places for it in {SRC.name}; their code changed here, update the hook")
        s = s.replace(old, new, 1)
    OUT.write_text(s, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {SRC.name} + {len(HOOKS)} backend hooks")


if __name__ == "__main__":
    main()
