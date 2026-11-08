"""Evidence behind each fitting decision.

The kit's engine (takomo_fit.baseline) still makes every decision. This module only
describes the evidence each decision rests on, how strong it is, and what would
improve it; describes the golfer's game in plain words instead of a skill label;
compares the recommended head with the runner-up; and analyses an optional
strike-pattern photo (impact tape or foot-powder spray on the clubface).

Strike results are used to explain the fit and to suggest a fitter review. They do
NOT change the kit's head-selection rules: that would need validation first.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import calibration, fitting

P = fitting.P

# Assumption to confirm with Takomo: length of a 7-iron's scoring lines (heel end to toe end), mm.
SCORING_LINE_MM = 60.0
# Prototype thresholds from the kit's SIMULATED data (dev split): the across-face SD of strike location is
# ~4 mm for scores under 71, ~6 mm at 71-77, ~8 mm at 78-84, ~10 mm at 85-92, ~14 mm over 92.
# Not validated on real golfers.
# thirds of the per-golfer impact-location spread on the kit's TrackMan shots (app/calibration.json)
STRIKE_SD_CLUSTERED_MM, STRIKE_SD_SPREAD_MM = calibration.strike_thresholds()
STRIKE_BIAS_MM = 5.0
STRIKE_MIN_MARKS = 3

SCORE_REPRESENTATIVE = {"several": "over several recent rounds", "one": "from one round", "guess": "a rough guess"}
LOSE_SHOTS = {"irons": "with your irons", "driving": "off the tee", "short_game": "around the green",
              "putting": "on the greens", "not_sure": None}
RATING_NAMES = {"forgiveness": "help on off-centre hits", "distance": "distance", "distance_control": "distance control",
                "workability": "shot-shaping", "feel": "forged feel"}


# ------------------------------------------------------------------ strike pattern
class StrikeError(ValueError):
    pass


def _face_frame(heel, toe):
    heel, toe = np.asarray(heel, float), np.asarray(toe, float)
    axis = toe - heel
    length = float(np.linalg.norm(axis))
    if length < 40:
        raise StrikeError("The heel and toe points are too close. Tap each end of the grooves.")
    u = axis / length
    n = np.array([-u[1], u[0]])
    return heel, u, n, length


def detect_marks(img: np.ndarray, heel, toe, max_marks: int = 12) -> list[list[float]]:
    """Suggest strike marks: compact dark spots on the face between heel and toe.

    Impact tape and foot-powder spray both leave a mark darker than its surroundings. Grooves
    are long thin lines and are filtered out by shape. These are suggestions for the golfer to
    confirm, remove or add to.
    """
    heel, u, n, length = _face_frame(heel, toe)
    mid = heel + u * length / 2
    w, h = int(length * 1.15), int(length * 0.7)
    angle = math.degrees(math.atan2(u[1], u[0]))
    M = cv2.getRotationMatrix2D((float(mid[0]), float(mid[1])), angle, 1.0)
    M[:, 2] += (w / 2 - mid[0], h / 2 - mid[1])
    band = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    k = max(3, int(length / 5) | 1)
    background = cv2.medianBlur(gray, k)
    diff = cv2.subtract(background, gray)                    # darker than the local background
    diff = cv2.GaussianBlur(diff, (3, 3), 0)
    thr = max(18, int(np.percentile(diff, 99) * 0.5))
    mask = (diff > thr).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, cents = cv2.connectedComponentsWithStats(mask)
    a_min, a_max = (length * 0.035) ** 2, (length * 0.3) ** 2
    found = []
    for i in range(1, count):
        x, y, bw, bh, area = stats[i]
        if not a_min <= area <= a_max:
            continue
        if max(bw, bh) / max(1, min(bw, bh)) > 2.5:          # grooves and edges are long and thin
            continue
        found.append((float(diff[labels == i].mean()) * area, cents[i]))
    found.sort(key=lambda t: -t[0])
    Minv = cv2.invertAffineTransform(M)
    pts = [(Minv @ np.array([c[0], c[1], 1.0])).tolist() for _, c in found[:max_marks]]
    return [[round(x, 1), round(y, 1)] for x, y in pts]


def analyse_marks(marks, heel, toe, scoring_line_mm: float = SCORING_LINE_MM) -> dict:
    heel, u, n, length = _face_frame(heel, toe)
    if len(marks) < STRIKE_MIN_MARKS:
        raise StrikeError(f"Mark at least {STRIKE_MIN_MARKS} strikes (five is better).")
    mm_per_px = scoring_line_mm / length
    centre = heel + u * length / 2
    pts = np.asarray(marks, float)
    along = (pts - centre) @ u * mm_per_px                     # + = toward the toe (as the kit's spray test)
    across = (pts - centre) @ n * mm_per_px
    if np.any(np.abs(along) > scoring_line_mm * 0.75):
        raise StrikeError("A mark is well outside the grooves. Check the heel and toe points and the marks.")
    sd = float(np.std(along, ddof=1)) if len(along) > 1 else 0.0
    mean = float(np.mean(along))
    pattern = "clustered" if sd < STRIKE_SD_CLUSTERED_MM else "spread" if sd > STRIKE_SD_SPREAD_MM else "moderate"
    bias = "toe" if mean > STRIKE_BIAS_MM else "heel" if mean < -STRIKE_BIAS_MM else "centre"
    words = {"clustered": "close together", "moderate": "moderately spread", "spread": "spread widely"}[pattern]
    text = f"Your {len(marks)} marks are {words} across the face (about {sd:.0f} mm from shot to shot)"
    text += "." if bias == "centre" else f", mostly toward the {bias} (about {abs(mean):.0f} mm off centre)."
    notes = []
    if bias == "toe":
        notes.append("Toe strikes can mean the club is too short or you stand too far away; we don't change length from this alone.")
    elif bias == "heel":
        notes.append("Heel strikes can mean the club is too long or you stand too close; we don't change length from this alone.")
    return {"marks": len(marks), "offsets_mm": [round(x, 1) for x in along.tolist()],
            "height_offsets_mm": [round(x, 1) for x in across.tolist()], "sd_mm": round(sd, 1), "mean_mm": round(mean, 1),
            "pattern": pattern, "bias": bias, "text": text, "notes": notes,
            "assumption": f"Millimetres assume {scoring_line_mm:g} mm scoring lines; thresholds come from the kit's simulated data."}


# ------------------------------------------------------------------ ball flight, in words
def flight_description(p: dict) -> str | None:
    start, curve, height = p.get("ball_start"), p.get("ball_curve"), p.get("ball_height")
    if not (start or curve or height):
        return None
    right_handed = p.get("handedness", "right") != "left"
    parts = []
    if start:
        parts.append({"straight": "starts on target"}.get(start, f"starts {start}"))
    if curve:
        if curve == "none":
            parts.append("flies fairly straight after that")
        else:
            # away from a right-hander's body is to the right; mirrored for left-handers
            fade_side = "right" if right_handed else "left"
            shape = "fades or slices" if curve == fade_side else "draws or hooks"
            parts.append(f"curves {curve} ({shape} for a {'right' if right_handed else 'left'}-handed golfer)")
    if height and height != "as_wanted":
        parts.append(f"flies {height} than you want")
    return "The ball usually " + ", ".join(parts) + "."


def player_summary(p: dict, strike: dict | None) -> str | None:
    """Describe the golfer's game from their answers, without a beginner/intermediate label."""
    bits = []
    if p.get("score_bucket"):
        s = f"You usually score {fitting.B.SCORE_LABELS[p['score_bucket']].replace('-', '–').lower()}"
        if p.get("score_representative") in SCORE_REPRESENTATIVE:
            s += f" ({SCORE_REPRESENTATIVE[p['score_representative']]})"
        bits.append(s)
    lose = LOSE_SHOTS.get(p.get("lose_shots"))
    if lose:
        bits.append(f"you lose most shots {lose}")
    if strike:
        bits.append({"clustered": "your strike test shows consistent contact", "moderate": "your strike test shows fairly consistent contact",
                     "spread": "your strike test shows inconsistent iron contact"}[strike["pattern"]])
    elif p.get("lose_shots") == "irons":
        bits.append("you report inconsistent iron contact")
    if "forgiveness" in (p.get("characteristics") or []):
        bits.append("you want more help on mishits")
    if not bits:
        return None
    text = bits[0] + ("" if len(bits) == 1 else ", " + ", ".join(bits[1:-1]) + (", and " if len(bits) > 2 else " and ") + bits[-1]) + "."
    return text.replace(", , ", ", ").replace(",  and", " and")


