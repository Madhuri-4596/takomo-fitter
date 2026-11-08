"""Thin wrapper around the Takomo Fitting Lab engine (kit/takomo_fit).

All fitting decisions come from takomo_fit.baseline.recommend(). This module only
turns the golfer's form input into baseline.Answers and adds plain-language
explanations, close-call flags and a before/after comparison on top.
"""
from __future__ import annotations

import csv
import dataclasses
import math
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent / "kit"
sys.path.insert(0, str(KIT))

from takomo_fit import baseline as B  # noqa: E402
from takomo_fit import products as P  # noqa: E402

# Measurement error assumptions from kit/docs/DATA_DICTIONARY.md and SIMULATOR.md
SELF_WTF_ERROR_CM = 3.25      # self-measured wrist-to-floor: 1 SD of answer minus truth on the kit's training data
PHOTO_WTF_ERROR_CM = 2.0      # photo with the sheet, good quality
CM_PER_LIE_DEGREE = 1 / B.LIE_PLANE[2]   # about 4.6 cm of wrist-to-floor per degree of lie

FLEX_LABEL = {"lady": "Ladies", "senior": "Senior", "regular": "Regular", "stiff": "Stiff", "x-stiff": "X-Stiff"}
FLIGHT_WORD = {"low": "a lower", "mid": "a medium", "high": "a higher"}
TEMPO_PROFILE_WORD = {"slow": "smooth", "mid": "moderate", "fast": "quick"}
HEAD_BLURB = {
    "101-mkii": "the most forgiving and longest head, with the most offset to help square the face",
    "201-mkii": "a forgiving distance iron with a little more control",
    "201t-mkii": "a balance of forgiveness, distance control and workability",
    "301-cb": "a players' cavity back with precise distance control and a forged feel",
    "301-mb": "a forged muscle back for skilled ball-strikers who want control and feel",
}


class InputError(ValueError):
    pass


# ------------------------------------------------------------------ input
def answers_from_form(f: dict) -> B.Answers:
    """Validate the questionnaire JSON and build the engine's Answers."""
    def num(key, lo, hi):
        try:
            v = float(f[key])
        except (KeyError, TypeError, ValueError):
            raise InputError(f"{key} is missing or not a number")
        if not (lo <= v <= hi) or math.isnan(v):
            raise InputError(f"{key} must be between {lo} and {hi}")
        return v

    def choice(key, options):
        v = f.get(key)
        if v not in options:
            raise InputError(f"{key} must be one of {', '.join(map(str, options))}")
        return v

    if f.get("speed_or_carry") == "speed":
        swing = B.swing_bucket_from_speed(num("speed_mph", 30, 130))
    elif f.get("speed_or_carry") == "carry":
        swing = B.swing_bucket_from_carry(num("carry_yd", 40, 250))
    else:
        raise InputError("speed_or_carry must be 'speed' or 'carry'")
    chars = f.get("characteristics") or []
    if not isinstance(chars, list) or len(chars) > 3 or any(c not in B.CHARACTERISTICS for c in chars):
        raise InputError("choose up to three priorities from the list")
    return B.Answers(
        age=int(num("age", 8, 100)),
        sex=choice("sex", ["male", "female"]),
        score_bucket=int(choice("score_bucket", [1, 2, 3, 4, 5])),
        swing_bucket=swing,
        characteristics=chars,
        flight=choice("flight", ["low", "mid", "high"]),
        tempo=choice("tempo", ["smooth", "moderate", "quick"]),
        height_cm=num("height_cm", 120, 220),
        wrist_to_floor_cm=num("wrist_to_floor_cm", 55, 120),
        glove=choice("glove", P.GLOVE_ORDER),
        handedness=choice("handedness", ["right", "left"]),
        rounds_per_year=f.get("rounds_per_year"),
    )


# ------------------------------------------------------------------ explanations
def _length_band(height_cm: float) -> tuple[float | None, float | None]:
    """Edges of the length band the height falls in (cm)."""
    rows = P.load()["current_tool_rules"]["length_by_height_cm"]
    lo = None
    for r in rows:
        if r["below_cm"] is None or height_cm < r["below_cm"]:
            return lo, r["below_cm"]
        lo = r["below_cm"]
    return lo, None


