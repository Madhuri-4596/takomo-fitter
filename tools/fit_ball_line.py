"""Fit the ball-line lie model on the kit's training golfers and check it on the held-out dev split.

lie need (deg) ~ a * static-chart reading (from height and wrist-to-floor) + b * mean ball-line tilt + c

Writes the coefficients and the measured accuracy into app/calibration.json ("ball_line").

  .venv/Scripts/python tools/fit_ball_line.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import fitting  # noqa: E402

DATA = ROOT / "kit" / "data"
OUT = ROOT / "app" / "calibration.json"


def table(split: str, body: str) -> pd.DataFrame:
    """One row per golfer with a ball-line test: the static reading, the mean tilt and the truth."""
    t = pd.read_csv(DATA / split / "home_tests.csv")
    t = t[t.test == "ball_line_tilt"].groupby("golfer_id").value.agg(["mean", "count", "std"]).reset_index()
    lab = pd.read_csv(DATA / split / "labels.csv")[["golfer_id", "true_lie_need_deg", "ref_lie_deg",
                                                     "true_height_cm", "true_wrist_to_floor_cm"]]
    q = pd.read_csv(DATA / split / "questionnaire.csv")[["golfer_id", "height_cm", "wrist_to_floor_cm"]]
    d = t.merge(lab, on="golfer_id").merge(q, on="golfer_id")
    h, w = (d.true_height_cm, d.true_wrist_to_floor_cm) if body == "true" else (d.height_cm, d.wrist_to_floor_cm)
    d["static_raw"] = [fitting.lie_detail(a, b)["raw"] for a, b in zip(h, w)]
    return d


def fit(d: pd.DataFrame):
    X = np.column_stack([d.static_raw, d["mean"], np.ones(len(d))])
    coef, *_ = np.linalg.lstsq(X, d.true_lie_need_deg, rcond=None)
    return coef


def predict(coef, static_raw, mean_tilt):
    return coef[0] * np.asarray(static_raw) + coef[1] * np.asarray(mean_tilt) + coef[2]


def accuracy(pred, ref) -> dict:
    lie = np.clip(np.round(pred), -3, 3)
    return {"exact": round(float((lie == ref).mean()), 3), "within_1": round(float((np.abs(lie - ref) <= 1).mean()), 3)}


def main():
    out = {}
    for body in ("self", "true"):          # self-reported body (worst case) and a perfectly measured body
        tr, dv = table("train", body), table("dev", body)
        coef = fit(tr)
        resid = tr.true_lie_need_deg - predict(coef, tr.static_raw, tr["mean"])
        out[body] = {
            "coef_static": round(float(coef[0]), 4), "coef_tilt": round(float(coef[1]), 4), "intercept": round(float(coef[2]), 4),
            "resid_sd_deg": round(float(resid.std()), 3), "n_train": int(len(tr)), "n_dev": int(len(dv)),
            "shots_median": float(tr["count"].median()),
            "dev_with_ball_line": accuracy(predict(coef, dv.static_raw, dv["mean"]), dv.ref_lie_deg),
            "dev_static_only": accuracy(dv.static_raw, dv.ref_lie_deg),
        }
    print(json.dumps(out, indent=1))
    cal = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    cal["ball_line"] = out
    OUT.write_text(json.dumps(cal, indent=1), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