# ------------------------------------------------------------------ head comparison
def head_comparison(result: dict, profile: dict, strike: dict | None) -> dict | None:
    best, alt = result.get("best"), result.get("runner_up")
    if not best or not alt:
        return None
    hb, ha = P.head(best["head"]), P.head(alt["head"])
    rb, ra = hb["ratings"], ha["ratings"]
    pb, pa = best["score_parts"], alt["score_parts"]
    plus_b = [k for k in RATING_NAMES if rb[k] > ra[k]]
    plus_a = [k for k in RATING_NAMES if ra[k] > rb[k]]
    lst = lambda ks: " and ".join(RATING_NAMES[k] for k in ks[:2])
    why = []
    label = fitting.B.SCORE_LABELS[profile["score_bucket"]] if profile.get("score_bucket") else "your score"
    if pb["skill"] != pa["skill"]:
        better = hb if pb["skill"] > pa["skill"] else ha
        why.append(f"your usual score ({label}) fits the {better['name']}'s target group better")
    if pb["speed"] != pa["speed"]:
        better = hb if pb["speed"] > pa["speed"] else ha
        src = "club speed" if profile.get("speed_or_carry") == "speed" else "7-iron carry"
        why.append(f"your {src} earns the {better['name']} more speed points")
    if pb["characteristics"] != pa["characteristics"]:
        better = hb if pb["characteristics"] > pa["characteristics"] else ha
        why.append(f"your priorities ({', '.join(profile.get('characteristics') or [])}) score higher on the {better['name']}")
    gap = best["head_score"] - alt["head_score"]
    text = (f"{hb['name']} scored {best['head_score']:g} and {ha['name']} {alt['head_score']:g}"
            + (": a close call." if gap <= 1 else "."))
    tradeoff = []
    if plus_b:
        tradeoff.append(f"The {hb['name']} gives more {lst(plus_b)}.")
    if plus_a:
        tradeoff.append(f"The {ha['name']} offers more {lst(plus_a)}.")
    if strike is None:
        contact = "Your contact consistency is the part we still need to check: the optional strike test would show it."
    else:
        contact = strike["text"]
    review = None
    if strike and strike["pattern"] == "spread" and rb["forgiveness"] <= 2:
        review = ("Your strike marks are spread across the face, but the recommended head is a players' iron. "
                  "We suggest a quick review with a Takomo fitter before ordering.")
    elif strike and strike["pattern"] == "clustered" and best["head"] == "101-mkii":
        review = ("Your contact looks consistent; a Takomo fitter could confirm whether a less forgiving head "
                  "with more control would suit you.")
    return {"best": hb["name"], "alternative": ha["name"], "gap": gap, "summary": text, "inputs": why,
            "tradeoff": " ".join(tradeoff), "contact": contact, "fitter_review": review,
            "ratings": {hb["name"]: {k: rb[k] for k in RATING_NAMES}, ha["name"]: {k: ra[k] for k in RATING_NAMES}}}


