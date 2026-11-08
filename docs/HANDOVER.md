# The Fitter in Your Pocket — developer handover

Takomo Golf × Since AI Hackathon 2026 · compiled Sat 7 Nov 2026, 13:40 Helsinki

This document is the full working context from the ideation session: research, concept, product data, engine rules, interview script, answer bank, swing-video method, voice-CX rules, brand rules, screen specs, the Step 1 prototype code, and open questions. It is written so Claude Code (or a developer) can build from it without the original chat.

**How to use with Claude Code:** put this file in the repo root (or `docs/`) and reference it from `CLAUDE.md` with a line like `Read docs/HANDOVER.md before starting any task.` The prototype `step1.html` is a standalone page and can be dropped into `public/` or `prototypes/`.

---

## 1. The challenge, in one page

**Brief (Takomo):** buying irons online should not mean choosing blindly. Make expert fitting guidance feel personal, understandable and accessible wherever a golfer is. An intelligent digital fitter should interpret needs, explain trade-offs, and give confidence without pretending every answer is certain.

**What exists today:** a questionnaire on takomogolf.com (age, gender, height, wrist-to-floor, glove size, skill/score, 7-iron swing speed or carry, ball flight and miss, iron-characteristic preferences) that outputs head family, shaft, length, lie, grip. 600k+ fittings in six months; over 25% of iron orders go through it. Irons only.

**Scope decisions made in the session:**
- Irons only. English only.
- Everything is 7-iron based: speed, carry, tempo, calibration.
- Units: yards / mph / ft-in by default; metres / km-h / cm behind a toggle. One internal unit.
- Score, not handicap: ask "what do you usually shoot on 18".

**Judging weights (from the brief):** trust & usability 35% · idea 25% · accuracy 20% (28,016-golfer set with known answers + scoring script) · feasibility 20% (must run on takomogolf.com at scale).

**Translation:** an opaque model that is 3% more accurate loses to a transparent fitter that is equally accurate and feels like a conversation.

---

## 2. Concept

**Hybrid: "a fitter who listens, measures what it can, explains everything, and tells you how sure it is."**

Two ways in, one engine, one verdict:
1. **Talk to the fitter** (voice or text, ~10 min): chat → measure together → swing on camera → verdict → Q&A/handoff.
2. **Quick quiz** (~3 min): the same nine inputs as a form, each question says why it is asked, with a live "where your fit stands" rail. Result uses the same verdict card with the shaft marked as a guess and a two-minute path into the camera step.

Both feed the same deterministic engine. The fitter is a better way to collect inputs and explain outputs, not a different recommendation. That keeps accuracy provable on the 28k set.

### Build rules (non-negotiable)
- The LLM never picks a spec. Engine decides → confidence module says how sure → LLM narrates and asks.
- Every "why" and every answer is built only from fields in `takomo_products.json` or the verdict object. Anything else gets the decline line.
- The fitter observes swings; it never coaches them (also a Takomo brand rule: "clubs don't fix swings, and we don't say they do").
- Accuracy ≥ today's tool on the 28k set — same rules, better inputs.
- Disclose AI in the first line. Human-fitter exit one tap away, with context passed on.
- Order is a payment-class action: read the full spec back, explicit yes, say "ordered" only after Shopify confirms.

### Architecture
```
Client (Shopify page / PWA: voice in+out, camera, forms)
   ⇅
Conversation layer (LLM + voice persona) — asks, reads back, narrates
   ↓ writes stated inputs                 ↑ reads verdict object
Golfer profile — every input tagged said / measured / seen / sim / estimated, with an error range
   ↑ evidence: swing video (tempo ratio, posture, swing length, speed ±), measurements (read back), sim numbers (photo/CSV)
   ↓
Fit engine (today's rules + product DB, deterministic)
   ↓
Confidence module — rerun engine across each input's error range; a flip = close call
   ↓
Verdict object — spec · why per line · confidence per line · runner-up · close_call
```

### Verdict object (API contract between engine and UI)
```json
{
  "head":   {"value":"201-mkii","why":"...","confidence":"high","runner_up":"201t-mkii"},
  "shaft":  {"value":"kbs-tour-stiff","why":"...","confidence":"medium"},
  "length": {"value":0,"why":"...","confidence":"high"},
  "lie":    {"value":1,"why":"...","confidence":"high"},
  "grip":   {"value":"standard","why":"...","confidence":"high"},
  "close_call": {"spec":"head","options":["201-mkii","201t-mkii"],"reason":"inside swing-speed margin"},
  "inputs": {"score":{"v":88,"src":"stated","range":4}, "speed_mph":{"v":78,"src":"video+carry","range":8}, "...":"..."}
}
```

---

## 3. Product data (from `takomo_products.json`, as of 2026-10-13)

**Heads (5).** Ratings are Takomo's relative 0–5 scales, not measurements.

| Head | Target score | 7i loft | 7i offset | Dist | Forg | Work | DistCtl | Feel | Offset |
|---|---|---|---|---|---|---|---|---|---|
| 101 MKII | over 92 | 29.0° | 3.4 mm | 5 | 5 | 2 | 2 | 0 | 5 |
| 201 MKII | 85–92 | 31.0° | 2.77 mm | 4 | 4 | 3 | 3 | 0 | 3 |
| 201T MKII | 78–84 | 33.0° | 2.46 mm | 3 | 3 | 4 | 4 | 1 | 2 |
| 301 CB | 71–77 | 34.0° | 1.8 mm | 1 | 2 | 5 | 5 | 2 | 0 |
| 301 MB | under 71 | 34.0° | 1.8 mm | 1 | 1 | 5 | 5 | 2 | 0 |

Stock shafts for every head: KBS Tour, KBS Tour Lite. Default: kbs-tour-regular (MKII) / kbs-tour-regular-301 (301).

**Shafts (8 families, ~35 flex records).** Each record: flex (L/A/R/S/X), weight_g, tempo_profile (slow/mid/fast or any), flight_profile (low/mid/high), heads.

For MKII heads (101/201/201T): KBS Tour (steel 120–133 g, mid|fast, low|mid) · KBS Tour Lite (steel 105–110 g, slow, mid|high) · Mitsubishi MMT (graphite 57–86 g, mid|fast, low|mid) · True Temper Denali Silver (graphite 53–89 g, slow, mid|high).