def lie_detail(height_cm: float, wtf_cm: float) -> dict:
    raw = B.lie_continuous(height_cm, wtf_cm)
    lie = int(max(-3, min(3, round(raw))))
    # distance to the nearest rounding edge, expressed in wrist-to-floor cm
    if abs(raw) >= 3.5:
        edge_cm = None
    else:
        frac = raw - math.floor(raw)
        edge_cm = round(abs(0.5 - frac) * CM_PER_LIE_DEGREE, 1)
    return {"raw": round(raw, 2), "lie": lie, "edge_cm": edge_cm}


def _signed(v: float, unit: str) -> str:
    if v == 0:
        return "standard"
    return f"{v:+g}{unit}"


def custom_reasons(head_slug: str, rec: B.Recommendation) -> list[str]:
    """Which parts of the spec force a custom build (mirrors baseline.purchase_for)."""
    out = []
    stock_models = P.head(head_slug)["stock_shafts"]
    if rec.material != "steel":
        out.append(f"graphite shaft ({rec.shaft_model}); stock sets are steel")
    elif rec.shaft_model not in stock_models:
        out.append(f"{rec.shaft_model} is not a stock shaft on this head ({' or '.join(stock_models)})")
    if rec.length_in != 0:
        out.append(f"length {rec.length_in:+g} in (stock is standard length)")
    if rec.lie_deg != 0:
        out.append(f"lie {rec.lie_deg:+d}° (stock is standard lie)")
    if rec.grip not in B.STOCK_GRIPS:
        out.append(f"{rec.grip} grip (stock comes with standard or midsize)")
    return out


def explain(a: B.Answers, rec: B.Recommendation, runner_up: B.Recommendation | None) -> dict:
    h = P.head(rec.head)
    sh = P.shaft(rec.shaft)
    parts = rec.score_parts
    score_label = B.SCORE_LABELS[a.score_bucket]
    speed_row = P.load()["current_tool_rules"]["swing_buckets"][a.swing_bucket - 1]
    flex_target = B.flex_for(a)
    lie = lie_detail(a.height_cm, a.wrist_to_floor_cm)
    lo, hi = _length_band(a.height_cm)
    edges = [e for e in (lo, hi) if e is not None]
    length_edge_cm = round(min(abs(a.height_cm - e) for e in edges), 1) if edges else None

    head_why = (f"{h['name']} is {HEAD_BLURB[rec.head]}. It scored {rec.head_score:g} points: "
                f"{parts['skill']} for matching your typical score ({score_label}), "
                f"{parts['speed']} for your swing speed, {parts['characteristics']:g} for your priorities"
                + (f" and {parts['forgiveness_penalty']} because you asked for forgiveness" if parts["forgiveness_penalty"] else "")
                + ".")
    head_close = None
    if runner_up is not None:
        gap = rec.head_score - runner_up.head_score
        head_close = {"runner_up": P.head(runner_up.head)["name"], "gap": gap}
        head_why += (f" {P.head(runner_up.head)['name']} came second with {runner_up.head_score:g}"
                     + (": a close call, both would suit you." if gap <= 1 else "."))

    mat_reason = ("ladies and senior flexes are built in graphite" if flex_target in ("lady", "senior") else
                  "graphite is lighter, which the fitting rules give every woman golfer" if a.sex == "female" else
                  "graphite is lighter and easier on the joints from age 70" if a.age >= 70 else
                  "steel gives the most consistent feel at your speed")
    shaft_why = (f"Your 7-iron answer puts you in swing group {a.swing_bucket} ({speed_row['seven_iron_speed_mph']} mph or "
                 f"{speed_row['seven_iron_carry_yd']} yd carry), which calls for {FLEX_LABEL[flex_target].lower()} flex. "
                 f"It is {sh['material']} because {mat_reason}. "
                 f"{sh['model']} ({sh['weight_g']} g) is designed for {' and '.join(sh['flight_profile'])} flight")
    if sh.get("tempo_profile"):
        shaft_why += " and " + " or ".join(TEMPO_PROFILE_WORD[t] for t in sh["tempo_profile"]) + " tempos"
    shaft_why += f"; you asked for {FLIGHT_WORD[a.flight]} flight with a {a.tempo} tempo."
    if sh["flex"] != flex_target:
        shaft_why += f" No {flex_target} flex is offered in this shaft family, so the nearest flex is used."

    length_why = (f"Length follows your height on the PING colour code chart: {a.height_cm:.1f} cm gives "
                  f"{_signed(rec.length_in, ' in')}" + (" (ladies-flex shafts get one inch added back)" if sh["flex"] == "lady" else "")
                  + ". Longer clubs suit taller golfers so they don't hunch over the ball.")
    lie_dir = ("more upright: the toe sits higher so the sole meets the ground flat when your hands are higher at address"
               if rec.lie_deg > 0 else
               "flatter: the toe sits lower to match hands that are closer to the ground" if rec.lie_deg < 0 else
               "standard: the sole sits flat at address with your proportions")
    lie_why = (f"Lie comes from your height and wrist-to-floor together. With {a.height_cm:.1f} cm and "
               f"{a.wrist_to_floor_cm:.1f} cm the chart reads {lie['raw']:+.2f}°, rounded to "
               f"{_signed(rec.lie_deg, '°')}. That is {lie_dir}. A lie that is wrong by 1° sends a 7-iron "
               f"roughly 3–4 yards off line.")
    grip_why = (f"Glove size {a.glove} maps to a {rec.grip} grip "
                f"(XS–M standard, M/L–L midsize, XL oversize). A grip that fits lets your hands release the club "
                f"without squeezing. {'Midsize is still available on a stock set.' if rec.grip == 'midsize' else ''}").strip()
    reasons = custom_reasons(rec.head, rec)
    if rec.purchase == "stock":
        buy_why = ("Everything in this spec is available off the shelf: steel stock shaft, standard length and lie, "
                   f"{rec.grip} grip. You get faster delivery and a 30-day return.")
    else:
        buy_why = ("This needs a custom build because of: " + "; ".join(reasons)
                   + ". Custom builds are made for you and cannot be returned, so it pays to be sure of the measurements.")

    return {
        "head": head_why, "shaft": shaft_why, "length": length_why, "lie": lie_why, "grip": grip_why,
        "handedness": f"{rec.handedness.capitalize()}-handed, as you told us.",
        "purchase": buy_why, "custom_reasons": reasons,
        "lie_detail": lie, "length_edge_cm": length_edge_cm, "head_close": head_close,
    }