# ------------------------------------------------------------------ evidence map
def evidence_map(profile: dict, measurement: dict | None, strike: dict | None, method_error: tuple | None,
                 result: dict | None, swing: dict | None = None) -> list[dict]:
    p = profile
    best = (result or {}).get("best")
    rows = []

    def row(decision, choice, items, improve):
        rows.append({"decision": decision, "choice": choice, "evidence": [i for i in items if i], "improve": [i for i in improve if i]})

    # head
    head_items = []
    if p.get("score_bucket"):
        rep = p.get("score_representative")
        head_items.append({"what": f"usual score {fitting.B.SCORE_LABELS[p['score_bucket']]}"
                                   + (f" ({SCORE_REPRESENTATIVE[rep]})" if rep in SCORE_REPRESENTATIVE else ""),
                           "strength": "confirmed answer" if rep == "several" else "self-reported"})
    head_items.append(_speed_item(p))
    if p.get("characteristics"):
        head_items.append({"what": "priorities: " + ", ".join(p["characteristics"]), "strength": "confirmed answer"})
    if strike:
        head_items.append({"what": f"strike test: {strike['pattern']} ({strike['sd_mm']:g} mm)", "strength": "measured (explains only)"})
    elif p.get("lose_shots"):
        head_items.append({"what": f"loses most shots {LOSE_SHOTS.get(p['lose_shots']) or '(not sure)'}", "strength": "self-reported"})
    row("Iron head", best and best["head_name"], head_items,
        ["Use your usual score over several recent rounds" if p.get("score_representative") in ("one", "guess") else None,
         None if strike else "Optional strike test: five shots with impact tape, one photo"])
    # flex
    speed = _speed_item(p)
    row("Shaft flex", best and best["flex_label"], [speed],
        ["Your 7-iron club speed from a launch monitor would be stronger than carry"
         if p.get("speed_or_carry") == "carry" else None])
    # shaft model
    if swing and p.get("tempo") == swing["tempo"]:
        tempo_item = {"what": f"tempo: {p['tempo']} (swing video: {swing['total_s']:.2f} s, ratio {swing['ratio']}:1)",
                      "strength": "measured"}
    elif swing:
        tempo_item = {"what": f"tempo answer {p.get('tempo') or 'not given'}, but your swing video suggests {swing['tempo']} "
                              f"({swing['total_s']:.2f} s)", "strength": "self-reported"}
    else:
        tempo_item = {"what": f"tempo: {p['tempo']}", "strength": "self-reported"} if p.get("tempo") else None
    row("Shaft model", best and best["shaft_model"],
        [tempo_item,
         {"what": f"wanted ball flight: {p['flight']}", "strength": "confirmed answer"} if p.get("flight") else None,
         {"what": "only shafts offered on this head", "strength": "product data"}],
        [None if swing and p.get("tempo") == swing["tempo"] else
         "Use your swing video's tempo" if swing else "A swing video can measure your tempo"])
    # length and lie
    m = measurement
    if m:
        err, method = method_error or (None, m["source"])
        src = {"printed_sheet": "photo with the printed sheet", "plain_paper": "photo with plain A4 paper",
               "entered_height": "photo scaled by your entered height", "tape": "tape measure", "simulated": "simulated photo"}.get(method, method)
        ll = [{"what": f"height {m['height_cm']} cm", "strength": "entered" if method == "entered_height" else "measured"},
              {"what": f"wrist-to-floor {m['wrist_to_floor_cm']} cm ({src}, about ±{err:g} cm)", "strength": "measured"}]
    else:
        ll = [{"what": f"height {p['height_cm']} cm", "strength": "self-reported"} if p.get("height_cm") else None,
              {"what": f"wrist-to-floor {p['wrist_to_floor_cm']} cm", "strength": "self-reported (often 3 cm off)"} if p.get("wrist_to_floor_cm") else None]
    bl = (result or {}).get("ball_line")
    if bl:
        ll = [x for x in ll if x] + [{"what": f"ball-line test: {bl['shots']} shots, marks leaning {bl['mean_tilt_deg']:+.1f} degrees "
                                             f"(lie {bl['lie_deg']:+d}, how the club sat at impact)", "strength": "measured: shot evidence"}]
    row("Length and lie", best and f"{_len(best['length_in'])}, lie {_lie(best['lie_deg'])}", ll,
        [None if m else "Measure wrist-to-floor with the photo step",
         "A fitter's lie check at a demo bay would confirm it"])
    # grip
    row("Grip", best and best["grip"],
        [{"what": f"glove size {p['glove']}", "strength": "self-reported"} if p.get("glove") else None],
        ["Check the glove size you actually wear; comfort matters too"])
    return rows