For 301 heads (CB/MB): KBS Tour 301 build (slow|mid, low|mid) · KBS Tour Lite 301 build (slow|mid, high) · KBS C-Taper (steel 125–140 g, fast, low) · KBS C-Taper Lite (109–125 g, fast, mid|high) · Takomo Obsidian (graphite 72–111 g, any, low|mid) · Fujikura Atmos Red (graphite 72 g, any, high).

**Grips:** standard / midsize / oversize (Lamkin). **Length:** −1.5″ … +1.5″ in ½″ steps. **Lie:** −3° (flat) … +3° (upright), 0 standard.

**Today's rules (`current_tool_rules`, "a starting point, not a spec"):**
- Head ← score bucket: <71 MB · 71–77 CB · 78–84 201T · 85–92 201 · >92 101.
- Flex ← 7i swing speed (or carry): <60 mph / <120 yd → L (women) or A (men) · 60–70 / 120–135 → A · 71–80 / 135–155 → R · 81–90 / 155–175 → S · >90 / >175 → X.
- Graphite if: flex is L or A, or golfer is female, or age ≥ 70.
- Tempo answers: smooth → slow, moderate → mid, quick → fast. Flight: low / mid / high.
- Length by height: <155.5 cm −1.5″ · <160.5 −1.0″ · <168.5 −0.5″ · <183.5 0 · <191.5 +0.5″ · <196.5 +1.0″ · else +1.5″. Lady flex adds +1″.
- Lie: five worked examples only (152/86 → +1; 173/86 → 0; 188/94 → +2; 196/100 → +3; 193/76 → −2). **No formula — ask Takomo.**
- Grip by glove: XS/S/M standard · M/L, L midsize · XL oversize.
- Stock set (no upcharge) = steel stock shaft, standard length, lie, grip.

Gaps to confirm: lie formula; whether forged heads can be bent after purchase; no price per shaft; no handedness field; the graphite rule is blunt; 301 Combo not modelled.

---

## 4. Engine — the six iron decisions

| Decision | Driven by | Where it gets shaky → close call | Error range to rerun with |
|---|---|---|---|
| Head | score bucket; miss and goal as tie-break | score within 2 of 77/78, 84/85, 92/93; aspirational score; "players look" with inconsistent strike | ±4 self-reported · ±2 from app/handicap |
| Flex | 7i speed, else carry ÷ ~1.9 | within 3 mph of 70/80/90; carry overstated; carry vs total; metres as yards | ±3 sim · ±6 stated · ±10 carry or driver×0.8 · video narrows to ±8 and cross-checks |
| Shaft model | steel/graphite rule, tempo, desired flight, head family | self-described tempo ("medium"); desired vs current flight mixed; slicer wants "height" | tempo adjacent bucket if stated, exact if video · flight adjacent |
| Length | height | within 1 cm of a threshold; arm length ignored | ±1 cm stated · ±2 "about" |
| Lie | height + wrist-to-floor; video posture as support | weakest rule; skipped wrist-to-floor → Low | wtf ±1″ measured · ±2″ estimated; default Medium until formula exists |
| Grip | glove size | M vs M/L; no glove | adjacent size if unsure |

Confidence = rerun the rules across each input's error range; if the answer flips, it is a close call and the fitter says so.

---

## 5. Interview script (voice mode; quiz asks the same nine as a form)

Every captured value carries a `_source` (sim · measured · video · stated · estimated) which sets its error range. Ask a follow-up only when the answer sits inside a range that would flip a decision. Agent turns ≤ 2 sentences then a question. Every number is read back as text.

| # | Fitter asks | Follow-up / aspiration probe | Extract → field | Drives |
|---|---|---|---|---|
| 0 | "Tell me about your game — how long you've played, how often, and what you'd like these irons to fix." | Reflect back: "So: … did I get that right?" | years, rounds/month, score if volunteered, miss, goal → `goal`, `miss` | whys, tie-breaks |
| 1 | "What do you usually shoot on 18 — a normal day, not your best?" | Vague → "Break 90 most days?" · "Now, or what you're aiming for?" | number / range / hcp (+72) → `score`, `score_source` | Head |
| 2 | "Know your 7-iron swing speed from a sim?" — else "How far does a normal 7-iron carry, in the air?" | "Good one or normal one?" · units · driver-only → ×0.8, tagged estimated | mph / carry yd → `speed_mph` or `carry_yd`, `speed_source` | Flex |
| 3 | Not asked in voice — measured from the 7-iron swing video. Quiz: "Smooth and rhythmic, or quick and aggressive?" | Quiz, if "medium": "Would a friend say you rush it?" | slow/mid/fast → `tempo`, `tempo_source` | Shaft model |
| 4 | "Irons fly high and land soft, or flatter? Want more height, less, or same?" | Slicer wanting height → "More launch won't fix the slice; offset and lie do more." | `flight_current`, `flight_desired` (rule uses desired) | Shaft model |
| 5 | "What's your bad shot — slice, hook, thin, fat, toe, heel?" | "Every round or now and then?" | `miss`, `miss_freq` | Head tie-break, whys |
| 6 | "What are you playing now, and what flex?" | "How old? Steel or graphite?" | `current_set` | sanity check, whys |
| 7 | Measure: "How tall?" · "Tape from wrist crease to floor, arms relaxed" · "Glove size?" | Read back every number. No tape → skip, lie → Low | `height_in`, `wtf_in`, `glove` + `_source` | Length · Lie · Grip |
| 8 | "Right- or left-handed?" · "Lighter graphite or steel?" | Age/gender only if the graphite rule needs them, via the shaft-weight question | `hand`, `graphite_pref`, `age`, `gender` | steel/graphite |

---

## 6. Answer bank (ship as grounded Q→A pairs; run as regression tests)

One sentence of why, then stop. Long explanations raise trust without raising accuracy.

