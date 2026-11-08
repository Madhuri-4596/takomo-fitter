# Takomo Fitting Lab: start here

**Challenge:** The Fitter in Your Pocket (Takomo Golf, Since AI Hackathon 2026, Turku, 6–8 November 2026)  
**Pack version:** 1.1.0 (5 November 2026) · **Made by:** Since AI ry from Takomo Golf's challenge pack · **Data:** synthetic

Takomo's own pack (the participant PDF and the product data files) explains club fitting and today's online tool. Read it first: it is in `takomo_official/`, unchanged.

This lab adds what is hard to get in 48 hours:

| You get | Why it helps |
|---|---|
| **28,016 synthetic golfers** with the truth known: real swing speed, body measurements, hand size, tempo, the lie their swing actually needs | You can measure how wrong self-reported answers are and prove your method fixes them |
| **Four kinds of evidence per golfer**: the 12-question answers, simulated phone measurements, at-home tests and launch-monitor shots (210,290 shots) | Build and test data fusion before you have your own recordings |
| **`takomo_fit` Python package**: today's tool as code (with Takomo's corrections of 5 November and the PING-based static chart), a calibrated ball-flight model and a scorer | Everyone starts from the same working baseline instead of rebuilding the rules |
| **A phone-camera kit**: printable measurement sheet, tested geometry code for hand and finger length, height, wrist-to-floor, swing tempo and the ball-line lie test | Measure instead of asking, with only a phone and a printer |
| **16 demo golfers with stories** | Ready-made cases for your demo and pitch |
| **A plan for Saturday's simulator visit** (Takomo, 11.00–12.00) and a shared shot log | Video and numbers of real swings; teams are encouraged to pool what they record |

Nothing here is real customer data. All golfers are generated; see `docs/SIMULATOR.md` for every assumption.

**New in 1.1.0 (5 November).** Head scoring, length, lie, grip and the stock rule now follow Takomo's live tool, as confirmed by Takomo on 5 November. That means the speed-points table, the over-92 cap, the forgiveness penalty, the PING-based static chart, and midsize grips counting as stock. The population is calibrated to Takomo's real grip mix. Finger length is added for PING's two-measurement grip method. The Saturday simulator plan is included. If you downloaded 1.0.0, replace it: the labels changed.

## Ten-minute start

```bash
pip install numpy pandas pytest                       # Python 3.9+
pip install opencv-contrib-python reportlab           # only for camera/ (the camera tests skip without OpenCV)
python examples/quickstart.py       # loads the data, runs today's tool, scores it, shows one golfer
python examples/baselines.py --data data/dev
python -m pytest -q tests           # 87 tests with OpenCV, under 3 seconds
```

## What is in the folder

```
START_HERE.md / START_HERE.html   this guide
takomo_official/                  Takomo's participant pack and product files, unchanged
data/
  train/  20,000 golfers, labels included
  dev/     3,000 golfers, labels included (score yourself here)
  test/    5,000 golfers, NO labels (organisers score these)
  demo/       16 golfers with stories, labels included
takomo_fit/                       baseline.py · physics.py · score.py · products.py
camera/                           measurement sheet (PDF) and measuring code
examples/                         quickstart.py · baselines.py
docs/                             data dictionary, simulator, rules, camera playbook, public resources, event protocol
tests/
```

Each data split holds the same six files:

| File | One row per | What it is |
|---|---|---|
| `golfers.csv` | golfer | age, sex, handedness, rounds per year, loft of own 7-iron (if the golfer knows it) |
| `questionnaire.csv` | golfer | the 12 answers as the golfer gave them, including their mistakes |
| `phone_measurements.csv` | golfer | simulated output of photo and video measurement (missing when not captured) |
| `home_tests.csv` | test shot | ball-line lie test and impact-spray strike marks from home practice |
| `shots.csv` | shot | launch-monitor shots: TrackMan-style fitting sessions and consumer home radar |
| `labels.csv` | golfer | the truth and the reference fitting (train, dev and demo only) |

Full column list: `docs/DATA_DICTIONARY.md`.

## How results are scored

The reference spec for each golfer is **Takomo's current rules applied to the golfer's true values**, with one change: lie comes from how the golfer actually delivers the club, which is what a fitter's ball-line or lie-board test reveals. So the benchmark asks one clear question: *how close can you get to the right answer when all you have is what a golfer can give you at home?*

