"""Fitting sessions: the one profile shared by the form, the voice agent and the fitting backend.

Rules enforced here:
- The agent can only *stage* answers (propose_updates). Staged values never reach the
  fitting until the golfer confirms them (confirm_staged), which the web app only calls
  from a real click on a Confirm button.
- Photo measurements are staged the same way and need their own confirmation.
- Any change to confirmed inputs makes the previous recommendation stale.
- Recommendations are only computed from confirmed inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import fitting

SESSION_TTL_S = 3 * 3600
IN_TO_CM = 2.54
KMH_TO_MPH = 0.621371
M_TO_YD = 1.09361

FIELDS = ["age", "sex", "handedness", "rounds_per_year", "score_bucket", "speed_or_carry", "speed_mph", "carry_yd",
          "characteristics", "flight", "tempo", "height_cm", "wrist_to_floor_cm", "glove",
          # evidence about the golfer's game: explains the fit, not used by the kit's rules
          "score_representative", "lose_shots", "ball_start", "ball_curve", "ball_height", "score_value",
          "current_clubs",                    # free text, e.g. "ten-year-old cavity backs": context only
          "flight_current",                   # the flight they hit now; the rules use "flight", the one they want
          "handedness_seen",                  # true when handedness was read off the filmed swings
          "speed_source"]                     # where the club-speed number came from
ENUMS = {"score_representative": ["several", "one", "guess"], "lose_shots": ["irons", "driving", "short_game", "putting", "not_sure"],
         "ball_start": ["left", "straight", "right"], "ball_curve": ["left", "none", "right"],
         "ball_height": ["lower", "as_wanted", "higher"],
         "speed_source": ["simulator", "launch_monitor", "own_estimate"],
         "flight_current": ["low", "mid", "high"]}
REQUIRED = ["age", "sex", "handedness", "score_bucket", "seven_iron", "flight", "tempo",
            "height_cm", "wrist_to_floor_cm", "glove"]
SCORE_LABELS = {1: "under 71", 2: "71 to 77", 3: "78 to 84", 4: "85 to 92", 5: "over 92"}
ROUNDS = ["Under 10", "10-30", "31-50", "Over 50"]


class Clarify(ValueError):
    """The value can't be staged until the golfer clarifies it."""


# ------------------------------------------------------------------ parsing spoken values
_NUM = r"(\d+(?:[.,]\d+)?)"


_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                      "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_WORDS.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90})


_NUMWORD = "|".join(sorted(list(_WORDS) + ["hundred"], key=len, reverse=True))
_RUN = re.compile(rf"\b(?:a\s+)?(?:{_NUMWORD})(?:[\s-]+(?:and[\s-]+)?(?:{_NUMWORD}))*\b")


def _run_value(run: str) -> str:
    """One run of number words -> digits; numbers that can't combine stay separate ('five ten' -> '5 10')."""
    out, cur = [], None
    for w in re.findall(rf"{_NUMWORD}", run):
        if w == "hundred":
            cur = (cur or 1) * 100
            continue
        n = _WORDS[w]
        if cur is not None and ((cur >= 20 and cur % 10 == 0 and cur % 100 and n < 10) or (cur >= 100 and cur % 100 == 0 and n < 100)):
            cur += n
        else:
            if cur is not None:
                out.append(str(cur))
            cur = n
    out.append(str(cur))
    return " ".join(out)


def words_to_digits(text) -> str:
    """Spoken numbers as digits: 'five foot ten' -> '5 foot 10', 'thirty-five' -> '35', 'a hundred and fifty' -> '150'."""
    return _RUN.sub(lambda m: _run_value(m.group(0)), str(text).lower())


def _num(text) -> float:
    m = re.search(_NUM, words_to_digits(text))
    if not m:
        raise Clarify(f"I didn't catch a number in '{text}'.")
    return float(m.group(1).replace(",", "."))


def _feet_inches(text: str) -> float | None:
    """'5 11', 5'11", '5 foot 11', '5 ft 11 in', 'five foot ten', '5-10' -> inches."""
    text = words_to_digits(text).replace("-", " ")
    m = re.match(r"^\s*(\d)\s*(?:'|ft|feet|foot|\s)\s*(\d{1,2}(?:\.\d+)?)?\s*(?:\"|in|inch|inches)?\s*$", text.strip().lower())
    if not m:
        return None
    return int(m.group(1)) * 12 + float(m.group(2) or 0)