| Topic | Golfer asks | Built from | Example line |
|---|---|---|---|
| Shafts | Why stiff not regular? | speed bucket + edge distance, tempo, confidence.flex | "78 is right on the line; your quick tempo tips it to stiff. Under 75 on a sim and I switch you back." |
| | Steel or graphite? | graphite_if, shaft.weight_g, material | "Graphite is ~40 g lighter — easier on the hands, a touch more speed. At your speed steel is the stock choice." |
| | KBS Tour vs Tour Lite? | weight_g, tempo_profile, flight_profile | "Tour Lite is 15–25 g lighter, suits a smoother tempo, launches higher. Tour is mid-to-quick tempo, flatter flight." |
| | Lighter shaft = more distance? | swing_length, tempo, weight — no yardage claims | "A little speed, usually. I can't promise yards — nobody honest can without a launch monitor." |
| Lie & length | What does +1° upright do? | lie_sign_convention, hand, miss | "Starts the ball a touch left for a right-hander and squares the sole for your hand position. Too much and you'd pull it." |
| | Why not longer, I'm tall? | length_by_height, wtf, lie_examples | "Length comes from height, lie from where your hands hang. Tall but long-armed → standard length, a touch upright." |
| | Can I change lie later? | Takomo policy (confirm) | "Forged heads bend a degree or two at any fitter. [confirm]" |
| Grip | Why midsize? | grip_by_glove_size | "Large glove → midsize. Too small and your hands get overactive; too big and it's hard to release. Grips are cheap to swap." |
| Models | 101 vs 201 / 201 vs 201T / CB vs MB | ratings deltas, loft, offset, target_score | "201T gives up one point of forgiveness for one of workability, 2° weaker loft, less offset. Strike it clean most days? Fair trade." |
| | Am I good enough for 201T/301? | score bucket, miss_freq, video consistency | "Not about good enough — whether you'd enjoy the trade. At 88 with a regular slice, the 201 gives you better rounds." |
| | Mix CB long irons / MB short? | 301 Combo exists on site; not in data | "Yes, that's the 301 Combo. I can't fit the split today — a human fitter can." |
| Trust | How sure are you? | confidence per spec, close_call | "Sure on length, lie and grip. Head is solid. Flex is the one I'd want a sim number for." |
| | What if it's wrong? Try first? | 30-day returns, 2-year warranty, 60+ demo locations | "30 days to send them back, and demo bays where you can hit both first." |
| | Do you just recommend what sells? | engine = rules; current_set comparison | "Same rules as the quiz — I just explain them. If your current set already matches, I'll say so." |
| | How is this different from the quiz? | — | "Same engine. I get better inputs — I watched you swing — and I tell you why and how sure I am." |
| Declines | Swing lessons · other brands · wedges/woods/putters · prices in other currencies · medical | refusal list; always offer the human exit | "That's outside what I can fit today. I can get you 15 minutes with a human fitter — they'll see everything we've done." |

---

## 7. Swing video — tempo, posture, clubhead speed

**Capture:** three swings **with a 7-iron**, **face-on**, phone at hip height, **slow-mo (120/240 fps)**. Live capture adds a framing coach (dashed body outline, orange when ok); upload runs the `quality` gate first. The fitter observes, never coaches. After each swing it says one specific thing it saw — that is the trust moment. Face-on, not down-the-line: hand speed is foreshortened DTL; tempo and hands-high/low work from either.

**Pipeline (approach informed by the open-source swing-analyzer PWA — reimplement, do not copy; it has no licence):**
1. MediaPipe Pose Landmarker in a Web Worker (GPU, CPU fallback), frames downscaled to ~480 px, sampled at ~60 real-time Hz, capped ~900 samples. Detect fps by playing at 0.2× and reading `requestVideoFrameCallback` timestamps; `timeScale` for baked-in slow-mo.
2. Hand = midpoint of wrists (landmarks 15/16). Fill gaps, smooth (~50 ms). Hand speed normalised by torso length.
3. Impact region = peak hand speed. Top = highest hand point before it. Impact = lowest hand point between top and ~120 ms after the peak. Address = last still run (speed < 8% of peak for ~100 ms) before the takeaway. Finish = first still run after impact with hands raised.
4. Confidence 0–1: penalise if hands rose < 0.8 torso-lengths, downswing not shorter than backswing, or < 70% frames with a pose.
5. Tempo = backswing ÷ downswing (real seconds). Three swings → median; discard swings with confidence < 0.6.

**Outputs (schema):**

| Output | Reliability | Used for | Fitter says |
|---|---|---|---|
| `tempo_ratio`, `tempo_bucket` slow/mid/fast | High | replaces stated tempo → shaft model | "Quick transition — about 2.4 to 1." |
| `address_hands` high/neutral/low | High | supports lie | "Hands hang low — that's why I'm going a degree upright." |
| `swing_length` short/full/past-parallel | Good | speed proxy; lighter-shaft reasoning | "Compact swing — a lighter shaft gives a bit more speed." |
| `speed_est_mph` + `speed_range` | Rough · needs slow-mo | never replaces stated speed; narrows band; cross-check if off by a bucket | "You said 85 — this looks closer to 75. Full-effort swings?" |
| `consistency` | Rough | forgiveness argument | "Those three were very alike." |
| `quality` ok / no-hands / too-far / too-dark | High | gate | "Can't see your hands — step back a bit?" |
| Not from a phone | — | ball speed, launch, spin, strike, face angle | "That's why 201 vs 201T stays a close call until a demo bay or sim numbers." |

**Tempo buckets (starting point, calibrate):** ratio ≥ 3.2 → slow/smooth · 2.6–3.2 → mid · < 2.6 → fast/quick; backswing > 1.0 s → slow regardless. Reject if no address stillness, top-to-impact < 5 frames, or wrist confidence < 0.6 mid-swing. Reference: tour ≈ 3:1 (backswing ~0.75–0.9 s, downswing ~0.25–0.3 s); downswing time alone barely separates golfers — the ratio does.

**Clubhead speed (rough, ±8 once calibrated), fused from three estimators:**
- A. carry → speed via the rule table (~1.9 yd/mph for a 7-iron).
- B. peak hand speed from pose × calibrated ratio. Pixel scale from the golfer's known height (shoulder→ankle at address). Requires slow-mo: hands peak ~18–22 mph (TPI tour average 22.0 peak mid-hands); at 240 fps that is ~4 cm/frame; at 30 fps it is 30 cm/frame with blur — unusable, fall back to A. Pros ≈ 5× hands, amateurs with a 7-iron nearer 4× — **fit `club_speed = a × peak_hand_speed + b` at the TrackMan session** on 30–50 paired 7-iron swings; the residual is the honest ±.
- C. downswing time + swing length as a sanity prior.
- Report as a range, never a point. If A and B disagree by a flex bucket, the cross-check question fires. Do not attempt clubhead tracking this weekend.

**What the golfer gets back (so the step feels useful):** one specific observation after each swing within ~2 s; their swing with a 3-dot overlay and tempo readout; the cross-check said out loud; a "your swing" card (tempo ratio, posture note, speed range) in the summary — a mirror, not advice.

---

## 8. Voice CX rules (from research)

