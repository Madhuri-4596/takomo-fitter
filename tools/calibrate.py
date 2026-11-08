"""Replace the app's assumed error margins with numbers measured on the Takomo Fitting Lab data, and check
whether the High / Medium / Low confidence labels mean what they say.

  train split  ->  self-report errors (questionnaire vs truth), strike-spread thresholds (TrackMan impact location)
  dev split    ->  held out: how often each spec line is right, per confidence level

Writes app/calibration.json (read by the app) and prints a report.

  .venv/Scripts/python tools/calibrate.py [--dev-limit 3000]

The kit's data is simulated (see kit/docs/SIMULATOR.md): these numbers describe the simulator's golfers, which
is the best evidence available until real golfers are measured at the event.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import calibration, evidence, fitting  # noqa: E402

DATA = ROOT / "kit" / "data"
OUT = ROOT / "app" / "calibration.json"


def load(split: str):
    q = pd.read_csv(DATA / split / "questionnaire.csv")
    lab = pd.read_csv(DATA / split / "labels.csv")
    return q.merge(lab, on="golfer_id")


def spread(err: pd.Series) -> dict:
    e = err.dropna()
    return {"n": int(len(e)), "bias": round(float(e.mean()), 2), "sd": round(float(e.std()), 2),
            "p68_abs": round(float(np.percentile(np.abs(e - e.mean()), 68)), 2),
            "p90_abs": round(float(np.percentile(np.abs(e), 90)), 2)}


def self_report_errors(d: pd.DataFrame) -> dict:
    sp = d[d.speed_or_carry == "speed"]
    ca = d[d.speed_or_carry == "carry"]
    return {
        "height_cm": spread(d.height_cm - d.true_height_cm),
        "wrist_to_floor_cm": spread(d.wrist_to_floor_cm - d.true_wrist_to_floor_cm),
        "speed_mph": spread(sp.reported_7i_speed_mph - sp.true_7i_speed_mph),
        "carry_yd": spread(ca.reported_7i_carry_yd - ca.own_true_mean_carry_yd),
        "score_bucket_wrong": round(float((d.score_bucket != d.ref_score_bucket).mean()), 3),
        "tempo_wrong": round(float((d.tempo != d.true_tempo).mean()), 3),
        "glove_wrong": round(float((d.glove != d.true_glove.str.replace("M-L", "M/L")).mean()), 3),
    }


def strike_thresholds(split: str) -> dict:
    s = pd.read_csv(DATA / split / "shots.csv", usecols=["golfer_id", "session", "club", "impact_offset_mm"])
    s = s[(s.session == "trackman_fitting") & (s.club == "own_7i")].dropna(subset=["impact_offset_mm"])
    sd = s.groupby("golfer_id").impact_offset_mm.std().dropna()
    return {"n_golfers": int(len(sd)), "sd_tertile_low_mm": round(float(sd.quantile(1 / 3)), 1),
            "sd_tertile_high_mm": round(float(sd.quantile(2 / 3)), 1), "sd_median_mm": round(float(sd.median()), 1)}


def form_of(r) -> dict:
    f = {"age": int(r.age), "sex": r.sex, "handedness": r.handedness, "rounds_per_year": r.rounds_per_year,
         "score_bucket": int(r.score_bucket), "speed_or_carry": r.speed_or_carry,
         "characteristics": [c for c in str(r.characteristics).split("|") if c and c != "nan"],
         "flight": r.flight, "tempo": r.tempo, "height_cm": float(r.height_cm),
         "wrist_to_floor_cm": float(r.wrist_to_floor_cm), "glove": r.glove}
    if r.speed_or_carry == "speed":
        f["speed_mph"] = float(r.reported_7i_speed_mph)
    else:
        f["carry_yd"] = float(r.reported_7i_carry_yd)
    return f


def graded(d: pd.DataFrame, limit: int):
    """Yields (line, confidence line dict, right?) for each golfer's answers vs the reference fit."""
    for r in d.head(limit).itertuples():
        form = form_of(r)
        try:
            best = fitting.recommend(fitting.answers_from_form(form))["best"]
        except Exception:                      # noqa: BLE001 - incomplete simulated answers
            continue
        conf = evidence.verdict_confidence(form, None, None, None)
        if not conf:
            continue
        right = {
            "Head": best["head"] == r.ref_head,
            "Shaft": str(best["flex_label"]).lower() == str(r.ref_flex).lower(),
            "Shaft model": best["shaft_model"] == r.ref_shaft_model,
            "Length": float(best["length_in"]) == float(r.ref_length_in),
            "Lie": int(best["lie_deg"]) == int(r.ref_lie_deg),
            "Grip": best["grip"] == r.ref_grip,
        }
        for k, ok in right.items():
            yield k, conf["lines"][k], ok


def learn_line_accuracy(d: pd.DataFrame, limit: int) -> dict:
    """Training split: per situation (line, inputs that could flip it, answer source), the share that was right."""
    t: dict = {}
    for _, line, ok in graded(d, limit):
        c = t.setdefault(line["key"], [0, 0]); c[0] += ok; c[1] += 1
    return {k: {"right": round(a / n, 3), "n": n} for k, (a, n) in sorted(t.items())}


def check_confidence(d: pd.DataFrame, limit: int) -> dict:
    """Held out: per line and confidence level, how often the spec from the golfer's answers matched the reference."""
    tally: dict = {}
    overall: dict = {}
    for k, line, ok in graded(d, limit):
        lvl = line["level"]
        t = tally.setdefault(k, {}).setdefault(lvl, [0, 0])
        t[0] += ok; t[1] += 1
        o = overall.setdefault(lvl, [0, 0]); o[0] += ok; o[1] += 1
    fmt = lambda t: {"right": round(t[0] / t[1], 3), "n": t[1]}
    return {"by_line": {k: {lvl: fmt(t) for lvl, t in v.items()} for k, v in tally.items()},
            "overall": {lvl: fmt(t) for lvl, t in overall.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-limit", type=int, default=3000)
    ap.add_argument("--train-limit", type=int, default=8000)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    train = load("train")
    keep = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    cal = {**{k: v for k, v in keep.items() if k == "ball_line"},          # fitted by tools/fit_ball_line.py
           "source": "Takomo Fitting Lab 2026 simulated data (kit/data); train split for errors, dev split for checks",
           "self_report": self_report_errors(train), "strike": strike_thresholds("train")}
    print(json.dumps(cal, indent=1))
    calibration.DATA = {**cal, "line_accuracy": {}}          # learn from the training split with the rules only
    print(f"learning confidence levels from {a.train_limit} training golfers...")
    cal["line_accuracy"] = learn_line_accuracy(train, a.train_limit)
    calibration.DATA = cal                                    # the dev check uses the learned table
    if not a.no_write:
        OUT.write_text(json.dumps(cal, indent=1), encoding="utf-8")
        print("wrote", OUT)
    print("\nconfidence check on the dev split (held out):")
    res = check_confidence(load("dev"), a.dev_limit)
    print(json.dumps(res, indent=1))
    if not a.no_write:
        cal["dev_confidence_check"] = res
        OUT.write_text(json.dumps(cal, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
