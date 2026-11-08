# The Fitter in Your Pocket

**Team DynamicDuo · Takomo Golf challenge · Since AI Hackathon 2026 (Turku, 6–8 Nov 2026)**

The golfer's main question is **"Which clubs suit my actual game?"** The app answers it the way a fitter would.

- **Evidence for each decision.** It collects evidence separately for the head, flex, shaft model, length and lie, and grip.
- **Measurement instead of guessing.** Wrist-to-floor is measured from one phone photo rather than guessed.
- **An honest explanation.** It shows why the kit's engine chose each part, how strong the evidence is, and when a check is worth doing before ordering.
- **Three ways to use it.** By form, by voice, or by typing to Takomo's AI fitting assistant (ElevenLabs).

All rules, product data and demo golfers come from the challenge ZIP (`TakomoGolf_SinceAI2026_FINAL.zip` → Takomo Fitting Lab 2026 v1.1.0), copied unchanged into `kit/`.

> **Takomo's files are not in this public copy.** Takomo Golf's product data and participant pack were shared for
> the Since AI Hackathon 2026 only and aren't covered by an open licence (see `kit/NOTICE`). To run the app, put
> `takomo_products.json` from the challenge ZIP in `kit/takomo_fit/data/` (and, optionally, the pack's
> `heads.csv`, `shafts.csv`, `grips.csv` and `takomo_products.json` in `data/`). Everything else here is our code,
> the kit's MIT code and its CC BY 4.0 synthetic data, with their licences in `kit/LICENSE` and `kit/NOTICE`.
> Live demo: https://takomo-fitter.vercel.app

## Frontend and backend

- **`/` is the frontend developer's page** (`prototypes/fitter.html`, branch `feature/ai-fitter-prototype`) connected
  to this backend. Their file is never edited. `tools/port_frontend.py` copies it to `app/static/fitter/fitter.html`
  and applies 12 small marked `[backend hook]` lines. To merge a new version of their page: `git pull`, then run
  `.venv/Scripts/python tools/port_frontend.py`. If their code moved, it names the hook it couldn't place.
- **The connection scripts** in `app/static/fitter/`:
  - `backend.js`: the real verdict from the engine.
  - `agent.js`: the live ElevenLabs assistant on their call dock (status, mute, keyboard).
  - `camera.js`: the real camera, body measurement and swing tempo in their camera cards.
  - `voice.js`: their spoken lines in the same ElevenLabs voice.
- **`/classic`** is the earlier UI, kept as a fallback and for its browser tests.

## Trust first

Golfers buy custom builds they can't return, so every screen is built to be believed:

- **How sure, tested.** The fit opens with "How sure are we": how many parts are High, Medium and Low. On 3,000 held-out golfers from Takomo's data, High matched Takomo's reference fit 9 times in 10, Medium 7 and Low 4.
- **Measured vs answered.** Each part names its evidence. "Measured" and "from your answers" are listed separately; nothing typed is ever called measured, and the progress bar ticks the body step only after a real measurement.
- **The weakest part, named.** The least certain part comes with one concrete next step.
- **No overclaiming.** There's no "perfect fit" or "guaranteed". Experimental features (the club-speed range from hand speed) are labelled and never used for the fit. Sample and simulated data are labelled. The returns note follows Takomo's published refund policy, and privacy notes are read from the assistant's real settings.
- **The assistant follows the same rules.** It reads answers back before saving them, separates "the speed you told me" from measurements, says how sure each part is, and names the least certain one. When asked "can I trust this?", it gives the tested numbers and says the data is simulated.
- **Honest about status.** This is a prototype calibrated on Takomo's simulated golfers and not yet checked on real ones. See "Validation status" below.

## The flow

Black-and-orange, mobile-first. **Conversation → Body measurement → Swing analysis → Your fit**, with a progress bar on every
step. Going back keeps confirmed answers; they live in the server session. The assistant stays reachable on every step through a
small dock showing Listening, Speaking or Mic muted, plus Mute.