def rec_to_json(a: B.Answers, rec: B.Recommendation, runner_up: B.Recommendation | None) -> dict:
    h = P.head(rec.head)
    sh = P.shaft(rec.shaft)
    return {
        "rank": rec.rank,
        "head": rec.head, "head_name": h["name"], "head_score": rec.head_score, "score_parts": rec.score_parts,
        "shaft": rec.shaft, "shaft_model": rec.shaft_model, "flex": rec.flex, "flex_label": FLEX_LABEL[rec.flex],
        "material": rec.material, "shaft_weight_g": sh["weight_g"],
        "length_in": rec.length_in, "lie_deg": rec.lie_deg, "grip": rec.grip,
        "handedness": rec.handedness, "purchase": rec.purchase,
        "trace": rec.trace,
        "why": explain(a, rec, runner_up),
    }


def _with_lie(rec: B.Recommendation, lie: int, note: str) -> B.Recommendation:
    """The kit's spec with the lie taken from shot evidence; the build (stock/custom) follows the kit's own rule."""
    buy = B.purchase_for(rec.head, P.shaft(rec.shaft), rec.length_in, lie, rec.grip)
    return dataclasses.replace(rec, lie_deg=lie, purchase=buy,
                               trace=[*rec.trace[:-1], note, f"purchase: {buy}"])


def recommend(a: B.Answers, lie_fn=None) -> dict:
    """lie_fn(height_cm, wrist_to_floor_cm) -> ball-line estimate dict or None: lie from shot evidence."""
    recs = B.recommend(a, top_n=2)
    shot = lie_fn(a.height_cm, a.wrist_to_floor_cm) if lie_fn else None
    if shot:
        note = (f"lie: ball-line test, {shot['shots']} shots, mean tilt {shot['mean_tilt_deg']:+.1f} deg -> need "
                f"{shot['need_deg']:+.2f} -> {shot['lie_deg']:+d} (static chart alone {shot['static_raw']:+.2f})")
        recs = [_with_lie(r, shot["lie_deg"], note) for r in recs]
    best, second = recs[0], recs[1] if len(recs) > 1 else None
    out = {
        "inputs": {"height_cm": a.height_cm, "wrist_to_floor_cm": a.wrist_to_floor_cm,
                   "swing_bucket": a.swing_bucket, "score_bucket": a.score_bucket},
        "best": rec_to_json(a, best, second),
        "runner_up": rec_to_json(a, second, None) if second else None,
        **({"ball_line": shot} if shot else {}),
    }
    if shot:
        for r in (out["best"], out["runner_up"]):
            if r:
                r["why"]["lie"] = f"{shot['text']} The static chart alone would say {r['why']['lie_detail']['lie']:+d}."
                r["why"]["lie_source"] = "ball_line"
    return out


