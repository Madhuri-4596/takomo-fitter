"""Score fitting predictions against reference specs.

Usage:
  python -m takomo_fit.score --pred my_predictions.csv --labels data/dev/labels.csv

Prediction CSV columns (one row per golfer_id; missing golfers score 0):
  golfer_id, head, shaft, length_in, lie_deg, grip, purchase, handedness
Optional measurement columns (reported as MAE, not part of the score):
  est_7i_speed_mph, est_height_cm, est_wrist_to_floor_cm, est_hand_length_cm,
  est_lie_need_deg, est_carry_yd_<head-slug>

Spec Agreement Score (0-100) per golfer, averaged:
  head 20, shaft flex 20, shaft material 10, shaft model 5, length 12, lie 15,
  grip 8, stock/custom 5, handedness 5.
A spec Takomo cannot build (unknown shaft slug, shaft not offered on the head,
length or lie outside the options, unknown grip) scores 0 for that golfer.
Credit is exact-match, with two exceptions:
  - head: the reference runner-up earns 0.5 (today's tool shows two heads);
  - close calls: where the reference is flagged as a close call, the adjacent
    answer (one flex, 0.5 in, 1 deg, one grip size, or the runner-up head)
    earns full credit. For a flex close call that means the whole shaft the
    rules give on the other side of the line (ref_shaft_alt): flex, material
    and model. Stock/custom then counts as right if it is the correct
    consequence of the predicted spec. A close call is a genuine judgement
    call, and the challenge asks teams to show it as one rather than guess.
Also reported: full_spec_exact_pct, the share of golfers whose whole buildable
spec (head, shaft, length, lie, grip, purchase, handedness) matches under the same rules.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from . import products as PR

WEIGHTS = dict(head=20, flex=20, material=10, shaft_model=5, length=12, lie=15, grip=8, purchase=5, handedness=5)
REQUIRED = ["golfer_id", "head", "shaft", "length_in", "lie_deg", "grip", "purchase", "handedness"]


def _num(x):
    try:
        return float(str(x).strip().replace('"', "").replace("in", "").replace("deg", "").replace("°", ""))
    except (TypeError, ValueError):
        return np.nan


def clean(pred: pd.DataFrame) -> pd.DataFrame:
    """Validate columns and normalise spelling (case, spaces, units)."""
    missing = [c for c in REQUIRED if c not in pred.columns]
    if missing:
        raise ValueError(f"prediction file is missing columns: {missing}")
    if pred["golfer_id"].duplicated().any():
        raise ValueError("duplicate golfer_id rows in predictions")
    p = pred.copy()
    for c in ["head", "shaft", "grip", "purchase", "handedness"]:
        p[c] = p[c].astype(str).str.strip().str.lower()
    p["length_in"] = p["length_in"].map(_num)
    p["lie_deg"] = p["lie_deg"].map(_num)
    return p


def buildable(p: pd.DataFrame) -> pd.Series:
    data = PR.load()
    def ok(r):
        sh = data["_shafts"].get(r["shaft"])
        return bool(sh and r["head"] in sh["heads"] and r["length_in"] in data["length_options_in"]
                    and r["lie_deg"] in data["lie_options_deg"] and r["grip"] in PR.GRIP_ORDER
                    and r["handedness"] in ("right", "left") and r["purchase"] in ("stock", "custom"))
    return p.apply(ok, axis=1) if len(p) else pd.Series(dtype=bool)


def _shaft_attr(slug, attr):
    try:
        return PR.shaft(slug)[attr]
    except KeyError:
        return None


def score_frame(pred: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    pred = clean(pred)
    p = labels[["golfer_id"]].merge(pred, on="golfer_id", how="left")
    L = labels.set_index("golfer_id").loc[p["golfer_id"]].reset_index()
    out = pd.DataFrame({"golfer_id": p["golfer_id"]})
    present = p["head"].notna() & p["head"].ne("nan")
    build = pd.Series(False, index=p.index)
    if present.any():
        build[present] = buildable(p[present])
    out["buildable"] = build.astype(float)

    head_ok = p["head"].eq(L["ref_head"])
    head_alt = p["head"].eq(L["ref_head_2"])
    out["head"] = np.where(head_ok, 1.0, np.where(head_alt, np.where(L["close_call_head"], 1.0, 0.5), 0.0))

    attr = lambda s, a: _shaft_attr(s, a)
    pf = p["shaft"].map(lambda s: attr(s, "flex")).map(lambda f: PR.FLEX_ORDER.index(f) if f in PR.FLEX_ORDER else np.nan)
    rf = L["ref_flex"].map(PR.FLEX_ORDER.index)
    exact_flex = (pf - rf).abs() == 0
    alt = L["ref_shaft_alt"] if "ref_shaft_alt" in L else pd.Series([None] * len(L))
    alt_flex = alt.map(lambda s: attr(s, "flex"))
    alt_ok = L["close_call_flex"].astype(bool) & p["shaft"].map(lambda s: attr(s, "flex")).eq(alt_flex) & (alt_flex != L["ref_flex"])
    out["flex"] = np.where(exact_flex | alt_ok, 1.0, 0.0)
    pm, pmod = p["shaft"].map(lambda s: attr(s, "material")), p["shaft"].map(lambda s: attr(s, "model"))
    out["material"] = (pm.eq(L["ref_material"]) & ~alt_ok | alt_ok & pm.eq(alt.map(lambda s: attr(s, "material")))).astype(float)
    out["shaft_model"] = (pmod.eq(L["ref_shaft_model"]) & ~alt_ok | alt_ok & pmod.eq(alt.map(lambda s: attr(s, "model")))).astype(float)

    dl = (p["length_in"] - L["ref_length_in"]).abs()
    out["length"] = np.where(dl < 1e-6, 1.0, np.where(((dl - 0.5).abs() < 1e-6) & L["close_call_length"], 1.0, 0.0))
    dli = (p["lie_deg"] - L["ref_lie_deg"]).abs()
    out["lie"] = np.where(dli < 1e-6, 1.0, np.where(((dli - 1).abs() < 1e-6) & L["close_call_lie"], 1.0, 0.0))
    pg = p["grip"].map(lambda x: PR.GRIP_ORDER.index(x) if x in PR.GRIP_ORDER else np.nan)
    rg = L["ref_grip"].map(PR.GRIP_ORDER.index)
    dg = (pg - rg).abs()
    out["grip"] = np.where(dg == 0, 1.0, np.where((dg == 1) & L["close_call_grip"], 1.0, 0.0))
    # stock/custom: the reference answer, or the correct consequence of an accepted close-call spec
    from .baseline import purchase_for
    def implied(r):
        try:
            return purchase_for(r["head"], PR.shaft(r["shaft"]), r["length_in"], int(r["lie_deg"]), r["grip"])
        except Exception:
            return None
    imp = p.apply(implied, axis=1) if len(p) else pd.Series(dtype=object)
    others_ok = (out[["flex", "material", "shaft_model", "length", "lie", "grip"]] == 1).all(axis=1)
    # the answer must also be the consequence of the predicted spec (an oversize grip cannot be "stock")
    consistent = p["purchase"].eq(imp)
    out["purchase"] = (consistent & (p["purchase"].eq(L["ref_purchase"]) | others_ok)).astype(float)
    out["handedness"] = p["handedness"].eq(L["ref_handedness"]).astype(float)
    for k in WEIGHTS:
        out[k] = out[k] * out["buildable"]
    out["score"] = sum(out[k] * w for k, w in WEIGHTS.items())
    full = (out[list(WEIGHTS)] == 1).all(axis=1) & (out["head"] == 1)
    out["full_spec_exact"] = full.astype(float)
    return out


def measurement_mae(pred, labels):
    m = labels.merge(pred, on="golfer_id", how="inner")
    res = {}
    pairs = {"est_7i_speed_mph": "true_7i_speed_mph", "est_height_cm": "true_height_cm",
             "est_wrist_to_floor_cm": "true_wrist_to_floor_cm", "est_hand_length_cm": "true_hand_length_cm",
             "est_lie_need_deg": "true_lie_need_deg"}
    for h in PR.HEAD_ORDER:
        pairs[f"est_carry_yd_{h}"] = f"exp_carry_yd_{h}"
    for e, t in pairs.items():
        if e in m and t in m:
            v = (pd.to_numeric(m[e], errors="coerce") - m[t]).abs().dropna()
            if len(v):
                res[e] = {"mae": round(float(v.mean()), 3), "n": int(len(v))}
    return res


def summary(pred, labels):
    s = score_frame(pred, labels)
    comp = {k: round(float(s[k].mean()) * 100, 1) for k in WEIGHTS}
    return {"spec_agreement_score": round(float(s["score"].mean()), 2),
            "full_spec_exact_pct": round(float(s["full_spec_exact"].mean()) * 100, 1), "n_golfers": int(len(s)),
            "n_unbuildable_or_missing": int((s["buildable"] == 0).sum()),
            "n_predicted": int(pred["golfer_id"].isin(labels["golfer_id"]).sum()),
            "component_pct": comp, "measurement_mae": measurement_mae(pred, labels)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--per-golfer", help="optional CSV path for per-golfer scores")
    a = ap.parse_args(argv)
    pred = pd.read_csv(a.pred)
    labels = pd.read_csv(a.labels)
    try:
        res = summary(pred, labels)
    except ValueError as e:
        sys.exit(f"error: {e}")
    print(json.dumps(res, indent=2))
    if a.per_golfer:
        score_frame(pred, labels).to_csv(a.per_golfer, index=False)


if __name__ == "__main__":
    main()