1. **Heard right.** Confirm every number and name — read back, chip to correct. "Repeating myself" is the #1 frustration (55% in a 455-person 2026 survey); misheard 45%; cut off 47.5%. Test with accents and outdoor noise.
2. **Timing.** No fixed silence timeout; semantic end-of-turn. Barge-in: mute, cancel pending generation, keep the partial in history. Agent turns ≤ 2 sentences then a question. No fake "working on it".
3. **Calibrated.** Speak at the engine's confidence: brisk on High, slower and flagged on Medium/Low. One-sentence whys — longer explanations raise user confidence without raising accuracy (UC Irvine / Nature Machine Intelligence calibration-gap study). Express uncertainty per spec, not inside the reasoning chain.
4. **Honest.** Disclose AI in the first line (80% want it; ~20% fewer hang-ups across 450k calls). Human exit one tap away, with context passed on. Order = payment-class.
5. **On screen.** Every number and spec also as text. Hard-to-hear details never voice-only.

Demo metrics: corrections per session, verified completion, close-call rate — not WER.

---

## 9. Brand rules (Takomo Brand Book, Feb 2025)

- **Colours:** Midnight Black `#0B0B0B` (never pure #000), Washed White `#F8F8F8`, Tiger Orange `#FF6E01`. Ratio 60/30/10; push black and dark shades; orange is an accent only. Greys used in the prototype: `#161616` surface, `#2A2A2A` line, `#3A3A3A` strong line, `#C8C8C8`, `#8E8E8E`, `#5A5A5A`.
- **Type:** Barlow Condensed for headlines (bold, uppercase, tight); Barlow for body and anything small. Both on Google Fonts.
- **Logo:** T-mark from a swing pattern; letter-spaced wordmark TAKOMO. Use the real SVG from brand assets (the prototype uses the wordmark only).
- **Imagery:** rough, deep/moody, natural; product on black, premium. No saturated fairways / blue skies.
- **Voice:** "the best random ever" — funny, good at golf, doesn't give swing tips, knows a ton and uses it helpfully, candid, hypes good shots, pokes fun at bad ones. Archetype Sage + Jester. Pillars: everyman over authority · funny over plain, boring or cheesy · candid over formal · technical over reductive. "Takomo fans don't want to talk to ChatGPT. They want to talk to a friend." Claim: *Honestly better golf.*
- **Direct constraint:** "We know clubs don't fix a bad swing, and we don't tell our buyers that they will."
- Option to ask Takomo: use the illustrated Forge blacksmith character as the fitter's avatar (sidesteps "pretending to be human").

---

## 10. Screen specs (mobile-first website)

Shared spine on every fitter screen: (1) wordmark + units toggle / step label; (2) progress spine chat · measure · swing · your fit; (3) **caption strip** — orange left rule, "FITTER · SPEAKING", live text of what he is saying; (4) **stage** — what he is talking about; (5) mic / type / skip in the same place.

1. **Hello.** He is already talking on load (text types out; audio after first tap). Three doors: Talk to the fitter (orange, primary) · Same thing, typing · Quick quiz. Fine print: no email to see your fit · saved so you can come back · same engine either way. Returning golfer: greeting changes, primary becomes Continue.
2. **The chat.** Caption: "Slice and weak long irons — been there. What do you usually shoot? Normal day, not the one you tell your mates about." Stage: profile grid filling in live (Usually shoot · 7-iron carry · Bad shot · Wants), the field being asked in orange, each tile tagged said/heard, tap to fix; "You said" shows the last transcript.
3. **Measure together.** Caption: "Tape at the crease of your wrist, arms hanging like you've given up on the round. What does it say at the floor?" Stage: height / wrist→floor / glove tiles, figure illustration, big number + "I heard 'thirty-three'" + That's right / Say again. No tape → skip, lie goes to Low.
4. **Swing — live or a clip.** Caption reacts after each swing: "Quick transition — about 2.4 to 1 — and your hands hang low. Noted. One more and I'll stop staring." Stage: camera with dashed frame, REC · swing 2 of 3, tempo readout 2.4 : 1, bottom line "7-iron, facing me. Framing's good — swing when you're ready." Card: what I can see / what I can't. Buttons: Use a clip I have · Skip (he guesses).
5. **Your fit.** Caption reads the line he is on; that row lights up. IRON 201 MKII, runner-up 201T. Rows Head / Shaft / Length / Lie / Grip, each with one why and a pill: Sure (grey) · Hmm (orange outline) · Guess (orange fill). One close call box. Buttons: Ask me anything · Review & order.
6. **Ask, then go.** Q&A chips (Why stiff? · +1° lie does what? · 201 vs 201T · Steel or graphite? · "Fix my slice? → nope, clubs don't"). Fit summary saved, 201 vs 201T open. Exits: Hit both at a demo bay · 15 min with a human fitter (they get the summary first).

Quiz: same caption strip relabelled "why I'm asking"; "Where your fit stands" rail (Head · set, Shaft · open, Lie · set); result = the same card with Shaft as Guess and "Turn the guess into a call · 2 min" into the camera step.

---

## 11. Step 1 UX spec (locked for prototyping)

- Lives on the existing "Find my fit" page. No account, no email, no mic wall before the first line.
- 0 s: page loads, caption strip visible and pulsing. 0.5 s: fitter starts. Text first, voice second — caption types out; audio only after a tap (browsers require a gesture). "Tap to hear him" on the strip.
- Line: "Hey. I'm Takomo's AI fitter — not a person, but I know these irons better than most people do. Ten minutes, I explain every spec, and I tell you when I'm guessing. Talk or type?"
- Doors tappable while he talks; tapping stops him mid-sentence (barge-in).
- Talk → mic permission sheet with context ("He needs to hear you. Nothing is recorded or kept."). Denied → typing, no second ask. Typing = identical fitter, keyboard. Quiz = tertiary.
- Units toggle top right, remembered. Returning golfer: "You're back. Last time we left 201 vs 201T open — got sim numbers, or pick up where we were?" with Continue.
- Must not: pretend to be human; cookie-banner-style wall; autoplay video with sound; onboarding carousel.
- Dev: caption strip is a reusable component; mic requested on tap, never on load; this fixed line can be pre-rendered TTS (zero latency).
- Open calls for the team: speak on load vs after tap; two doors vs three; the fitter's name (not a human one).

---

## 12. Prototype — `step1.html`

A standalone mobile-first page: types the caption, speaks with the browser voice (word highlight in sync), barge-in, permission sheet, units toggle, returning-golfer state, demo bar (Replay · Returning golfer · Mute voice). The microphone is simulated (the hosted prototype cannot be granted mic access). Replace `speakLine()` with an ElevenLabs stream and keep the word-highlight logic (ElevenLabs returns word timestamps). Full source in Appendix A.

**ElevenLabs key setup agreed:** scopes Text-to-Speech (+ Speech-to-Text if used, + Voices read), credit cap, no IP allowlist for the hackathon; key server-side only (or single-use tokens for client streaming); low-latency model for conversational turns.

---

## 13. Research summary (market)

Landscape splits into: (A) OEM static questionnaires — PING WebFit (trained on real fittings, "practice round" for in-person), Callaway and Titleist selectors (Titleist adds free virtual consults; its ball tool is praised for explaining *why* and showing the runner-up), TaylorMade Club Recommender; (B) AI co-pilots inside the studio — Club Champion AI Fitter Co-Pilot (~80% accurate by its founder's estimate, i.e. one in five would buy the wrong clubs without live testing), Wilson FitAi (needs a launch monitor), Mizuno Swing DNA (shaft-load sensor), GOLFTEC TECfit; (C) sensor/phone capture — Groove Golf (Apple Watch, "Groove Number"), Sportsbox AI (3D from one phone video; fitting partnerships with LA Golf and Cool Clubs), Arccos (on-course data into fittings); (D) new LLM-style fitters — FitMyGolfClubs (grades your current bag on 8 factors, traceable per-factor reasoning, states its own limits, revenue from analysis not equipment), Eldrick (API/MCP engine from launch-monitor data), True Path Golf, StickFitter, Kinetixx FIT; (E) human-in-the-loop remote — 2nd Swing (video session, Fit Pack). Nobody explains trade-offs well, nobody quantifies confidence, OEM tools are accused of recommending the golfer's aspiration rather than reality. Takomo's small catalogue (5 heads, 8 shaft families) makes explanation tractable; 60+ demo locations are the hand-off for close calls.

---

## 14. Plan, risks, open questions

**Timeline.** Saturday: engine wrapper + verdict object + perturbation confidence; TrackMan session (film every 7-iron swing in slow-mo beside the monitor; collect speed pairs; ask the fitters the questions below); capture module (tempo first, speed second); conversational UI; end-to-end on one demo golfer by evening. Sunday: polish, run all 16 demo golfers, record a walkthrough video as backup, one-page write-up (idea / what the PoC proves / accuracy validation / trust design / next steps), README, hand in.

**Roles.** Engine & eval · Capture (video) · Conversation & UX (ElevenLabs + caption strip) · Story (write-up, demo script, judging map).

**Risks.** Pose estimation eating Saturday → video is additive; demo must work without it. LLM saying something a fitter wouldn't → it only narrates the engine; answer bank as regression tests. Chatbot asking too much → ask only when it would flip a decision.

**Questions for Takomo (Saturday).** How was the 28k set produced (real fittings or synthetic)? Which input causes the most returns/support tickets when wrong? How is lie decided beyond the five examples? Can forged heads be bent after purchase? What does a Takomo fitter say when two heads are a close call? Can the Forge character be the fitter's avatar? Any constraints on LLM use in production (cost at ~100k fittings/month, EU data)?

---

## Sources

Market: takomogolf.com/pages/fitting-tool · mygolfspy.com/news-opinion/ping-webfit · golf.com/gear/irons/titleist-iron-selector-tool-t-series · pluggedingolf.com/the-next-evolution-in-club-fitting · mygolfspy.com/news-opinion/after-five-swings-wilson-fit-ai-knew-more-about-my-iron-game-than-i-did · fitmygolfclubs.com/blog/ai-golf-club-fitting · 2ndswing.com/golf-club-fittings/online · mygolfspy.com/buyers-guides/golf-balls/a-guide-to-the-best-and-worst-online-golf-ball-fitting-tools

Voice CX: assemblyai.com/voice-agent-report · hbr.org/2026/03/what-should-your-companys-ai-sound-like-to-customers · picovoice.ai/guide/voice-agents/voice-ux-latency-turn-taking · socsci.uci.edu (Steyvers, calibration gap, Nature MI) · sociable.co (voice AI trust survey) · learn.microsoft.com disclosure design patterns for synthetic voices

Swing video: github.com/JamesBKav/swing-analyzer · github.com/ryanboscobanze/GolfPosePro · developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/web_js · mytpi.com (measuring swing timing from video; linear kinematic sequence) · dewizgolf.com (five tempo measurements; max hand speed) · deepswing.io/golf-swing-speed-app · arxiv 1903.06528 (GolfDB/SwingNet)

ElevenLabs: elevenlabs.io/docs/api-reference/authentication


---

## Appendix A — `step1.html` (Step 1 prototype, full source)

Standalone mobile-first page. Open directly in a browser; no build step. Browser `speechSynthesis` stands in for ElevenLabs.

```html
<title>Takomo Fitter Prototype</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@600;700;800&display=swap">
<style>
/* Layout: a mobile-first website. One column, full-bleed on a phone; on wider screens the same column sits centred (max 480px) on the brand ground. Single dark look by choice — it is the brand. */
:root{
  --ground:#1C1C1C;
  --bg:#0B0B0B;
  --surface:#161616;
  --line:#2A2A2A;
  --line-strong:#3A3A3A;
  --fg:#F8F8F8;
  --fg-2:#C8C8C8;
  --fg-3:#8E8E8E;
  --fg-4:#5A5A5A;
  --accent:#FF6E01;
  --accent-ink:#0B0B0B;
  --accent-soft:rgba(255,110,1,.14);
  --display:'Barlow Condensed','Arial Narrow',Impact,sans-serif;
  --body:Barlow,'Helvetica Neue',Arial,sans-serif;
  color-scheme:dark;
}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--body);-webkit-font-smoothing:antialiased;padding:0;box-sizing:border-box}
.stage{min-height:100%;display:flex;flex-direction:column;align-items:stretch}
.phone{position:relative;width:100%;flex:1 1 auto;min-height:100%;background:var(--bg);display:flex;flex-direction:column;box-sizing:border-box}
.screen{flex:1 1 auto;min-height:100%;display:flex;flex-direction:column;gap:14px;box-sizing:border-box;padding-block:28px 24px;padding-left:max(20px,env(safe-area-inset-left,0px));padding-right:max(20px,env(safe-area-inset-right,0px));position:relative}
.screen[hidden]{display:none}
.demo{position:fixed;left:0;right:0;bottom:0;z-index:5;border-radius:0;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-bottom:calc(8px + env(safe-area-inset-bottom,0px))}
.screen{padding-bottom:88px}
/* wider screens: the same column, centred on the brand ground, like the site */
@media (min-width:700px){
  body{background:var(--ground)}
  .stage{align-items:center;padding-block:32px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
  .phone{max-width:480px;min-height:calc(100vh - 64px);border-left:1px solid var(--line);border-right:1px solid var(--line)}
  .screen{padding-left:28px;padding-right:28px;padding-block:36px 24px;padding-bottom:88px}
  .demo{left:auto;right:16px;bottom:16px;border-radius:8px;max-width:420px}
}
.sheet,.scrim{position:fixed}
@media (min-width:700px){.sheet{left:50%;right:auto;width:480px;transform:translate(-50%,105%)}.sheet.show{transform:translate(-50%,0)}}

.top{display:flex;justify-content:space-between;align-items:center}
.wordmark{font-family:var(--display);font-weight:700;letter-spacing:.3em;font-size:15px}
.units{display:flex;gap:2px;background:var(--surface);border-radius:3px;padding:2px}
.units button{font:600 11px/1 var(--body);letter-spacing:.06em;text-transform:uppercase;padding:6px 9px;border:0;border-radius:2px;background:transparent;color:var(--fg-3);cursor:pointer;transition:background .25s,color .25s}
.units button[aria-pressed="true"]{background:var(--line);color:var(--fg)}
.rule{height:1px;background:var(--line)}

/* The caption strip: the fitter is always saying something, and it is always readable. */
.cap{position:relative;border-left:3px solid var(--accent);padding-left:14px;display:flex;flex-direction:column;gap:6px;min-height:118px}
.cap-label{display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:700}
.dot{width:7px;height:7px;border-radius:50%;background:var(--accent);animation:pulse 1.4s ease-in-out infinite}
.cap.idle .dot{animation:pulse 1.4s ease-in-out infinite}
.cap.done .dot{animation:none;opacity:.5}
@keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(.55);opacity:.45}}
.cap-text{font-size:18px;line-height:25px;font-weight:500;min-height:75px}
.cap-text .w{transition:color .12s}
.cap-text .w.on{color:var(--accent)}
.cap-text .w.todo{color:var(--fg-4)}
.hear{align-self:flex-start;display:inline-flex;align-items:center;gap:6px;margin-top:2px;padding:6px 10px;border:1px solid var(--line-strong);border-radius:3px;background:transparent;color:var(--fg-2);font:600 12px/1 var(--body);letter-spacing:.04em;cursor:pointer}
.hear[hidden]{display:none}
.hear svg{width:14px;height:14px}

.doors{flex:1 1 auto;display:flex;flex-direction:column;gap:10px;justify-content:flex-end}
h1{margin:0 0 6px;font-family:var(--display);font-size:52px;line-height:48px;font-weight:800;text-transform:uppercase;letter-spacing:-.01em;text-wrap:balance}
.door{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 18px;border-radius:4px;text-decoration:none;color:var(--fg);background:var(--surface);border:1px solid var(--line);cursor:pointer;text-align:left;font-family:var(--body);transition:transform .15s,border-color .2s}
.door:active{transform:scale(.985)}
.door:focus-visible,.hear:focus-visible,.units button:focus-visible,.sheet button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.door .t{font-family:var(--display);font-size:22px;line-height:22px;font-weight:700;text-transform:uppercase}
.door .s{font-size:13px;color:var(--fg-3);margin-top:3px}
.door.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);padding:18px}
.door.primary .t{font-size:24px;line-height:24px}
.door.primary .s{color:var(--accent-ink);opacity:.85;font-weight:500}
.door svg{flex:none;width:26px;height:26px}
.door .arrow{font-size:20px;color:var(--fg-3)}
.fine{font-size:12px;line-height:16px;color:var(--fg-4);text-align:center;margin-top:4px}

/* entrance: from a visible resting state, short */
.rise{animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
.rise.d1{animation-delay:.08s}.rise.d2{animation-delay:.16s}.rise.d3{animation-delay:.24s}.rise.d4{animation-delay:.32s}
@keyframes rise{from{transform:translateY(10px);opacity:.001}to{transform:none;opacity:1}}

/* permission sheet */
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.6);opacity:0;pointer-events:none;transition:opacity .25s;z-index:8}
.scrim.show{opacity:1;pointer-events:auto}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:9;background:var(--surface);border-top:1px solid var(--line-strong);border-radius:16px 16px 0 0;padding:18px 20px calc(22px + env(safe-area-inset-bottom,0px));transform:translateY(105%);transition:transform .32s cubic-bezier(.2,.7,.2,1);display:flex;flex-direction:column;gap:12px}
.sheet.show{transform:none}
.sheet .h{font-family:var(--display);font-size:26px;line-height:26px;font-weight:700;text-transform:uppercase}
.sheet p{margin:0;font-size:14px;line-height:20px;color:var(--fg-2)}
.sheet .row{display:flex;gap:8px;margin-top:4px}
.btn{flex:1 1 0;height:48px;border-radius:4px;border:1px solid var(--line-strong);background:transparent;color:var(--fg);font:700 19px/1 var(--display);text-transform:uppercase;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}

/* step 2 placeholder */
.listen{flex:1 1 auto;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;text-align:center}
.mic{width:96px;height:96px;border-radius:50%;background:var(--accent);display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 14px var(--accent-soft);animation:breathe 1.6s ease-in-out infinite}
.mic.typing{background:var(--surface);border:1px solid var(--line-strong);box-shadow:none;animation:none}
@keyframes breathe{0%,100%{box-shadow:0 0 0 12px var(--accent-soft)}50%{box-shadow:0 0 0 24px rgba(255,110,1,.06)}}
.listen .big{font-family:var(--display);font-size:30px;line-height:30px;font-weight:700;text-transform:uppercase}
.listen .sub{font-size:14px;line-height:20px;color:var(--fg-3);max-width:280px}
.back{align-self:center;background:none;border:0;color:var(--fg-3);font:500 13px/1 var(--body);letter-spacing:.04em;cursor:pointer;padding:12px}
.tag{display:inline-block;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);border:1px solid var(--accent);border-radius:2px;padding:3px 7px}

/* demo controls, outside the phone */
.demo{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:center;padding:10px 14px;background:#111;border-top:1px solid var(--line);font-size:12px;color:var(--fg-3);max-width:100%;box-sizing:border-box}
.demo b{color:var(--fg-2);font-weight:600;letter-spacing:.08em;text-transform:uppercase;font-size:11px}
.demo button{font:600 12px/1 var(--body);padding:8px 10px;border-radius:3px;border:1px solid var(--line-strong);background:transparent;color:var(--fg-2);cursor:pointer}
.demo button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent)}
@media (prefers-reduced-motion:reduce){.rise,.dot,.mic{animation:none!important}.sheet,.scrim{transition:none}}
</style>

<div class="stage">
<div class="phone" id="phone">

  <section class="screen" id="s1">
    <div class="top rise">
      <span class="wordmark">TAKOMO</span>
      <div class="units" role="group" aria-label="Units">
        <button type="button" id="u-imp" aria-pressed="true">yd · mph</button>
        <button type="button" id="u-met" aria-pressed="false">m · km/h</button>
      </div>
    </div>
    <div class="rule rise"></div>

    <div class="cap idle rise d1" id="cap">
      <div class="cap-label"><span class="dot"></span><span id="cap-label">Fitter</span></div>
      <div class="cap-text" id="cap-text" aria-live="polite"></div>
      <button type="button" class="hear" id="hear" hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 10v4h4l5 4V6L8 10H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/></svg>
        Tap to hear him
      </button>
    </div>

    <div class="doors">
      <h1 class="rise d2" id="h1">Find<br>your fit.</h1>
      <button type="button" class="door primary rise d2" id="d-talk">
        <span><span class="t" id="d-talk-t">Talk to the fitter</span><span class="s" id="d-talk-s">~10 min · he measures with you and watches you swing</span></span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
      </button>
      <button type="button" class="door rise d3" id="d-type">
        <span><span class="t">Same thing, typing</span><span class="s">on a train, in a meeting, whatever</span></span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8"/></svg>
      </button>
      <button type="button" class="door rise d4" id="d-quiz">
        <span><span class="t">Quick quiz</span><span class="s">~3 min · eight questions, each says why it’s asked</span></span>
        <span class="arrow">→</span>
      </button>
      <p class="fine rise d4">No email to see your fit · it’s saved so you can come back · same engine either way</p>
    </div>

    <div class="scrim" id="scrim"></div>
    <div class="sheet" id="sheet" role="dialog" aria-labelledby="sheet-h" aria-modal="true">
      <span class="tag">Microphone</span>
      <div class="h" id="sheet-h">He needs to hear you.</div>
      <p>Your voice is turned into text on the spot. Nothing is recorded or kept. You can switch to typing any time.</p>
      <div class="row">
        <button type="button" class="btn" id="sheet-no">Not now</button>
        <button type="button" class="btn primary" id="sheet-yes">Allow</button>
      </div>
    </div>
  </section>

  <section class="screen" id="s2" hidden>
    <div class="top">
      <span class="wordmark">TAKOMO</span>
      <span style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--fg-3)">AI fitter · irons</span>
    </div>
    <div class="rule"></div>
    <div class="cap" id="cap2">
      <div class="cap-label"><span class="dot"></span><span>Fitter</span></div>
      <div class="cap-text" id="cap2-text"></div>
    </div>
    <div class="listen">
      <div class="mic" id="mic">
        <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="#0B0B0B" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
      </div>
      <div class="big" id="s2-big">Listening</div>
      <div class="sub" id="s2-sub">Step 2 starts here. This prototype stops at the door — the chat is the next build.</div>
      <span class="tag">Step 2 · next</span>
    </div>
    <button type="button" class="back" id="back">← Back to the start</button>
  </section>

</div>

<div class="demo" id="demo">
  <b>Demo</b>
  <button type="button" id="dm-reset">Replay</button>
  <button type="button" id="dm-return" aria-pressed="false">Returning golfer</button>
  <button type="button" id="dm-mute" aria-pressed="false">Mute voice</button>
  <span id="dm-note"></span>
</div>
</div>

<script>
(function(){
  const $ = id => document.getElementById(id);
  const cap = $('cap'), capText = $('cap-text'), hear = $('hear');
  const s1 = $('s1'), s2 = $('s2');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const LINES = {
    fresh: "Hey. I’m Takomo’s AI fitter — not a person, but I know these irons better than most people do. Ten minutes, I explain every spec, and I tell you when I’m guessing. Talk or type?",
    back:  "You’re back. Last time we left 201 versus 201T open. Got sim numbers, or want to pick up where we were?",
    talk:  "Right. Tell me about your game — how long you’ve played, how often, and what you’d like these irons to fix.",
    type:  "No problem, type away. Same questions, same fit. Tell me about your game.",
    quiz:  "Eight questions. I’ll say why I’m asking each one.",
    cont:  "Good. Where were we — you were going to hit both at a demo bay. Did you?"
  };

  let state = { returning:false, muted:false, audioOK:false, speaking:false, typer:null, utter:null };

  // ---- caption: types out, or highlights words in sync with the voice
  function setWords(text){
    capText.innerHTML = '';
    text.split(' ').forEach((w,i)=>{
      const s = document.createElement('span'); s.className='w todo'; s.textContent = w + ' '; s.dataset.i=i; capText.appendChild(s);
    });
    return capText.querySelectorAll('.w');
  }
  function stopCaption(){
    if(state.typer){ clearInterval(state.typer); state.typer=null; }
    if(window.speechSynthesis){ speechSynthesis.cancel(); }
    state.speaking=false;
    capText.querySelectorAll('.w').forEach(w=>{ w.classList.remove('on','todo'); });
    cap.classList.remove('idle'); cap.classList.add('done');
  }
  function typeLine(text, done){
    const words = setWords(text);
    let i=0; cap.classList.add('idle'); cap.classList.remove('done');
    const step = reduce ? 0 : 150;
    if(!step){ words.forEach(w=>w.classList.remove('todo')); cap.classList.remove('idle'); cap.classList.add('done'); done&&done(); return; }
    state.typer = setInterval(()=>{
      if(i>0) words[i-1].classList.remove('on');
      if(i<words.length){ words[i].classList.remove('todo'); words[i].classList.add('on'); i++; }
      else { clearInterval(state.typer); state.typer=null; cap.classList.remove('idle'); cap.classList.add('done'); done&&done(); }
    }, step);
  }
  function pickVoice(){
    const vs = speechSynthesis.getVoices();
    const pref = ['Daniel','Google UK English Male','Microsoft Ryan','Alex','Google US English','Samantha'];
    for(const p of pref){ const v = vs.find(v=>v.name.includes(p)); if(v) return v; }
    return vs.find(v=>/^en/i.test(v.lang)) || vs[0] || null;
  }
  function speakLine(text, done){
    if(!window.speechSynthesis || state.muted || !state.audioOK){ typeLine(text, done); return; }
    speechSynthesis.cancel();
    const words = setWords(text);
    cap.classList.add('idle'); cap.classList.remove('done');
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(); if(v) u.voice = v;
    u.rate = 1.02; u.pitch = 0.92;
    let idx = 0; const starts=[]; let pos=0;
    text.split(' ').forEach(w=>{ starts.push(pos); pos += w.length+1; });
    u.onboundary = e => {
      if(e.name && e.name!=='word') return;
      while(idx<starts.length && starts[idx] <= e.charIndex){ idx++; }
      words.forEach((w,i)=>{ w.classList.toggle('on', i===idx-1); if(i<idx) w.classList.remove('todo'); });
    };
    u.onend = () => { if(state.utter!==u) return; words.forEach(w=>w.classList.remove('on','todo')); cap.classList.remove('idle'); cap.classList.add('done'); state.speaking=false; done&&done(); };
    u.onerror = () => { if(state.utter!==u) return; stopCaption(); typeLine(text, done); };
    state.utter = u; state.speaking = true;
    speechSynthesis.speak(u);
    // Safari sometimes never fires boundary events: fall back to a timed reveal.
    setTimeout(()=>{ if(state.speaking && !words[0].classList.contains('on') && words[0].classList.contains('todo')){ let i=0; state.typer=setInterval(()=>{ if(i<words.length){ words[i].classList.remove('todo'); i++; } else { clearInterval(state.typer); state.typer=null; } }, 150); } }, 700);
  }
  function say(text, done){
    stopCaption();
    if(state.audioOK && !state.muted && window.speechSynthesis){ hear.hidden = true; speakLine(text, done); }
    else { hear.hidden = state.muted || !window.speechSynthesis; typeLine(text, done); }
  }

  // ---- screen 1 content by state
  function renderS1(){
    const r = state.returning;
    $('h1').innerHTML = r ? 'Welcome<br>back.' : 'Find<br>your fit.';
    $('d-talk-t').textContent = r ? 'Continue where we left off' : 'Talk to the fitter';
    $('d-talk-s').textContent = r ? '201 vs 201T still open · 2 min to settle it' : '~10 min · he measures with you and watches you swing';
    $('dm-note').textContent = r ? 'saved fit found (simulated)' : '';
  }
  function start(){
    stopCaption();
    s2.hidden = true; s1.hidden = false;
    hideSheet();
    renderS1();
    // re-run the entrance
    s1.querySelectorAll('.rise').forEach(el=>{ el.style.animation='none'; void el.offsetWidth; el.style.animation=''; });
    setTimeout(()=> say(state.returning ? LINES.back : LINES.fresh), reduce ? 0 : 500);
  }

  // ---- doors
  function go(kind){
    stopCaption(); // barge-in: he stops mid-sentence
    if(kind==='talk'){ showSheet(); return; }
    enterS2(kind);
  }
  function enterS2(kind){
    hideSheet();
    s1.hidden = true; s2.hidden = false;
    const mic = $('mic');
    const voice = kind==='talk';
    mic.classList.toggle('typing', !voice);
    $('s2-big').textContent = voice ? 'Listening' : (kind==='quiz' ? 'Question 1 of 8' : 'Your turn — type');
    $('s2-sub').textContent = voice ? 'Hold the button and talk. Everything he hears shows up as text so you can correct him.' : (kind==='quiz' ? 'Same engine as the chat. He still explains every question.' : 'Same fitter, keyboard instead of mic. He still talks — you read.');
    const line = state.returning && voice ? LINES.cont : LINES[kind];
    const words = $('cap2-text'); words.textContent = line;
    if(state.audioOK && !state.muted && window.speechSynthesis){ const u=new SpeechSynthesisUtterance(line); const v=pickVoice(); if(v)u.voice=v; u.rate=1.02; u.pitch=0.92; speechSynthesis.speak(u); }
  }
  function showSheet(){ $('scrim').classList.add('show'); $('sheet').classList.add('show'); $('sheet-yes').focus(); }
  function hideSheet(){ $('scrim').classList.remove('show'); $('sheet').classList.remove('show'); }

  $('d-talk').addEventListener('click', ()=>go('talk'));
  $('d-type').addEventListener('click', ()=>go('type'));
  $('d-quiz').addEventListener('click', ()=>go('quiz'));
  $('sheet-yes').addEventListener('click', ()=>enterS2('talk'));
  $('sheet-no').addEventListener('click', ()=>enterS2('type'));   // denied → typing, no second ask
  $('scrim').addEventListener('click', hideSheet);
  $('back').addEventListener('click', start);

  // first tap anywhere unlocks audio for the rest of the session (browsers need a gesture)
  hear.addEventListener('click', ()=>{ state.audioOK = true; say(state.returning ? LINES.back : LINES.fresh); });
  document.addEventListener('pointerdown', ()=>{ if(!state.audioOK && !state.muted && window.speechSynthesis){ state.audioOK = true; } }, {capture:true});

  // units
  $('u-imp').addEventListener('click', ()=>setUnits('imp'));
  $('u-met').addEventListener('click', ()=>setUnits('met'));
  function setUnits(u){
    $('u-imp').setAttribute('aria-pressed', u==='imp'); $('u-met').setAttribute('aria-pressed', u==='met');
    try{ localStorage.setItem('fitter.units', u); }catch(e){}
  }
  try{ const u = localStorage.getItem('fitter.units'); if(u) setUnits(u); }catch(e){}

  // demo controls
  $('dm-reset').addEventListener('click', start);
  $('dm-return').addEventListener('click', e=>{ state.returning = !state.returning; e.currentTarget.setAttribute('aria-pressed', state.returning); start(); });
  $('dm-mute').addEventListener('click', e=>{ state.muted = !state.muted; e.currentTarget.setAttribute('aria-pressed', state.muted); if(state.muted) stopCaption(); hear.hidden = state.muted || cap.classList.contains('done'); });

  if(window.speechSynthesis){ speechSynthesis.onvoiceschanged = ()=>{}; speechSynthesis.getVoices(); }
  start();
})();
</script>
```