def _speed_item(p):
    if p.get("speed_or_carry") == "speed" and p.get("speed_mph"):
        src = {"simulator": "from a simulator", "launch_monitor": "from a launch monitor"}.get(p.get("speed_source"))
        if src:
            return {"what": f"7-iron club speed {p['speed_mph']:g} mph {src}", "strength": "measured: simulator data"}
        return {"what": f"7-iron club speed {p['speed_mph']:g} mph", "strength": "confirmed answer"}
    if p.get("carry_yd"):
        return {"what": f"7-iron carry {p['carry_yd']:g} yd", "strength": "fallback: carry varies with loft and brand"}
    return None


def _len(v):
    return "standard length" if v == 0 else f"{v:+g} in"


def _lie(v):
    return "standard" if v == 0 else f"{v:+d}°"


# ------------------------------------------------------------------ tempo -> shaft and ball-flight height
FLIGHT_RANK = {"low": 0, "mid": 1, "high": 2}
FLIGHT_WORDS = {"low": "low", "mid": "medium", "high": "high"}


def _flight_level(shaft: dict) -> float:
    return sum(FLIGHT_RANK[f] for f in shaft["flight_profile"]) / len(shaft["flight_profile"])


def tempo_effect(profile: dict, tempo: str) -> dict | None:
    """What the kit engine would pick with this tempo: shaft model, and what that means for ball-flight height.

    The kit picks the shaft model by the wanted flight first, then tempo, among shafts offered on the head.
    """
    try:
        new = fitting.recommend(fitting.answers_from_form({**profile, "tempo": tempo}))["best"]
    except (fitting.InputError, KeyError, TypeError):
        return None
    try:
        cur = fitting.recommend(fitting.answers_from_form(profile))["best"] if profile.get("tempo") else None
    except (fitting.InputError, KeyError, TypeError):
        cur = None
    sh = P.shaft(new["shaft"])
    words = lambda s: " to ".join(FLIGHT_WORDS[f] for f in s["flight_profile"])
    text = (f"With a {tempo} tempo the kit picks the {new['shaft_model']} ({new['flex_label']}, {sh['weight_g']} g), "
            f"made for {words(sh)} ball flight")
    height = None
    if cur and cur["shaft"] != new["shaft"]:
        old = P.shaft(cur["shaft"])
        text += f", instead of the {cur['shaft_model']} ({old['weight_g']} g, {words(old)} flight)"
        d = _flight_level(sh) - _flight_level(old)
        height = "higher" if d > 0 else "lower" if d < 0 else "similar"
        text += {"higher": ": expect a slightly higher ball flight.", "lower": ": expect a slightly lower ball flight.",
                 "similar": ": a similar ball flight."}[height]
    elif cur:
        text += ", the same shaft as now."
    else:
        text += "."
    wish = profile.get("ball_height")
    if wish == "lower" and _flight_level(sh) >= 1.5:
        text += " That suits your wish for a higher flight."
    elif wish == "higher" and _flight_level(sh) <= 0.5:
        text += " That suits your wish for a lower flight."
    elif wish in ("lower", "higher"):
        text += (f" Your flight is {wish} than you want; the shaft choice also follows your wanted flight "
                 f"({profile.get('flight', 'mid')}), so check that answer too.")
    return {"tempo": tempo, "shaft": new["shaft"], "shaft_model": new["shaft_model"], "flex": new["flex_label"],
            "weight_g": sh["weight_g"], "flight_profile": sh["flight_profile"],
            "current_shaft_model": cur["shaft_model"] if cur else None, "height_change": height,
            "changes_shaft": bool(cur and cur["shaft"] != new["shaft"]), "text": text}