# ------------------------------------------------------------------ before / after
SPEC_FIELDS = [("head_name", "Head"), ("shaft_model", "Shaft"), ("flex_label", "Flex"), ("length_in", "Length"),
               ("lie_deg", "Lie"), ("grip", "Grip"), ("handedness", "Hand"), ("purchase", "Build")]


METHOD_LABEL = {"printed_sheet": "the printed sheet", "plain_paper": "the plain A4 paper", "entered_height": "your entered height",
                "tape": "your tape measure", "simulated": "the simulated photo"}
TAPE_CHECK_TEXT = ("Estimated using {method} and the camera image. This result is close to a fitting boundary. "
                   "Please check with a tape before ordering custom.")


def boundary_check(a: B.Answers, best: dict, error_cm: float) -> dict:
    """Could the measurement's assumed error move the lie into the next band, and would that flip stock/custom?"""
    lie = lie_detail(a.height_cm, a.wrist_to_floor_cm)
    if lie["edge_cm"] is None or lie["edge_cm"] >= error_cm:
        return {"close": False, "edge_cm": lie["edge_cm"], "alt_lie": None, "could_flip_purchase": False}
    frac = lie["raw"] - math.floor(lie["raw"])
    step = 1 if frac >= 0.5 or (lie["raw"] > lie["lie"]) else -1        # towards the nearer rounding edge
    alt = int(max(-3, min(3, lie["lie"] + step)))
    alt_purchase = B.purchase_for(best["head"], P.shaft(best["shaft"]), best["length_in"], alt, best["grip"])
    return {"close": True, "edge_cm": lie["edge_cm"], "alt_lie": alt, "alt_purchase": alt_purchase,
            "could_flip_purchase": alt_purchase != best["purchase"]}


