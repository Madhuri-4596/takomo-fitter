# Takomo AI iron fitter — project notes for Claude Code

Read `docs/HANDOVER.md` before starting any task. It is the full context from the
ideation session: concept, engine rules, interview script, answer bank, swing-video
method, voice-CX rules, brand rules, screen specs, and the Step 1 prototype.

## Layout

| Path | What it is |
|---|---|
| `docs/HANDOVER.md` | Source of truth for the product. The PDF beside it is a print export of the same text. |
| `data/takomo_products.json` | Takomo product data (as of 2026-10-13): 5 heads, 35 shaft records, 3 grips, length/lie options, and today's questionnaire rules (`current_tool_rules`). **The engine reads this file only.** |
| `data/heads.csv`, `data/shafts.csv`, `data/grips.csv` | Flat exports of the same data from the Takomo teams pack. Verified identical to the JSON. `heads.csv` is semicolon-separated; the other two are comma-separated. Prefer the JSON. |
| `prototypes/fitter.html` | **Current UI prototype.** The whole talk/type flow: hello → chat → measure → swing → your fit → ask, then go. Standalone, no build step. A scripted demo golfer answers (mic and camera are simulated); the verdict is hard-coded for that golfer. Demo bar jumps to any screen. |
| `prototypes/step1.html` | The locked Step 1 "hello" screen on its own, as handed over. Kept for reference; `fitter.html` supersedes it. |

## Prototype conventions

- One file, plain HTML/CSS/JS, mobile-first (480 px column centred on wider screens). Browser `speechSynthesis` stands in for ElevenLabs; keep the word-highlight caption logic when swapping it.
- Every fitter screen shares one spine: top bar with units toggle → progress (chat · measure · swing · your fit) → caption strip → stage → action bar (mic / type / skip) in the same place.
- Confidence pills: Sure (grey outline), Hmm (orange outline), Guess (orange fill). Every input tile carries its source tag (said / measured / seen / estimated).
- The quick quiz and the engine are out of scope for the UI prototype. Do not add fitting logic to `fitter.html`; the verdict there is scripted.

## Build rules (non-negotiable, from the handover)

- The LLM never picks a spec. Engine decides → confidence module says how sure → LLM narrates and asks.
- Every "why" and every answer is built only from fields in `data/takomo_products.json` or the verdict object. Anything else gets the decline line and the human-fitter exit.
- The fitter observes swings; it never coaches them. Takomo brand rule: clubs don't fix swings, and we don't say they do.
- Accuracy must be ≥ today's tool on the 28k set. Same rules, better inputs.
- Disclose AI in the first line. Human-fitter exit one tap away, with context passed on.
- Order is a payment-class action: read the full spec back, explicit yes, say "ordered" only after Shopify confirms.
- Irons only. English only. Everything 7-iron based. Yards/mph/ft-in default, metric behind a toggle, one internal unit.

## Data notes

- Head slugs: `101-mkii`, `201-mkii`, `201t-mkii`, `301-cb`, `301-mb`. Score buckets 5→1 in that order.
- MKII heads take KBS Tour, KBS Tour Lite, Mitsubishi MMT, True Temper Denali Silver. 301 heads take KBS Tour (301 build), KBS Tour Lite (301 build), KBS C-Taper, KBS C-Taper Lite, Takomo Obsidian, Fujikura Atmos Red. A shaft's `heads` array is authoritative.
- `tempo_profile: null` means any tempo. Flex codes: lady L, senior A, regular R, stiff S, x-stiff X.
- Lie has five worked examples and no formula. Default lie confidence to Medium until Takomo gives one. Skipped wrist-to-floor → Low.
- `putter_length_by_height_cm` is in the JSON but out of scope (irons only).

## Brand

Midnight Black `#0B0B0B` (never pure black), Washed White `#F8F8F8`, Tiger Orange `#FF6E01` as accent only.
Barlow Condensed for headlines, Barlow for body. Voice: candid, funny, technical, never a swing tip.
Full rules in `docs/HANDOVER.md` §9; CSS tokens in `prototypes/step1.html`.