`python -m takomo_fit.score --pred your_predictions.csv --labels data/dev/labels.csv`

Spec Agreement Score (0–100) per golfer: head 20, flex 20, shaft material 10, shaft model 5, length 12, lie 15, grip 8, stock or custom 5, handedness 5. Exact match only, with two exceptions: the reference runner-up head earns half, and where the reference is a **close call** (for example a swing speed within 1 mph of a flex line) the neighbouring answer earns full credit. For a flex close call that means the whole shaft the rules give on the other side of the line. A spec Takomo cannot build (a shaft not offered on that head, a length or lie outside the options) scores 0. The scorer also reports the share of golfers whose whole spec is right, and the error of any measurements you estimate (speed, height, wrist-to-floor, hand length, lie need, carry per head).

| Baseline (dev split) | Score | Whole spec right |
|---|---|---|
| B1 · today's tool on the golfer's own answers | 77.1 | 13.7 % |
| B2 · same rules, answers replaced by phone and launch-monitor evidence where it exists | 83.6 | 25.3 % |
| Oracle · today's rules on true values | 100 | 100 % |

Lie is where the most is lost: B1 matches the reference lie for 41 % of golfers. Swing speed is next: the golfer's speed-or-carry answer lands in the right speed bucket for 57 % of golfers.

Stock or custom carries little weight because 91 % of reference specs are custom: always answering "custom" already scores well. The interesting part is saying *when* stock, with its 30-day return, is worth keeping.

**Submitting test predictions (optional).** Predict all 5,000 golfers in `data/test` in the format above and attach the CSV, named `<team>_takomo_test_predictions.csv`, to your final submission. The organisers score it against the hidden labels and give the result to Takomo's judges as evidence for the accuracy criterion. Missing golfers score 0.

**This score is a tool, not the judging.** Takomo's judges use the brief's criteria: trust and usability 35 %, idea 25 %, accuracy 20 %, feasibility and business impact 20 %. A strong score is good evidence for the accuracy part. It says nothing about whether a golfer would trust your explanation, which is worth more.

## What the synthetic data can and cannot prove

It can show that a method recovers the truth from noisy inputs **under this simulator's assumptions**. It cannot show that the method matches a professional fitter on real golfers. For that, use the real recordings from Saturday's simulator visit (`docs/EVENT_SESSION_PROTOCOL.md`) and ask Takomo's fitters. Say which kind of evidence you have in your write-up; judges will ask.

Known simplifications, in short: one 7-iron only; no turf interaction; a simple roll model; self-report errors and phone-measurement errors are assumed, not measured; the static lie chart approximates PING's colour code chart from eleven known points. All listed in `docs/SIMULATOR.md`.

## Using the phone camera

What a phone can measure, how well, and how: `docs/PHONE_CAMERA_PLAYBOOK.md`. Short version:

| Input today's tool asks for | Measure it instead with |
|---|---|
| Height, wrist-to-floor | One full-body photo against a wall with the printed sheet for scale |
| Glove size | Photo of the hand on the sheet → hand length (and finger length, which PING's grip chart also uses) → grip size |
| Tempo | Any face-on swing video; pose landmarks give backswing and downswing time |
| Swing speed or carry | 240 fps slow motion (rough), or a launch monitor if the golfer has one |
| Lie (not asked today) | Ball-line test: a marker line on the ball, five shots off a hard mat, a photo of the face |
| Strike pattern (not asked today) | Foot-powder spray or impact tape on the face, photo after five shots |

## Rules for using this pack

- Code (MIT) and synthetic data (CC BY 4.0), copyright Since AI ry. Takomo's files in `takomo_official/` belong to Takomo Golf and are shared for this hackathon.
- Do not present simulated numbers as real golfer data in your pitch.
- External datasets have their own licences; check `docs/PUBLIC_RESOURCES.md` before you use one. GolfDB, for example, is non-commercial.

## Contacts

Questions about golf, fitting or Takomo's products: Jonas Koisti, Takomo Golf (jonas@takomogolf.com, or Takomo's Discord channel). Questions about the data or the code: the Since AI organisers on Discord.