# ------------------------------------------------------------------ verdict confidence (team board 4)
TEMPOS = ["smooth", "moderate", "quick"]
NEXT_STEP = {
    "Head": "hit both at a demo bay, or send me simulator numbers",
    "Shaft": "a swing-speed number from a simulator or launch monitor would settle it",
    "Shaft model": "film a swing so I can measure your tempo",
    "Length": "measure your height with a tape",
    "Lie": "a fitter at a demo bay can check your lie in a couple of minutes",
    "Grip": "check the glove size you actually buy",
}


def _spec(form: dict) -> dict | None:
    try:
        b = fitting.recommend(fitting.answers_from_form(form))["best"]
    except (fitting.InputError, KeyError, TypeError):
        return None
    return {"Head": b["head_name"], "Shaft": b["flex_label"], "Shaft model": b["shaft_model"],
            "Length": b["length_in"], "Lie": b["lie_deg"], "Grip": b["grip"]}


def _fmt(k, v):
    if k == "Length":
        return "standard length" if v == 0 else f"{v:+g} in"
    if k == "Lie":
        return "standard lie" if v == 0 else f"{v:+d} degree lie"
    if k == "Shaft":
        return f"{v} flex"
    return str(v)


GLOVES = ["XS", "S", "M", "M/L", "L", "XL"]
HIGH_RATE, MEDIUM_RATE = 0.85, 0.65            # share of training golfers whose line was right