| Step | What happens |
|---|---|
| **Before we start** | Microphone and camera cards, **Start conversation** or **Type instead**. The privacy notes are read from the agent's real ElevenLabs settings: audio recording off, transcripts kept 30 days. The microphone is requested when the conversation starts; the camera only when measuring starts. |
| **1 · Tell us about your game** | Live transcript, status pill and summary cards: usual score, current clubs, 7-iron speed or carry, common miss, priorities. Units are shown, missing items say "Still to confirm" and anything heard but unconfirmed is marked. Tap a card to edit it. When enough is confirmed: **Have we got this right? → Confirm & measure / Edit my answers**. The assistant's spoken summary is built from the same state as the cards. The **Quick quiz** is the same profile as a form. |
| **2 · Body measurement** | The golfer picks the scale: **I know my height** (the camera estimates wrist-to-floor), **Measure my height too** (a blank A4 sheet on the wall; the camera measures both) or the printed sheet. Then **Open camera**, **Upload photo** or **Use a tape measure**. One instruction at a time, from real tracking state: "Step back so your whole body is visible", "Let your arms hang naturally", "Keep your wrist visible", "Hold still", then a green **Ready to capture** and a countdown. |
| **Check what we measured** | Measured automatically, on the same screen. The golfer taps the top of the head; body tracking places the wrist and floor markers. The result appears at once and updates whenever a marker is dragged. It shows the source ("Camera estimate using your height"), the values and any issues. If the method's assumed error could flip stock/custom: "A small measurement change could affect your build. Please check with a tape." **Confirm & continue**, or say "yes" to the assistant (`confirm_measurement`). Also Adjust markers, Retake, Use a tape. |
| **3 · Swing check** | Camera-position guide, **Swing 1 of 3** progress, record or upload. Each swing plays back with draggable **takeaway, top and impact** markers; moving one re-times the swing by the kit's rule. Tempo is shown only when the events are found; otherwise the golfer is asked to retake. Accepted swings are combined (median), with consistency and the effect on the shaft. **7-iron clubhead speed is a separate card**: simulator or launch-monitor entry (a screenshot can be shown on the phone to read from; it isn't uploaded) or the kit's carry fallback. Speed is never derived from tempo. An **experimental** club-speed range from hand speed is shown for comparison only (below). |
| **4 · Your fit. Explained.** | The iron model, **STOCK SET** or **CUSTOM BUILD**, then rows for head, shaft model and flex, length, lie, grip and hand. Each row has a short reason, the evidence source and any open check, plus a confidence level. Before/after from real engine runs when a measurement changed things, the runner-up head and its trade-off, **Ask why** (voice) and **Save my fit** (a text file). The returns note follows [Takomo's refund policy](https://takomogolf.com/policies/refund-policy): 30 days from delivery for unused products, the 7-iron of a standard set may be tried, custom builds can't be returned. |
| **Optional strike test** | A clubface photo with impact tape or foot-powder marks after five shots. The app finds the marks, the golfer confirms or corrects them, and the app reports clustered or spread contact and any toe or heel bias. **It explains the fit and can suggest a fitter review; it never changes the kit's head choice.** |

### Three ways to set the scale (step 2)

| Method | Golfer needs | Scale from | Photo leaves the device? | Assumed error* |
|---|---|---|---|---|
| **I know my height** (default) | Their height (in golf shoes or barefoot) | Entered height × (floor→wrist ÷ floor→head along the body) | **No**: only three marker positions are sent | ±3 cm |
| **Measure my height too** (plain A4 paper) | Any blank A4 sheet on the wall | The four corners they mark → 210 × 297 mm homography | Yes, measured in memory, not stored | ±2.5 cm |
| **Printed sheet** | The kit's ArUco sheet | 4 markers (DICT_4X4_50, 40 mm, ids 0–3) → kit `SheetPlane` homography | Yes, measured in memory, not stored | ±2 cm |

\* These are **prototype assumptions** that decide when to ask for a tape check. They are not measured error limits.

**What every method shares:**

- MediaPipe Pose (on device, `@mediapipe/tasks-vision` 1.1.1 and `pose_landmarker_lite`, served by the app with no key and no CDN) suggests the wrist and checks framing.
- **Why something of known size is needed.** One camera picture has no absolute scale: a tall person far away looks the same as a short person close up. So the camera needs the golfer's height or an A4 sheet. With the sheet it measures height too. When the assistant opens the step and no height is known, it picks the A4 method.
- The **head top is always tapped by hand**: no landmark marks the top of the head.
- The **wrist and floor markers are placed by body tracking** and labelled "· check" until moved. MediaPipe's wrist landmark is the joint, about 7–8 cm from the crease on the sample, so the golfer is asked to check it. The floor goes under the lower heel.
- The live camera shows a skeleton overlay and checks for sheet (printed method only), head, wrist, feet and holding still. "Ready to capture" starts a 3-second countdown, which cancels if tracking is lost.
- The frozen photo has zoom, pan, undo, reset and retake.
- Results are labelled **estimates** and cross-checked against the entered answers. They are saved only after **Confirm & continue** or a spoken yes to the read-back.

## Voice (ElevenLabs Agents)

- **The chat screen** (*Find your fit → Chat with the fitter*): a mic button, a live transcript, "What I've heard so far" chips (tap one to correct it in the quiz), and a switch to typing. Voice is Sarah (`eleven_turbo_v2`) over **WebRTC** with a server-issued conversation token. Typing uses a signed WebSocket URL with text only. Audio recording is off, and transcripts are deleted after 30 days.
- **Answers are staged, then confirmed by saying "yes".** The agent reads each value back ("five foot ten, is that right?"). Only after a clear yes does it call `confirm_profile_update`. A "no" or a correction re-stages the value. The backend rejects ambiguous values and returns the question to ask (ball vs club speed, total vs carry, best vs usual score, missing units).
- **Tools:** `get_fitting_state` (including the evidence), `propose_profile_update`, `confirm_profile_update`, `open_measurement_step`, `get_measurement_status` (actual camera and body-tracking state) and `get_fit_recommendation`. Its result matches the card's `Fit #n`. Camera instructions are spoken once each, and the agent stays quiet during the countdown.
- **Keys:** the API key stays on the server (tokens and signed URLs). The fitting webhook uses a bearer secret plus a `secret__` session variable when deployed with `--public-url`.

### Noisy places (range, garage, outdoors)

| Layer | What is set | Where |
|---|---|---|
| Mic stream the SDK actually uses | Echo cancellation, noise suppression and auto gain on. Voice isolation on where the browser supports it (Chrome desktop: not supported, reported as off). The app captures the SDK's own track and shows what it got. | `voice.js` → `captureMic`, `micInfo()` |
| Microphone choice | A picker under the mic button that remembers the choice. It switches live with `changeInputDevice`. A level meter is shown, and a warning appears if the track opened is not the one picked. | `#mic-select`, `#mic-info` |
| ElevenLabs background speech | `vad.background_voice_detection: true` | `setup_elevenlabs_agent.py --audio normal` |
| Turn-taking | `turn_eagerness: normal`, `turn_v3`, `spelling_patience: auto`. `--audio patient` is for slow, number-heavy answers. | same |
| Interruptions | Backchannel words ("yeah", "okay", "mm") don't interrupt. The first message can't be interrupted. | same |
| Recognition | 28 ASR keywords (wrist-to-floor, carry, Takomo, flex names…) | same |
| Agent behaviour | It asks a short clarification instead of guessing, never stages a number it isn't sure of, ignores speech not addressed to it, and waits on half-said numbers ("five foot… ten"). It suggests a headset or typing if noise keeps intruding. | `agent/prompt.md` |
| Mute | Optional mute and end controls. The status shows Listening, Speaking or Mic muted. | chat bar |

`noisy_environment_detection` is **not available on the current ElevenLabs plan** (the API returns 403). The setup script skips it and says so. ElevenLabs does not document exactly how `background_voice_detection` filters speech.

**Measured in the noisy-environment test** (`tools/audio_lab.py`, real agent, synthetic golfer voice, Normal eagerness):

| Scenario | Transcription | Answers |
|---|---|---|
| Quiet | every word right | height and wrist-to-floor saved correctly |
| Keyboard clicks | right (5'10" written as digits) | correct |
| Pause inside a number ("five foot … ten") | right | correct, after one clarification |
| Self-correction ("34, no, 35 inches") | right | correct: 35 saved |
| Two nearby voices as loud as the golfer | the chatter was transcribed into the golfer's turn | **nothing wrong saved**, but the agent couldn't finish: it kept asking for a clear answer |
| Talking over the agent, speaker echo | rerun pending | rerun pending |

Two fixes came out of this. Spoken number words ("five foot ten", "thirty-five") were sometimes rejected by the parser and are now understood. And loud nearby talkers defeat background-voice filtering, which is why the app recommends a headset mic or typing in that case. The synthetic chatter was mixed straight into the microphone signal, which is harsher than a real room.

**Body scanning with the phone a few metres away.** Use a wired or Bluetooth headset mic and pick it in the microphone menu. The phone camera stays on the tripod. The line under the menu must name the headset; if it names another device, the app warns you.

## Run it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt        # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m uvicorn app.server:app --port 8765
```

Voice setup: put `ELEVENLABS_API_KEY=sk_...` in `.env` (git-ignored), then run:

```bash
.venv/Scripts/python tools/setup_elevenlabs_agent.py                  # creates the agent and tools
.venv/Scripts/python tools/setup_elevenlabs_agent.py --update-prompt  # after editing agent/prompt.md or tool definitions
```

The microphone and camera need HTTPS on a phone; localhost is fine on a computer.

## Tests (all passing, 7 Nov 2026)

```bash
.venv/Scripts/python -m pytest -q tests                 # 77 tests
.venv/Scripts/python -m playwright install chromium      # once; then, with the server running:
.venv/Scripts/python tools/e2e_check.py      # form flow, two runs
.venv/Scripts/python tools/e2e_measure.py    # body measurement: 3 methods, live camera, failure paths
.venv/Scripts/python tools/e2e_voice.py      # voice integration (scripted stand-in for the SDK)
.venv/Scripts/python tools/e2e_evidence.py   # game questions, head comparison, evidence map, strike test
.venv/Scripts/python tools/e2e_swing.py      # swing video -> tempo -> shaft and ball-height effect
.venv/Scripts/python tools/calibrate.py      # error margins and confidence levels from the kit data
.venv/Scripts/python tools/fit_ball_line.py  # the ball-line lie model (kept for a fitter tool; not in the golfer flow)
.venv/Scripts/python tools/live_agent_check.py   # short text conversation with the REAL agent (uses quota)
.venv/Scripts/python tools/audio_lab.py          # noisy-environment voice tests with the REAL agent (uses quota, ~15 min)
.venv/Scripts/python tools/audio_lab.py --recordings path/to/wavs   # add your own range/garage recordings as background
```

**What the tests cover:**

- **Geometry fixtures with known coordinates:** marker calibration within 0.6 cm up to strong perspective, and exact corner calibration. Also resizing, mirroring (mirrored markers never decode, so a selfie preview is never measured), EXIF rotation, blur, and crossed, overlapping or degenerate corners.
- **The height method:** the body-axis ratio, tilt warnings and the barefoot allowance.
- **"Check only when needed":** a tape check is requested only when the build could flip.
- **Strike detection:** marks are found and grooves rejected, on straight and rotated faces.
- **Swing tempo:** quick, moderate and smooth classification on uneven frame timing, low-fps and lost-tracking warnings, slow-motion time scaling, near-boundary flags, and the tempo-to-shaft and ball-height effect.
- **Evidence:** the evidence map, the handedness-aware flight wording, and the head comparison including the fitter-review trigger.

**Browser results on the sample photo:**

| Path | Result (truth 177.2 / 79.8 cm) |
|---|---|
| Height method | 80.0 cm wrist-to-floor |
| Printed sheet | 176.1 / 78.2 cm |
| Plain paper | 177.0 / 79.6 cm |
| Resized photo | 177.0 / 79.3 cm |
| Rotated photo | 176.7 / 78.7 cm |
| Live camera, sheet | 176.1 / 78.2 cm |
| Live camera, height | 80 cm |

The sample is a public-domain Library of Congress portrait (LCCN 2015649998) composited next to the sheet. Its truth points were placed by eye (±0.8 cm).

The privacy test confirms that the height method sends no photo or camera frame.

## Calibrated on the Takomo Fitting Lab data

`tools/calibrate.py` uses the kit's full datasets (`kit/data/train`, `dev`, `test`; 20,000 / 3,000 / 5,000 simulated golfers) instead of assumed margins. It writes `app/calibration.json`, which the app reads.

**What golfers get wrong** (questionnaire answers vs the true values, train split):

| Answer | Error | Used for |
|---|---|---|
| 7-iron carry | over-reported by **15 yd** on average, ± 9 yd | confidence ranges are centred on what they most likely mean |
| 7-iron club speed | over-reported by 2 mph, ± 4 mph | same |
| Height | over-reported by 1 cm, ± 1.3 cm | same |
| Wrist-to-floor | ± 3.3 cm | self-reported lie range |
| Tempo, glove size, score group | wrong 36 %, 37 %, 27 % of the time | neighbouring answers are tried |
| Strike spread (TrackMan, own 7-iron) | middle third of golfers 8.7–12.1 mm | "clustered" and "spread" thresholds |

**Confidence levels are learned, then checked on held-out golfers.**
- **Learned:** for each situation (which spec line, which answers could flip it, and where the speed and body numbers came from), the training split gives how often the fit from the golfer's answers matched the reference fit. 85 % or more right is **High**, 65 % or more is **Medium**, otherwise **Low**. The app also says "right about 7 times in 10 for golfers who answered like you".
- **Checked on 3,000 dev golfers:**

| | before (hand-written rules) | after (learned) |
|---|---|---|
| High | 79 % right | **90 % right** |
| Medium | 67 % | 72 % |
| Low | 39 % | 43 % |

Every spec line is now ordered High above Medium above Low.

**What the data says to do next:**
- **Shaft flex is never High from carry or self-reported speed.** It reaches High only with a simulator or launch-monitor number. That's why the swing step asks for one.
- **Lie from self-measured wrist-to-floor is right 32 % of the time.** Even a perfect body measurement gives the reference lie only 37 % of the time (86 % within 1°), because the reference lie comes from how the golfer delivers the club. **The kit's ball-line test was modelled and then left out of the golfer's flow** (below).

**The ball-line model** (`tools/fit_ball_line.py`): `lie need ≈ a × static chart reading + b × mean tilt + c`, fitted on the 3,981 training golfers who did the test and checked on 586 held-out dev golfers. The tilt coefficient is about −0.4: the mark exaggerates, as the playbook warns.

| Lie matches the reference | Chart only | With the ball-line test |
|---|---|---|
| Self-measured body: exact / within 1° | 32 % / 80 % | **46 % / 94 %** |
| Measured body: exact / within 1° | 39 % / 86 % | **49 % / 95 %** |

The test needs a whiteboard marker, a hard mat, five shots and five photos, which is too much to ask of most golfers
at home. So it is **not in the golfer's flow**: no button, and the assistant never asks for it. When lie is the least
certain part of a fit, the app suggests a fitter's lie check at a demo bay or, for a stock set, trying the 7-iron within
30 days. The model and the server endpoints (`app/ball_line.py`, `/api/ball-line/tilt`, `/api/session/ball-line`,
`tools/fit_ball_line.py`, `tests/test_ball_line.py`) are kept for a fitter-run version at demo bays or the event.
- The data is simulated (`kit/docs/SIMULATOR.md`). These numbers describe the kit's golfers until real golfers are measured at the event.

## Validation status: read before relying on numbers

- **No real-person validation yet.** Sample size 0. The measurement pipeline has only been checked on synthetic and composite images. Next step: tape-measure height and wrist-to-floor on consenting volunteers (golf shoes, same day), run all three methods, and record the error and sample size per method. Then replace the assumed ±2 / 2.5 / 3 cm.
- **Thresholds are prototype values.** `GET /api/photo/thresholds` lists them: sharpness, ratio range, cross-check 4 cm, corner geometry and tilt. Strike thresholds (clustered below 6 mm, spread above 11 mm across the face) come from the kit's **simulated** shots, not real golfers. Millimetres assume 60 mm scoring lines, to confirm with Takomo.
- **Stable tracking is not accuracy.** The camera checks framing; it does not prove the measurement is right.
- **The kit's rules are unchanged.** Score-representativeness, lost shots, ball flight and strike tests explain the fit and can trigger a fitter review. Using them to change the head choice would need validation first.
- **Experimental club speed from hand speed: not validated, never used by the fit.**
  - The hands' peak speed in the downswing is measured from the video. The scale comes from the golfer's height and the shoulder→hip→knee→ankle segment length in the video, about 0.78 × height (Drillis & Contini body proportions); the segment sum doesn't change with address posture.
  - Club speed is shown as hand speed × 3.0–4.0. That ratio is an **assumption**; the real one depends on each golfer's release.
  - Face-on video misses motion towards the camera and pose tracking lags fast hands, so it probably reads low, most of all below 50 fps.
  - Shown as a wide range ("roughly 60–80 mph") with an Experimental label. The fit uses only simulator or launch-monitor speed, or the kit's carry fallback.
  - To validate it: record swings alongside TrackMan at the event, compare, and replace the 3.0–4.0 assumption with the measured ratio and its spread.
- **Swing tempo accuracy** has been tested only on synthetic tracks and a synthetic video (a portrait moving along a known curve), not on real swings.
  - At 30 fps the backswing reads up to about 0.1 s short: the kit's takeaway threshold, plus a 3-frame spike filter for single-frame pose jitter. On the synthetic video it read 0.79 s for a true 0.9 s, and 0.33 s for a 0.3 s downswing.
  - Totals within 0.06 s of a class line are flagged as close calls, and 60 fps or more is recommended.
  - Slow-motion files need the speed setting.
  - Validate against TrackMan tempo at the event.
- **Sessions** are kept in memory and saved to `data/sessions/` (git-ignored), so a server restart doesn't lose answers. Each is deleted 3 hours after its last change. The browser keeps the session id across reloads. If a session has expired, a new one starts with a notice instead of an error. For more than one server worker, use a shared store.
- **Voice conversations** may run 30 minutes (ElevenLabs' default is 10, too short for a full fitting). If a conversation drops without the golfer ending it, it reconnects by itself with the recent transcript as context, up to 3 times in 5 minutes.

## Layout

```
app/fitting.py    kit engine wrapper: explanations, boundary check (tape only when stock/custom could flip)
app/evidence.py   evidence map, game description, ball-flight words, head comparison, strike detection/analysis
app/session.py    shared session: staging, spoken-value parsing, confirmation, per-method error, invalidation
app/photo.py      kit sheet geometry, paper-corner fallback, entered-height method, blur check, thresholds
app/swing.py      swing tempo: resampling, spike filter, kit tempo_from_wrist_y, kit tempo rule
app/voice.py      ElevenLabs: signed URL, privacy settings, webhook auth
app/static/       index.html, styles.css, app.js (form, steps, evidence), measure.js (camera, markers),
                  pose.js (MediaPipe), strike.js (strike test), swing.js (swing tempo), voice.js (assistant), vendor/mediapipe, models/
agent/prompt.md   the assistant's instructions
kit/              Takomo Fitting Lab 2026 files used by the app, unchanged
tools/            agent setup, sample-photo generator, browser tests
```

## Licence and attribution

- `kit/takomo_fit`, `kit/camera`: MIT, © 2026 Since AI ry. Synthetic data: CC BY 4.0.
- `takomo_products.json` and the product names belong to **Takomo Golf**, shared for the Since AI Hackathon 2026 only (`kit/NOTICE`).
- MediaPipe Tasks Vision and the pose model: Apache 2.0, © Google (`app/static/vendor/mediapipe/`).
- Voice by ElevenLabs.
- Sample photo: Library of Congress, public domain.
- This is a hackathon prototype, not an official Takomo Golf product.

**Keep this repository private** (team, organisers and judges): it contains Takomo's product data.