def compare(form: dict, measured: dict, error_cm: float = PHOTO_WTF_ERROR_CM, method: str = "printed_sheet",
            lie_fn_self=None, lie_fn_measured=None) -> dict:
    a_self = answers_from_form(form)
    m = dict(form)
    for key in ("height_cm", "wrist_to_floor_cm"):
        if measured.get(key) is not None:
            m[key] = measured[key]
    a_photo = answers_from_form(m)
    before, after = recommend(a_self, lie_fn_self), recommend(a_photo, lie_fn_measured)
    b, f = before["best"], after["best"]
    changes = [{"field": label, "before": b[k], "after": f[k]} for k, label in SPEC_FIELDS if b[k] != f[k]]

    d_wtf = a_photo.wrist_to_floor_cm - a_self.wrist_to_floor_cm
    d_h = a_photo.height_cm - a_self.height_cm
    lie_b, lie_a = b["why"]["lie_detail"], f["why"]["lie_detail"]
    story = [f"Your photo measured a wrist-to-floor of {a_photo.wrist_to_floor_cm:.1f} cm, "
             f"{abs(d_wtf):.1f} cm {'longer' if d_wtf > 0 else 'shorter'} than the {a_self.wrist_to_floor_cm:.1f} cm you entered."
             if abs(d_wtf) >= 0.05 else
             f"Your photo confirmed your wrist-to-floor of {a_photo.wrist_to_floor_cm:.1f} cm."]
    if abs(d_h) >= 0.05:
        story.append(f"Height came out at {a_photo.height_cm:.1f} cm ({d_h:+.1f} cm).")
    story.append(f"On the lie chart, about {CM_PER_LIE_DEGREE:.1f} cm of wrist-to-floor equals one degree of lie. "
                 f"The reading moved from {lie_b['raw']:+.2f}° to {lie_a['raw']:+.2f}°"
                 + (f", so the lie changes from {_signed(b['lie_deg'], '°')} to {_signed(f['lie_deg'], '°')}."
                    if b["lie_deg"] != f["lie_deg"] else f", which still rounds to {_signed(f['lie_deg'], '°')}."))
    if b["length_in"] != f["length_in"]:
        story.append(f"The new height also moves the length from {_signed(b['length_in'], ' in')} to {_signed(f['length_in'], ' in')}.")
    if b["purchase"] != f["purchase"]:
        story.append("That turns a custom build into a stock set: faster delivery and a 30-day return."
                     if f["purchase"] == "stock" else
                     "That turns a stock set into a custom build. It can't be returned, but it is built to your measurements.")
    elif not changes:
        story.append("Nothing in your spec changes, so you can order with more confidence.")

    edge = lie_a["edge_cm"]
    chk = boundary_check(a_photo, f, error_cm)
    label = METHOD_LABEL.get(method, "the photo")
    if after.get("ball_line"):                   # the lie comes from shot evidence, not the chart's band edges
        bl = after["ball_line"]
        chk = {"close": bl["edge_deg"] < 0.2, "edge_cm": None, "alt_lie": None, "could_flip_purchase": False}
        confidence = {"level": "close call" if chk["close"] else "confident", "tape_check": False,
                      "text": f"Your lie comes from your ball-line shots and your measurements. "
                              + ("It sits close to the line between two lie settings: a few more ball-line shots would settle it."
                                 if chk["close"] else "It isn't close to the line between two lie settings.")}
    elif chk["close"] and chk["could_flip_purchase"]:
        confidence = {"level": "check needed", "tape_check": True, "text": TAPE_CHECK_TEXT.format(method=label),
                      "detail": f"Your lie sits {edge} cm of wrist-to-floor from the next band, inside the ±{error_cm:g} cm we "
                                f"assume for {label}. One band over would make this a {chk['alt_purchase']} build."}
    elif chk["close"]:
        confidence = {"level": "close call", "tape_check": False,
                      "text": f"Your lie sits {edge} cm of wrist-to-floor from the next band, inside the ±{error_cm:g} cm we "
                              f"assume for {label}. It wouldn't change stock or custom; a tape check or a five-shot "
                              f"ball-line test would confirm the lie."}
    else:
        confidence = {"level": "confident", "tape_check": False,
                      "text": (f"Your lie is {edge} cm of wrist-to-floor away from the next band, more than the ±{error_cm:g} cm "
                               f"we assume for {label}." if edge is not None else
                               "Your lie is at the end of the chart, so small measuring errors don't change it.")}
    return {"before": before, "after": after, "changes": changes, "story": story, "confidence": confidence,
            "boundary": chk, "method": method,
            "accuracy": {"self_cm": SELF_WTF_ERROR_CM, "measurement_cm": error_cm, "assumed": True}}


# ------------------------------------------------------------------ demo golfers
def _read(name: str) -> dict[str, dict]:
    with open(KIT / "data" / "demo" / name, encoding="utf-8") as fh:
        return {r["golfer_id"]: r for r in csv.DictReader(fh)}


def demo_golfers() -> list[dict]:
    """The kit's 16 synthetic demo golfers as pre-filled questionnaire answers."""
    golfers, q, ph = _read("golfers.csv"), _read("questionnaire.csv"), _read("phone_measurements.csv")
    out = []
    for gid, g in golfers.items():
        r, m = q[gid], ph[gid]
        photo = None
        if m["body_photo"] == "True" and m["wrist_to_floor_photo_cm"]:
            photo = {"height_cm": float(m["height_photo_cm"]), "wrist_to_floor_cm": float(m["wrist_to_floor_photo_cm"]),
                     "quality": m["body_photo_quality"]}
        out.append({
            "id": gid, "persona": g["persona"], "story": g["story"],
            "answers": {
                "age": int(r["age"]), "sex": r["sex"], "handedness": r["handedness"],
                "rounds_per_year": r["rounds_per_year"], "score_bucket": int(r["score_bucket"]),
                "speed_or_carry": r["speed_or_carry"],
                "speed_mph": float(r["reported_7i_speed_mph"]) if r["reported_7i_speed_mph"] else None,
                "carry_yd": float(r["reported_7i_carry_yd"]) if r["reported_7i_carry_yd"] else None,
                "characteristics": [c for c in r["characteristics"].split("|") if c],
                "flight": r["flight"], "tempo": r["tempo"],
                "height_cm": float(r["height_cm"]), "wrist_to_floor_cm": float(r["wrist_to_floor_cm"]),
                "glove": r["glove"],
            },
            "simulated_photo": photo,
        })
    return out