def line_key(line: str, flip_inputs: list[str], form: dict, measurement: dict | None) -> str:
    """The situation a confidence level is measured for: the line, which inputs could flip it, and the answer source."""
    if line in ("Shaft", "Shaft model"):
        src = "measured_speed" if form.get("speed_source") in ("simulator", "launch_monitor") else form.get("speed_or_carry") or "?"
    elif line in ("Lie", "Length"):
        src = "measured_body" if measurement else "self_body"
    else:
        src = ""
    return f"{line}|{'+'.join(sorted(set(flip_inputs)))}|{src}"


def verdict_confidence(profile: dict, measurement: dict | None, method_error: tuple | None,
                       swing: dict | None) -> dict | None:
    """Rerun the kit engine across each input's error range; a decision that flips is a close call.

    Ranges for self-reported answers are MEASURED on the Takomo Fitting Lab training data (app/calibration.json):
    golfers over-report carry by ~15 yd and club speed by ~2 mph, so the range is centred on what they most likely
    mean (answer minus bias) +-1 SD, and the centre itself is tried too. Score +-2 strokes (when a number was given),
    a stated tempo the neighbouring bucket (wrong 36 % of the time; exact if measured from video), the neighbouring
    glove sizes (wrong 37 %), wrist-to-floor the measurement method's error.
    """
    form = dict(profile)
    if measurement:
        form.update(height_cm=measurement["height_cm"], wrist_to_floor_cm=measurement["wrist_to_floor_cm"])
    base = _spec(form)
    if not base:
        return None
    variants = []
    if form.get("score_value"):
        for d in (-2, 2):
            variants.append(("score", dict(form, score_bucket=fitting.B.score_bucket_from_score(float(form["score_value"]) + d))))
    measured_speed = form.get("speed_source") in ("simulator", "launch_monitor")
    if form.get("speed_or_carry") == "speed" and form.get("speed_mph"):
        bias, sd = (0.0, 1.5) if measured_speed else calibration.self_report("speed_mph")
        for d in (0.0, -sd, sd):
            variants.append(("swing speed", dict(form, speed_mph=round(form["speed_mph"] - bias + d, 1))))
    elif form.get("carry_yd"):
        bias, sd = calibration.self_report("carry_yd")
        for d in (0.0, -sd, sd):
            variants.append(("carry", dict(form, carry_yd=round(form["carry_yd"] - bias + d))))
    if form.get("tempo") in TEMPOS and not (swing and swing.get("tempo") == form["tempo"]):
        i = TEMPOS.index(form["tempo"])
        for j in (i - 1, i + 1):
            if 0 <= j < 3:
                variants.append(("tempo", dict(form, tempo=TEMPOS[j])))
    h_bias, h_sd = (0.0, 1.0) if measurement else calibration.self_report("height_cm")
    for d in (-h_sd, h_sd):
        variants.append(("height", dict(form, height_cm=round(form["height_cm"] - h_bias + d, 1))))
    wtf_err = method_error[0] if (measurement and method_error) else calibration.self_report("wrist_to_floor_cm")[1]
    for d in (-wtf_err, wtf_err):
        variants.append(("wrist-to-floor", dict(form, wrist_to_floor_cm=form["wrist_to_floor_cm"] + d)))
    if form.get("glove") in GLOVES:
        i = GLOVES.index(form["glove"])
        for j in (i - 1, i + 1):
            if 0 <= j < len(GLOVES):
                variants.append(("glove size", dict(form, glove=GLOVES[j])))
    flips = {k: [] for k in base}
    for label, f in variants:
        s = _spec(f)
        if not s:
            continue
        for k in base:
            if s[k] != base[k] and all(a != s[k] or l != label for l, a in flips[k]):
                flips[k].append((label, s[k]))
    weak = {"carry"} | ({"wrist-to-floor"} if not measurement else set())
    lines = {}
    for k in base:
        fl = flips[k]
        key = line_key(k, [lbl for lbl, _ in fl], form, measurement)
        rate = calibration.line_accuracy(key)
        if fl:
            why = f"your {fl[0][0]} is within its margin of {_fmt(k, fl[0][1])}"
        elif k == "Lie" and not measurement:
            why = "wrist-to-floor is self-measured, often 3 cm off"
        elif k == "Shaft" and form.get("speed_or_carry") == "carry":
            why = "carry is only an estimate of club speed"
        else:
            why = None
        if rate is not None:
            # measured: how often this line was right for training golfers in the same situation
            level = "High" if rate >= HIGH_RATE else "Medium" if rate >= MEDIUM_RATE else "Low"
            right = f"right about {round(rate * 10)} times in 10 for golfers who answered like you"
            why = f"{why}; {right}" if why and level != "High" else (right if level != "High" else None)
        elif fl:
            level = "Low" if any(lbl in weak for lbl, _ in fl) else "Medium"
        elif why or (k == "Head" and profile.get("score_representative") in ("one", "guess")):
            level, why = "Medium", why or "your score is from one round or a guess"
        else:
            level = "High"
        if k == "Head" and not (profile.get("characteristics") or []):
            # Your game doesn't ask what the irons should do; without it the head agrees with Takomo's quiz about 8 in 10
            # (79.5% on the Fitting Lab dev set, against 91.8% with the quiz's priorities)
            if level == "High":
                level = "Medium"
            why = "from your score and speed only, with no stated priorities; that matches Takomo's quiz about 8 times in 10"
        lines[k] = {"level": level, "why": why, "alternatives": [_fmt(k, a) for _, a in fl], "measured_rate": rate,
                    "flip_inputs": sorted({lbl for lbl, _ in fl}), "key": key,
                    "next_step": NEXT_STEP[k] if level != "High" else None}
    close = None
    for k in ("Head", "Shaft", "Shaft model", "Lie", "Length", "Grip"):
        if flips[k]:
            lbl, alt = flips[k][0]
            step = NEXT_STEP[k]
            close = {"decision": k, "text": f"{_fmt(k, base[k])} vs {_fmt(k, alt)} is inside your {lbl} margin. "
                                            f"{step[0].upper()}{step[1:]}."}
            break
    return {"lines": lines, "close_call": close}