def _fmt_ftin(cm: float) -> str:
    total = cm / IN_TO_CM
    ft, inch = int(total // 12), round(total - 12 * int(total // 12))
    if inch == 12:
        ft, inch = ft + 1, 0
    return f"{ft} ft {inch} in"


def _length_cm(value, unit: str | None, what: str, lo: float, hi: float) -> tuple[float, str]:
    unit = (unit or "").lower().strip()
    text = str(value)
    if not unit:
        unit = _unit_in(text) or ""
    if not unit and not re.search(r"\d\s*'|\b(foot|feet|ft)\b", words_to_digits(text)):
        n = _num(text)                                  # no unit said: the number's size tells cm from inches
        if what == "height":
            unit = "cm" if 120 <= n <= 220 else "in" if 48 <= n <= 87 else "ft_in" if 4 <= n <= 7.5 else ""
        else:
            unit = "cm" if 55 <= n <= 120 else "in" if 22 <= n <= 48 else ""
    if not unit and re.search(r"\d\s*'|\b(foot|feet|ft)\b", words_to_digits(text)):
        unit = "ft_in"                               # "5'10" or "five foot ten" says its own unit
    if unit in ("ft_in", "ft", "feet"):
        inches = _feet_inches(text)
        if inches is None:
            raise Clarify(f"Please say your {what} as feet and inches, for example 5 foot 11.")
        cm = inches * IN_TO_CM
    elif unit in ("in", "inch", "inches"):
        cm = _num(text) * IN_TO_CM
    elif unit in ("cm", "centimetres", "centimeters"):
        cm = _num(text)
    elif unit in ("m", "metres", "meters"):
        cm = _num(text) * 100
    else:
        raise Clarify(f"Was your {what} of {text} in centimetres or inches?")
    cm = round(cm, 1)
    if not lo <= cm <= hi:
        raise Clarify(f"{text} {unit} gives a {what} of {cm:.0f} cm, which doesn't look right. "
                      f"Could you say it again with the unit?")
    return cm, f"{cm:g} cm ({_fmt_ftin(cm) if what == 'height' else f'{cm / IN_TO_CM:.1f} in'})"


def _unit_in(s: str) -> str | None:
    """The unit the golfer said, if any: '34 inches', '86 cm', "5'10", '140 yards', '75 mph', '160 metres'."""
    t = s.lower()
    if re.search(r"\d\s*('|’)\s*\d|\bfoot\b|\bfeet\b|\bft\b", t):      # 5'10" is feet and inches, not inches
        return "ft_in"
    if re.search(r"\binch|\bins?\b|\d\s*(\"|”|'')", t):
        return "in"
    if re.search(r"\bcm\b|centimet|\d\s*cm", t):
        return "cm"
    if re.search(r"\bfoot\b|\bfeet\b|\bft\b|\d\s*('|’)", t):
        return "ft_in"
    if re.search(r"\bmph\b|miles", t):
        return "mph"
    if re.search(r"km\s*/?\s*h|\bkph\b|kilomet", t):
        return "kmh"
    if re.search(r"\byards?\b|\byds?\b", t):
        return "yd"
    if re.search(r"\bmet(er|re)s?\b|\d\s*m\b", t):
        return "m"
    return None


def _has(t: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}\b", t) for w in words)


def _score_number(t: str) -> float | None:
    """'around 88', 'low 90s', 'mid eighties', 'high 80s', 'break 90', '80s'."""
    for w, n in (("seventies", "70s"), ("eighties", "80s"), ("nineties", "90s"), ("sixties", "60s"), ("hundreds", "100s")):
        t = t.replace(w, n)
    t = words_to_digits(t)
    m = re.search(r"(\d{2,3})\s*(?:to|-|–|and)\s*(\d{2,3})", t)              # a bracket: "85 to 92" -> the middle
    if m:
        return (float(m.group(1)) + float(m.group(2))) / 2
    m = re.search(r"\b(?:under|below|less than|sub)\s*(\d{2,3})", t)         # "under 71" -> just under
    if m:
        return float(m.group(1)) - 1
    m = re.search(r"\b(?:over|above|more than)\s*(\d{2,3})|(\d{2,3})\s*(?:plus|\+)", t)   # "over 92" -> just over
    if m:
        return float(m.group(1) or m.group(2)) + 1
    m = re.search(r"(low|mid|middle|high)?\s*(\d{2,3})\s*'?s\b", t)
    if m:
        base = float(m.group(2))
        return base + {"low": 2, "mid": 5, "middle": 5, "high": 8}.get(m.group(1) or "", 5)
    m = re.search(r"break(?:ing)?\s*(\d{2,3})", t)
    if m:
        return float(m.group(1)) - 1
    m = re.search(_NUM, t)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_update(u: dict, profile: dict) -> tuple[dict, str]:
    """Turn one spoken answer into canonical profile fields plus a read-back text.

    Raises Clarify with the question the agent should ask when the value is ambiguous
    (missing unit, ball speed instead of club speed, total instead of carry, best
    instead of typical score) or implausible.
    """
    f = str(u.get("field", "")).strip()
    v = u.get("value")
    unit = u.get("unit")
    kind = (u.get("kind") or "").lower().strip()
    if v is None or str(v).strip() == "":
        raise Clarify(f"I didn't catch a value for {f}.")
    s = str(v).strip().lower()

    if f == "current_clubs":
        text = " ".join(str(v).split())[:120]
        return {"current_clubs": text}, f"your current irons are {text}"
    if f == "age":
        age = int(round(_num(s)))
        if not 8 <= age <= 100:
            raise Clarify(f"Did you say you are {age}? Please repeat your age.")
        return {"age": age}, f"{age} years old"
    if f == "sex":
        # everyday answers: "men's", "mens", "a man", "male", "guy", "women's", "ladies", "female", "woman", "lady"
        t = re.sub(r"[^a-z ]", "", s.replace("’", "'").replace("'", ""))
        words = set(t.split())
        female = {"f", "female", "woman", "women", "womens", "lady", "ladies", "girl", "she"}
        male = {"m", "male", "man", "men", "mens", "guy", "gentleman", "gents", "he", "boy"}
        val = "female" if words & female else "male" if words & male else None
        if not val:
            raise Clarify("Should the fitting use the men's or the women's rules?")
        return {"sex": val}, val
    if f == "handedness":
        val = "left" if _has(s, "left", "lefty", "left-handed", "lefthanded", "l") else \
              "right" if _has(s, "right", "righty", "right-handed", "righthanded", "r") else None
        if not val:
            raise Clarify("Do you play right-handed or left-handed?")
        return {"handedness": val}, f"{val}-handed"
    if f == "rounds_per_year":
        if s.replace(" ", "") in {r.lower().replace(" ", "") for r in ROUNDS}:
            val = next(r for r in ROUNDS if r.lower().replace(" ", "") == s.replace(" ", ""))
        elif re.search(r"twice a week|two or three times a week|(every|most) days?|daily|\d+ times a week", s):
            val = "Over 50"
        elif re.search(r"once a week|every week|weekly|every weekend|each weekend", s):
            val = "31-50"
        elif re.search(r"(once|twice|few times) a month|monthly|every month|now and then", s):
            val = "10-30"
        elif re.search(r"rarely|hardly|few times a year|not (very )?often|once a year", s):
            val = "Under 10"
        else:
            n = _num(s)
            val = "Under 10" if n < 10 else "10-30" if n <= 30 else "31-50" if n <= 50 else "Over 50"
        return {"rounds_per_year": val}, f"{val} rounds a year"
    if f == "score" and (kind == "handicap" or re.search(r"handicap|\bhcp\b|\bcap\b|off \d|\bplus \d|\+\s*\d|scratch", s)):
        plus = bool(re.search(r"\bplus\b|\+", s))
        h = 0.0 if "scratch" in s else _num(words_to_digits(s))
        if not 0 <= h <= 54:
            raise Clarify(f"A handicap of {h:g}? What's your handicap, or what do you usually shoot?")
        n = 72 - h if plus else 72 + h
        b = fitting.B.score_bucket_from_score(n)
        return ({"score_bucket": b, "score_value": n},
                f"handicap {'plus ' if plus else ''}{h:g}, so about {n:g}, which is the {SCORE_LABELS[b]} group")
    if f == "score":
        if kind == "best" or _has(s, "best", "record", "lowest", "once"):
            raise Clarify("That sounds like your best score. What do you usually shoot on a normal round?")
        n = _score_number(s)
        if n is None:
            raise Clarify("What do you usually shoot over 18 holes?")
        if not 55 <= n <= 150:
            raise Clarify(f"A score of {n:g} for 18 holes doesn't sound right. What do you usually shoot?")
        b = fitting.B.score_bucket_from_score(n)
        return {"score_bucket": b, "score_value": n}, f"usual score {n:g}, which is the {SCORE_LABELS[b]} group"
    if f == "seven_iron_speed":
        driver = kind == "driver" or _has(s, "driver", "drive")
        src = ("launch_monitor" if re.search(r"launch monitor|trackman|gc ?quad|flightscope|mevo|rapsodo|skytrak", s) else
               "simulator" if (kind in ("sim", "simulator") or _has(s, "sim", "simulator", "simulators")) else None)
        if kind in ("ball", "ball_speed") or _has(s, "ball"):
            raise Clarify("That sounds like ball speed. The fitting needs club speed, the speed of the club head. "
                          "Do you know your 7-iron club speed? If not, your carry distance works too.")
        n = _num(s)
        unit_l = (unit or _unit_in(s) or "").lower()
        if not unit_l:                                   # no unit said: mph is the usual one; km/h numbers are bigger
            unit_l = "mph" if n <= 115 else "kmh"
        if unit_l in ("kmh", "km/h", "kph"):
            n = n * KMH_TO_MPH
        elif unit_l in ("mps", "m/s"):
            n = n * 2.23694
        elif unit_l != "mph":
            raise Clarify(f"Was {v} in miles per hour or kilometres per hour?")
        if driver:                                       # a driver speed, scaled to a 7-iron and tagged estimated
            if not 50 <= n <= 150:
                raise Clarify(f"{n:g} mph for a driver? Could you repeat it, or give me a 7-iron carry instead?")
            d = n
            n = round(n * 0.8, 1)
            return ({"speed_or_carry": "speed", "speed_mph": n, "carry_yd": None, "speed_source": "own_estimate"},
                    f"driver {d:g} mph, so about {n:g} mph with a 7-iron (estimated)")
        n = round(n, 1)
        if not 35 <= n <= 110:
            raise Clarify(f"{n:g} mph is unusual for a 7-iron club speed. Is that ball speed, or could you repeat it?")
        return ({"speed_or_carry": "speed", "speed_mph": n, "carry_yd": None, "speed_source": src},
                f"7-iron club speed {n:g} mph" + (f" from a {src.replace('_', ' ')}" if src else ""))
    if f == "seven_iron_carry":
        if kind == "total" or _has(s, "total", "roll", "rolls", "rolling", "including roll"):
            raise Clarify("That sounds like total distance including roll. How far does the ball fly in the air "
                          "before it lands? That's the carry.")
        n = _num(s)
        unit_l = (unit or _unit_in(s) or "yd").lower()
        if unit_l in ("m", "meters", "metres"):
            n = n * M_TO_YD
        elif unit_l not in ("yd", "yards", "yard"):
            raise Clarify(f"Was {v} in yards or metres?")
        n = round(n)
        if not 40 <= n <= 250:
            raise Clarify(f"A 7-iron carry of {n} yards doesn't sound right. Could you repeat it?")
        return ({"speed_or_carry": "carry", "carry_yd": float(n), "speed_mph": None, "speed_source": None},
                f"7-iron carry {n} yards in the air")
    if f == "height":
        cm, text = _length_cm(v, unit, "height", 120, 220)
        return {"height_cm": cm}, f"height {text} in golf shoes"
    if f == "wrist_to_floor":
        cm, text = _length_cm(v, unit, "wrist-to-floor", 55, 120)
        h = profile.get("height_cm")
        if h and not 0.42 <= cm / h <= 0.58:
            raise Clarify(f"A wrist-to-floor of {cm:g} cm with a height of {h:g} cm is unusual. "
                          "Could you measure it again, or use the photo step?")
        return {"wrist_to_floor_cm": cm}, f"wrist-to-floor {text}"
    if f == "glove":
        t = re.sub(r"\b(men'?s|women'?s|ladies|size|glove|a|an|it'?s|i wear|usually|probably|cadet|regular)\b", " ", s.replace("’", "'"))
        t = " ".join(t.split())
        val = ("M/L" if re.search(r"medium[\s-]*large|m\s*/\s*l|\bml\b", t) else
               "XL" if re.search(r"extra[\s-]*large|\bxl\b", t) else
               "XS" if re.search(r"extra[\s-]*small|\bxs\b", t) else
               "L" if re.search(r"\blarge\b|\bl\b", t) else
               "M" if re.search(r"\bmedium\b|\bm\b", t) else
               "S" if re.search(r"\bsmall\b|\bs\b", t) else None)
        if not val or val not in fitting.P.GLOVE_ORDER:
            raise Clarify("What glove size do you wear: XS, S, M, M/L, L or XL?")
        return {"glove": val}, f"glove size {val}"
    if f == "flight_current":
        val = ("low" if _has(s, "low", "lower", "flat", "flatter", "penetrating", "piercing") else
               "high" if _has(s, "high", "higher", "ballooning", "balloon", "towering") else
               "mid" if _has(s, "mid", "medium", "middle", "normal", "average", "regular", "standard") else None)
        if not val:
            raise Clarify("What does your iron flight look like now: low, mid or high?")
        return {"flight_current": val}, f"{val} ball flight now"
    if f == "flight_desired":
        cur = profile.get("flight_current")
        order = ["low", "mid", "high"]
        if cur and _has(s, "same", "keep", "fine", "happy", "as is", "no change", "unchanged", "it's good", "good"):
            return {"flight": cur}, f"keep it {cur}"
        if cur and _has(s, "lower", "flatter", "down", "less"):
            val = order[max(0, order.index(cur) - 1)]
            return {"flight": val}, f"{cur} now, wants it lower: {val}" if val != cur else f"already low, so low it is"
        if cur and _has(s, "higher", "up", "more", "taller"):
            val = order[min(2, order.index(cur) + 1)]
            return {"flight": val}, f"{cur} now, wants it higher: {val}" if val != cur else f"already high, so high it is"
        val = ("low" if _has(s, "low") else "high" if _has(s, "high") else "mid" if _has(s, "mid", "medium", "middle") else None)
        if not val:
            raise Clarify("Same, lower or higher?")
        return {"flight": val}, f"wants a {val} flight"
    if f == "flight":
        val = ("low" if _has(s, "low", "lower", "flatter", "flat", "down") else
               "high" if _has(s, "high", "higher", "up", "taller") else
               "mid" if _has(s, "mid", "medium", "middle", "same", "normal", "average", "keep", "fine", "neutral") else None)
        if not val:
            raise Clarify("Would you like a lower, medium or higher ball flight?")
        return {"flight": val}, f"{val} ball flight"
    if f == "tempo":
        val = ("smooth" if _has(s, "smooth", "slow", "slower", "rhythmic", "easy", "relaxed", "steady") else
               "quick" if _has(s, "quick", "fast", "faster", "aggressive", "rush", "rushed", "snappy", "hard") else
               "moderate" if _has(s, "moderate", "medium", "average", "normal", "middle", "between", "in-between", "mid") else None)
        if not val:
            raise Clarify("Is your swing tempo smooth, moderate or quick?")
        return {"tempo": val}, f"{val} tempo"
    if f == "characteristics":
        t, items = " " + s + " ", []
        for name, pattern in [("less offset", r"less offset|low offset|minimal offset"), ("more offset", r"more offset|bigger offset"),
                              ("distance control", r"distance control|\bcontrol\b|consisten\w* distance|dialled|dialed"),
                              ("forged feel", r"forged|\bfeel\b|soft"), ("workability", r"workab\w*|shap\w*|work the ball|draws? and fades?"),
                              ("forgiveness", r"forgiv\w*|mishits?|off[- ]cent(er|re)"), ("distance", r"distance|longer|further|farther|more yards|length")]:
            if re.search(pattern, t):
                items.append(name)
                t = re.sub(pattern, " ", t)
        items = list(dict.fromkeys(items))
        if not 1 <= len(items) <= 3:
            raise Clarify("Pick up to three: forgiveness, distance, forged feel, distance control, "
                          "workability, less offset or more offset.")
        return {"characteristics": items}, "priorities: " + ", ".join(items)
    if f in ENUMS:
        alias = {"several rounds": "several", "usually": "several", "one round": "one", "guess": "guess",
                 "iron": "irons", "irons": "irons", "driver": "driving", "driving": "driving", "tee": "driving",
                 "chipping": "short_game", "short game": "short_game", "putting": "putting", "not sure": "not_sure",
                 "straight": "straight", "left": "left", "right": "right", "none": "none", "no curve": "none",
                 "lower": "lower", "low": "lower", "higher": "higher", "high": "higher", "about right": "as_wanted",
                 "fine": "as_wanted", "as wanted": "as_wanted"}
        val = alias.get(s, s)
        if val not in ENUMS[f]:
            raise Clarify({"score_representative": "Is that score from several recent rounds, one round, or a guess?",
                           "lose_shots": "Where do you lose most shots: irons, driving, short game or putting?",
                           "ball_start": "Does the ball usually start left, straight or right?",
                           "ball_curve": "After that, does it curve left, curve right, or stay fairly straight?",
                           "ball_height": "Does it fly lower or higher than you want, or about right?"}[f])
        return {f: val}, f"{f.replace('_', ' ')}: {val.replace('_', ' ')}"
    raise Clarify(f"I can't store '{f}'.")


# ------------------------------------------------------------------ session
@dataclass
class Session:
    id: str
    profile: dict = field(default_factory=dict)          # confirmed answers (same object the form edits)
    staged: dict = field(default_factory=dict)           # field -> {"values", "text", "heard", "at"}
    measurement_staged: dict | None = None
    measurement: dict | None = None                      # confirmed photo/tape measurement
    strike: dict | None = None                           # confirmed strike-pattern analysis (explains only)
    swing: dict | None = None                            # confirmed swing-video tempo (evidence for the shaft model)
    ball_line: list | None = None                        # confirmed ball-line tilts (deg), one per shot: sets the lie
    recommendation: dict | None = None
    version: int = 0                                      # bumps on every change, so clients can sync
    touched: float = field(default_factory=time.time)

    # -------- inputs
    def effective_inputs(self) -> dict:
        p = dict(self.profile)
        if self.measurement:
            p["height_cm"] = self.measurement["height_cm"]
            p["wrist_to_floor_cm"] = self.measurement["wrist_to_floor_cm"]
        return p

    def missing(self) -> list[str]:
        p = self.profile
        out = []
        for k in REQUIRED:
            if k == "seven_iron":
                if not (p.get("speed_or_carry") == "speed" and p.get("speed_mph")) and \
                   not (p.get("speed_or_carry") == "carry" and p.get("carry_yd")):
                    out.append("seven_iron_speed_or_carry")
            elif p.get(k) in (None, ""):
                out.append(k)
        return out

    def _inputs_hash(self) -> str:
        return hashlib.sha256(json.dumps([self.effective_inputs(), self.ball_line], sort_keys=True).encode()).hexdigest()[:16]

    def lie_fn(self, measured_body: bool):
        """Lie from the ball-line shots, or None when there is no confirmed test."""
        if not self.ball_line:
            return None
        from . import ball_line
        return lambda h, w: ball_line.estimate(self.ball_line, h, w, measured_body)

    def set_ball_line(self, tilts: list | None):
        from . import ball_line
        if tilts is not None and not (ball_line.MIN_SHOTS <= len(tilts) <= ball_line.MAX_SHOTS):
            raise Clarify(f"Use {ball_line.MIN_SHOTS} to {ball_line.MAX_SHOTS} shots.")
        self.ball_line = [round(float(t), 1) for t in tilts] if tilts else None
        self._changed()

    def is_stale(self) -> bool:
        return bool(self.recommendation) and self.recommendation["inputs_hash"] != self._inputs_hash()

    def _changed(self):
        self.version += 1
        self.touched = time.time()

    # -------- form (explicit user input: commits directly)
    def set_profile(self, values: dict):
        if values.get("score_value") not in (None, ""):          # an exact score sets the bucket too
            try:
                values = dict(values, score_bucket=fitting.B.score_bucket_from_score(float(values["score_value"])))
            except (TypeError, ValueError):
                values = {k: v for k, v in values.items() if k != "score_value"}
        for k, v in values.items():
            if k in ENUMS and v not in ENUMS[k] and v is not None:
                continue
            if k in FIELDS:
                self.profile[k] = v
                self.staged.pop(_staged_key(k), None)
        if "characteristics" in values:
            self.profile["characteristics"] = list(values.get("characteristics") or [])[:3]
        self._changed()

    # -------- agent: stage only
    def propose(self, updates: list[dict]) -> list[dict]:
        results = []
        for u in updates:
            try:
                values, text = parse_update(u, {**self.profile, **self._staged_values()})
            except Clarify as c:
                results.append({"field": u.get("field"), "status": "needs_clarification", "ask": str(c)})
                continue
            key = u["field"]
            self.staged[key] = {"values": values, "text": text, "heard": str(u.get("heard_text") or u.get("value")),
                                "at": time.time()}
            results.append({"field": key, "status": "staged_for_review", "read_back": text})
        if any(r["status"] == "staged_for_review" for r in results):
            self._changed()
        return results

    def _staged_values(self) -> dict:
        out = {}
        for s in self.staged.values():
            out.update(s["values"])
        return out

    # -------- golfer: confirm or discard (only called from a real button press)
    def confirm_staged(self, keys: list[str], edits: dict | None = None) -> list[str]:
        """Commit staged fields. `edits` lets the golfer correct a value in the review card
        before confirming; corrected values go through the same validation."""
        done = []
        for k in keys:
            item = self.staged.get(k)
            if not item:
                continue
            values = item["values"]
            if edits and k in edits:
                values, _ = parse_update(edits[k], {**self.profile, **self._staged_values()})
            self.profile.update(values)
            del self.staged[k]
            done.append(k)
        if done:
            self._changed()
        return done

    def discard_staged(self, keys: list[str]):
        for k in keys:
            self.staged.pop(k, None)
        self._changed()

    # -------- measurements
    def stage_measurement(self, height_cm: float, wtf_cm: float, source: str, detail: dict | None = None):
        if not (120 <= height_cm <= 220 and 55 <= wtf_cm <= 120):
            raise Clarify("Those measurements are outside the range the fitting accepts.")
        self.measurement_staged = {"height_cm": round(height_cm, 1), "wrist_to_floor_cm": round(wtf_cm, 1),
                                   "source": source, "detail": detail or {}, "at": time.time()}
        self._changed()

    def confirm_measurement(self) -> dict:
        if not self.measurement_staged:
            raise Clarify("There is no measurement waiting for confirmation.")
        self.measurement = self.measurement_staged
        self.measurement_staged = None
        self._changed()
        return self.measurement

    # -------- recommendation (confirmed inputs only)
    def recommend(self) -> dict:
        missing = self.missing()
        if missing:
            return {"ok": False, "error": "missing_confirmed_answers", "missing": missing,
                    "pending_review": sorted(self.staged)}
        try:
            if self.measurement:
                err, method = measurement_error(self.measurement)
                cmp = fitting.compare(self.profile, self.measurement, err, method,
                                      lie_fn_self=self.lie_fn(False), lie_fn_measured=self.lie_fn(True))
                result, before = cmp["after"], cmp["before"]
            else:
                cmp, before = None, None
                result = fitting.recommend(fitting.answers_from_form(self.profile), self.lie_fn(False))
        except fitting.InputError as e:
            return {"ok": False, "error": "invalid_confirmed_answers", "detail": str(e)}
        rid = (self.recommendation or {}).get("id", 0) + 1
        self.recommendation = {"id": rid, "inputs_hash": self._inputs_hash(), "result": result,
                               "before": before, "compare": cmp, "measured": bool(self.measurement),
                               "measurement_source": (self.measurement or {}).get("source"), "at": time.time()}
        self._changed()
        return {"ok": True, **agent_summary(self.recommendation)}

    # -------- strike test (golfer-confirmed marks; explains the fit, never changes it)
    def set_strike(self, analysis: dict | None):
        self.strike = analysis
        self._changed()

    def set_swing(self, analysis: dict | None):
        if analysis:
            analysis = {k: v for k, v in analysis.items() if k != "track"}
        self.swing = analysis
        self._changed()

    def evidence(self) -> dict:
        from . import evidence as ev
        out = self._evidence()
        out["trust"] = ev.trust_summary(out["confidence"], self.profile, self.measurement, self.swing)
        return out

    def _evidence(self) -> dict:
        from . import evidence as ev
        rec = self.recommendation
        result = rec["result"] if rec else None
        err = measurement_error(self.measurement) if self.measurement else None
        return {"player_summary": ev.player_summary(self.profile, self.strike),
                "ball_flight": ev.flight_description(self.profile),
                "head_comparison": ev.head_comparison(result, self.profile, self.strike) if result else None,
                "decisions": ev.evidence_map(self.profile, self.measurement, self.strike, err, result, self.swing),
                "strike_test": self.strike, "swing_test": self.swing,
                "ball_line_test": (result or {}).get("ball_line") if result else None,
                "tempo_effect": ev.tempo_effect(self.profile, self.swing["tempo"]) if self.swing else None,
                "confidence": ev.with_ball_line(ev.verdict_confidence(self.profile, self.measurement, err, self.swing),
                                                (result or {}).get("ball_line")) if result else None,
                "trust": None,                                    # filled below from the confidence lines
                "short_whys": ev.short_whys(self.profile, result["best"], self.measurement) if result else None}

    # -------- views
    def state(self) -> dict:
        rec = self.recommendation
        return {
            "evidence": self.evidence(),
            "session_version": self.version,
            "profile": self.profile,
            "missing_fields": self.missing(),
            "staged_for_review": {k: {"read_back": v["text"], "heard": v["heard"], "values": v["values"]}
                                  for k, v in self.staged.items()},
            "measurement": self.measurement,
            "measurement_staged": self.measurement_staged,
            "measurement_check": self.measurement_check(),
            "recommendation": None if not rec else {**agent_summary(rec), "stale": self.is_stale()},
        }

    def measurement_check(self) -> dict | None:
        """Before the golfer confirms a staged measurement: would the method's assumed error flip stock/custom?"""
        m = self.measurement_staged
        if not m:
            return None
        if [k for k in self.missing() if k not in ("height_cm", "wrist_to_floor_cm")]:
            return {"available": False, "text": "Your other answers aren't complete yet, so we can't tell whether "
                                                "a small measurement change would affect the build."}
        form = dict(self.profile)
        for k in ("height_cm", "wrist_to_floor_cm"):        # no earlier answer: compare the measurement with itself
            if form.get(k) in (None, ""):
                form[k] = m[k]
        err, method = measurement_error(m)
        try:
            cmp = fitting.compare(form, m, err, method)
        except Exception:                                    # noqa: BLE001 - the preview must never break the state
            return {"available": False, "text": ""}
        c = cmp["confidence"]
        return {"available": True, "level": c["level"], "tape_check": c["tape_check"], "text": c["text"],
                "error_cm": err, "method": method, "build_after": cmp["after"]["best"]["purchase"]}

    def full_recommendation(self) -> dict | None:
        rec = self.recommendation
        if not rec:
            return None
        return {"id": rec["id"], "stale": self.is_stale(), "result": rec["result"], "before": rec["before"],
                "compare": rec["compare"], "measured": rec["measured"], "measurement_source": rec["measurement_source"]}


def measurement_error(m: dict) -> tuple[float, str]:
    """Assumed wrist-to-floor error (cm) and method name for a confirmed measurement (prototype values)."""
    from .photo import METHOD_ERROR_CM
    if m["source"] == "photo":
        cal = (m.get("detail") or {}).get("calibration")
        method = {"aruco_markers": "printed_sheet", "paper_corners": "plain_paper", "entered_height": "entered_height"}.get(cal, "printed_sheet")
    else:
        method = m["source"]
    return METHOD_ERROR_CM.get(method, 2.0), method


def _staged_key(profile_key: str) -> str:
    return {"height_cm": "height", "wrist_to_floor_cm": "wrist_to_floor", "score_bucket": "score",
            "speed_mph": "seven_iron_speed", "carry_yd": "seven_iron_carry"}.get(profile_key, profile_key)


def agent_summary(rec: dict) -> dict:
    """What the agent may say about the fit: the engine output, its reasons, and the next useful check."""
    r = rec["result"]["best"]
    why = r["why"]
    lie = why["lie_detail"]
    cmp = rec.get("compare")
    close = []
    conf = cmp["confidence"] if cmp else None
    if conf and conf["level"] != "confident":
        close.append(f"lie is {lie['edge_cm']} cm of wrist-to-floor from the next band"
                     + (", which could change stock or custom" if conf.get("tape_check") else ""))
    elif not cmp and lie["edge_cm"] is not None and lie["edge_cm"] < fitting.SELF_WTF_ERROR_CM:
        close.append(f"lie is {lie['edge_cm']} cm of wrist-to-floor from the next band")
    if why["head_close"] and why["head_close"]["gap"] <= 1:
        close.append(f"head: {why['head_close']['runner_up']} is within one point")
    if why["length_edge_cm"] is not None and why["length_edge_cm"] < 1.0:
        close.append(f"height is {why['length_edge_cm']} cm from the next length band")
    if not rec["measured"]:
        next_check = ("Measure wrist-to-floor with the photo step: it decides the lie, and self-measured "
                      "values are often 3 cm off.")
    elif conf and conf.get("tape_check"):
        next_check = conf["text"]
    elif conf and conf["level"] == "close call":
        next_check = "Optional: a tape measurement or the five-shot ball-line test would confirm the lie."
    else:
        next_check = None
    return {
        "recommendation_id": rec["id"],
        "spec": {"head": r["head_name"], "shaft_model": r["shaft_model"], "flex": r["flex_label"],
                 "material": r["material"], "length": "standard" if r["length_in"] == 0 else f"{r['length_in']:+g} in",
                 "lie": "standard" if r["lie_deg"] == 0 else f"{r['lie_deg']:+d} degrees",
                 "grip": r["grip"], "handedness": r["handedness"], "build": r["purchase"]},
        "reasons": {k: why[k] for k in ("head", "shaft", "length", "lie", "grip", "purchase")},
        "custom_reasons": why["custom_reasons"],
        "runner_up_head": rec["result"]["runner_up"]["head_name"] if rec["result"].get("runner_up") else None,
        "measured_with": rec.get("measurement_source") or "self-reported answers",
        "changes_from_measurement": cmp["changes"] if cmp else [],
        "close_calls": close,
        "next_useful_check": next_check,
    }


# ------------------------------------------------------------------ store
# Sessions are kept in memory and also saved to disk (one JSON file each), so a server restart or a
# redeploy doesn't throw away a golfer's answers mid-fitting. A session is deleted SESSION_TTL_S after
# its last use, from memory and from disk.
SESSION_DIR = Path(os.environ.get("FITTER_SESSION_DIR", Path(__file__).resolve().parent.parent / "data" / "sessions"))
# On Vercel several short-lived servers share the work, so sessions live in Upstash Redis (REST, no extra package).
# The Vercel Marketplace integration sets one of these pairs.
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
if REDIS_URL or os.environ.get("VERCEL"):
    SESSION_DIR = None                                    # the server's disk isn't shared or kept on Vercel


def _redis(*cmd):
    """One Redis command over Upstash's REST API; None if Redis isn't configured or doesn't answer."""
    if not (REDIS_URL and REDIS_TOKEN):
        return None
    import urllib.request
    req = urllib.request.Request(REDIS_URL, data=json.dumps([str(c) for c in cmd]).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("result")
    except Exception:                                     # Redis trouble must not break the fitting
        return None
_ID_OK = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class Store:
    def __init__(self, directory: Path | None = SESSION_DIR):
        self._s: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._dir = directory
        if self._dir:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # -------- disk
    def _path(self, sid: str) -> Path | None:
        return self._dir / f"{sid}.json" if self._dir and _ID_OK.match(sid) else None

    def _load(self):
        now = time.time()
        for f in self._dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if not _ID_OK.match(d.get("id", "")) or now - float(d.get("touched", 0)) > SESSION_TTL_S:
                    f.unlink(missing_ok=True)
                    continue
                self._s[d["id"]] = Session(**{k: d[k] for k in Session.__dataclass_fields__ if k in d})
            except (OSError, ValueError, TypeError, KeyError):
                continue                                      # a damaged file is skipped, never fatal

    def save(self, s: Session):
        if REDIS_URL and _ID_OK.match(s.id):
            _redis("SET", f"fitter:session:{s.id}", json.dumps(asdict(s), default=str), "EX", int(SESSION_TTL_S))
        p = self._path(s.id)
        if not p:
            return
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(asdict(s), default=str), encoding="utf-8")
            os.replace(tmp, p)                                # atomic: a crash never leaves half a file
        except OSError:
            pass                                              # disk trouble must not break the fitting

    def _drop(self, sid: str):
        self._s.pop(sid, None)
        if REDIS_URL and _ID_OK.match(sid):
            _redis("DEL", f"fitter:session:{sid}")
        p = self._path(sid)
        if p:
            p.unlink(missing_ok=True)

    # -------- sessions
    def create(self) -> Session:
        with self._lock:
            self._sweep()
            s = Session(id=secrets.token_urlsafe(24))
            self._s[s.id] = s
            self.save(s)
            return s

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            if REDIS_URL and _ID_OK.match(sid):              # another server may have changed it: Redis is the truth
                raw = _redis("GET", f"fitter:session:{sid}")
                if raw:
                    try:
                        d = json.loads(raw)
                        self._s[sid] = Session(**{k: d[k] for k in Session.__dataclass_fields__ if k in d})
                    except (ValueError, TypeError, KeyError):
                        pass
            s = self._s.get(sid)
            if s and time.time() - s.touched > SESSION_TTL_S:
                self._drop(sid)
                return None
            if s:
                s.touched = time.time()
            return s

    def _sweep(self):
        now = time.time()
        for k in [k for k, s in self._s.items() if now - s.touched > SESSION_TTL_S]:
            self._drop(k)


store = Store()