def short_whys(profile: dict, best: dict, measurement: dict | None) -> dict:
    """One line of 'why' per verdict row; the full explanation stays one tap away."""
    p = dict(profile)
    if measurement:
        p.update(height_cm=measurement["height_cm"], wrist_to_floor_cm=measurement["wrist_to_floor_cm"])
    score = fitting.B.SCORE_LABELS.get(p.get("score_bucket"), "").lower()
    pri = ", ".join(p.get("characteristics") or []) or "no stated priorities"
    if p.get("speed_or_carry") == "speed" and p.get("speed_mph"):
        speed = f"{p['speed_mph']:g} mph club speed"
    else:
        speed = f"{(p.get('carry_yd') or 0):g} yd carry, an estimate"
    flight = {"low": "lower", "mid": "medium", "high": "higher"}.get(p.get("flight"), "medium")
    h_in = p["height_cm"] / 2.54
    feet, inches = int(h_in // 12), round(h_in - 12 * int(h_in // 12))
    return {
        "Head": f"shoots {score}, wants {pri}" if p.get("characteristics") else f"shoots {score}, with {speed}",
        "Shaft": f"from your {speed}; {p.get('tempo', 'moderate')} tempo, {flight} flight",
        "Length": f"{feet} ft {inches} in tall" + (", ladies flex adds an inch" if best["flex"] == "lady" else ""),
        "Lie": f"{p['height_cm']:g} cm tall with {p['wrist_to_floor_cm']:g} cm wrist-to-floor" + (", measured" if measurement else ""),
        "Grip": f"glove size {p.get('glove')}",
    }


def with_ball_line(conf: dict | None, bl: dict | None) -> dict | None:
    """When the lie comes from ball-line shots, its confidence is the accuracy measured for that test on the kit's
    held-out golfers (exact match for the level, as for every line), not the static chart's margins."""
    if not conf or not bl:
        return conf
    acc = bl.get("accuracy") or {}
    exact, within = acc.get("exact"), acc.get("within_1")
    level = ("High" if exact is not None and exact >= HIGH_RATE else
             "Medium" if exact is not None and exact >= MEDIUM_RATE else "Low")
    why = (f"from your {bl['shots']} ball-line shots: on the kit's held-out golfers this lands on the reference lie "
           f"{round((exact or 0) * 10)} times in 10, and within 1 degree {round((within or 0) * 100)} % of the time")
    near = bl.get("edge_deg", 1) < 0.2
    conf = {**conf, "lines": {**conf["lines"], "Lie": {
        "level": level, "why": why + ("; it sits near the line between two settings" if near else ""),
        "alternatives": [], "measured_rate": exact, "flip_inputs": ["ball-line shots"], "key": "Lie|ball_line",
        "next_step": ("hit a few more ball-line shots" if bl["shots"] < 5 else
                      "a lie-board check with a fitter would confirm it") if level != "High" else None}}}
    if conf.get("close_call", {}) and conf["close_call"].get("decision") == "Lie":
        conf["close_call"] = None                     # the chart's margin no longer decides the lie
    return conf


LINE_NAMES = {"Head": "head", "Shaft": "shaft flex", "Shaft model": "shaft model", "Length": "length", "Lie": "lie", "Grip": "grip"}


def trust_summary(conf: dict | None, profile: dict, measurement: dict | None, swing: dict | None) -> dict | None:
    """What the golfer needs to decide whether to trust the fit: how sure each part is, how well those levels held up
    on held-out golfers, which numbers were measured and which are answers, the least certain part and what would fix it."""
    if not conf:
        return None
    lines = conf["lines"]
    counts = {lvl: sum(1 for v in lines.values() if v["level"] == lvl) for lvl in ("High", "Medium", "Low")}
    check = (calibration.DATA.get("dev_confidence_check") or {}).get("overall") or {}
    tested = {lvl: {"in_10": round(v["right"] * 10), "n": v["n"]} for lvl, v in check.items()}
    measured, answered = [], []
    if measurement:
        cal = (measurement.get("detail") or {}).get("calibration")
        how = {"photo": "camera estimate" + (" using your height" if cal == "entered_height" else ""),
               "tape": "tape measure", "simulated": "simulated demo photo"}.get(measurement.get("source"), measurement.get("source"))
        measured.append(f"wrist-to-floor and height ({how})")
    else:
        answered.append("height and wrist-to-floor")
    if profile.get("speed_or_carry") == "speed" and profile.get("speed_source") in ("simulator", "launch_monitor"):
        measured.append(f"7-iron club speed ({profile['speed_source'].replace('_', ' ')})")
    else:
        answered.append("7-iron carry" if profile.get("speed_or_carry") == "carry" else "7-iron club speed")
    if swing and swing.get("tempo") == profile.get("tempo"):
        measured.append(f"tempo ({swing.get('swings', 1)} swing video{'s' if swing.get('swings', 1) > 1 else ''})")
    else:
        answered.append("tempo")
    answered += ["usual score", "glove size", "priorities and flight" if profile.get("characteristics") else "ball flight"]
    order = {"Low": 0, "Medium": 1, "High": 2}
    weakest = min(lines.items(), key=lambda kv: (order[kv[1]["level"]], kv[1].get("measured_rate") or 1))
    k, w = weakest
    least = None if w["level"] == "High" else {"part": LINE_NAMES.get(k, k), "level": w["level"], "why": w.get("why"),
                                               "next_step": w.get("next_step")}
    return {"counts": counts, "tested_on_held_out": tested, "measured": measured, "from_answers": answered,
            "least_certain": least,
            "basis": "Levels were learned on 20,000 and tested on 3,000 other golfers from Takomo's Fitting Lab data. "
                     "That data is simulated: this is a prototype, not yet checked on real golfers."}
