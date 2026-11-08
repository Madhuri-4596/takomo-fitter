"""Reference implementation of Takomo's current 12-question iron fitting tool.

Built from the rules in "The Fitter in Your Pocket - Participant Pack", the
lookup tables in takomo_products.json and Takomo's corrections of 5 November 2026
(head speed points, over-92 cap, forgiveness penalty, PING-based length and lie,
midsize grips in stock). docs/BASELINE_RULES_INTERPRETATION.md lists every rule
and its source. It is a starting point to beat, not Takomo's production code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable

from . import products as P

SCORE_LABELS = {1: "Under 71", 2: "71-77", 3: "78-84", 4: "85-92", 5: "Over 92"}
CHARACTERISTICS = ["forgiveness", "distance", "forged feel", "distance control",
                   "workability", "less offset", "more offset"]
TEMPO_TO_PROFILE = {"smooth": "slow", "moderate": "mid", "quick": "fast"}
# Takomo, 5 Nov 2026: speed points per swing bucket and head (live tool table)
SPEED_POINTS = {
    1: {"101-mkii": 7, "201-mkii": 5, "201t-mkii": 3, "301-cb": 1, "301-mb": 1},
    2: {"101-mkii": 4, "201-mkii": 5, "201t-mkii": 3, "301-cb": 1, "301-mb": 1},
    3: {"101-mkii": 3, "201-mkii": 4, "201t-mkii": 5, "301-cb": 3, "301-mb": 3},
    4: {"101-mkii": 2, "201-mkii": 3, "201t-mkii": 4, "301-cb": 5, "301-mb": 5},
    5: {"101-mkii": 1, "201-mkii": 2, "201t-mkii": 3, "301-cb": 5, "301-mb": 5},
}
OVER92_SPEED_CAP = 3                       # score bucket 5 with swing bucket 4-5: speed points max 3
FORGIVENESS_PENALTY = {"301-cb": -1, "301-mb": -1}   # when "forgiveness" is a priority
# Lie: Takomo works from the PING colour code chart. Its band coordinates are a
# graphic on ping.com; this plane (lie = a + b*height + c*wrist_to_floor, cm)
# approximates it. It reproduces Takomo's five examples and six published PING
# chart examples (docs/BASELINE_RULES_INTERPRETATION.md). Rounded, capped at +-3.
LIE_PLANE = (-14.8636, -0.018586, 0.215119)


@dataclass
class Answers:
    age: int
    sex: str                      # "male" | "female"
    score_bucket: int             # 1..5 (Under 71 ... Over 92)
    swing_bucket: int             # 1..5 from 7-iron speed OR carry (treated as equivalent)
    characteristics: list[str]    # up to 3 of CHARACTERISTICS
    flight: str                   # "low" | "mid" | "high"
    tempo: str                    # "smooth" | "moderate" | "quick"
    height_cm: float
    wrist_to_floor_cm: float
    glove: str                    # XS S M M/L L XL
    handedness: str = "right"     # "right" | "left"
    rounds_per_year: str | None = None   # collected, not used (as today)


@dataclass
class Recommendation:
    rank: int
    head: str
    head_score: float
    score_parts: dict
    shaft: str
    shaft_model: str
    flex: str
    material: str
    length_in: float
    lie_deg: int
    grip: str
    handedness: str
    purchase: str                 # "stock" | "custom"
    trace: list[str] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


# ------------------------------------------------------------------ buckets
def swing_bucket_from_speed(mph: float) -> int:
    # pack ranges are whole numbers (60-70, 71-80 ...); decimals between them go up: 70.4 -> 71-80
    if mph < 60: return 1
    if mph <= 70: return 2
    if mph <= 80: return 3
    if mph <= 90: return 4
    return 5


def swing_bucket_from_carry(yd: float) -> int:
    # pack ranges share endpoints (120-135, 135-155 ...); a value on an edge goes to the lower bucket
    if yd < 120: return 1
    if yd <= 135: return 2
    if yd <= 155: return 3
    if yd <= 175: return 4
    return 5


def score_bucket_from_score(score: float) -> int:
    if score < 71: return 1
    if score <= 77: return 2
    if score <= 84: return 3
    if score <= 92: return 4
    return 5


# ------------------------------------------------------------------ heads
def score_heads(a: Answers) -> list[tuple[str, float, dict]]:
    """Total = skill + speed + characteristics + forgiveness penalty (Takomo, 5 Nov 2026)."""
    out = []
    chars = a.characteristics[:3]
    for slug in P.HEAD_ORDER:
        h = P.head(slug)
        skill = 5 - abs(a.score_bucket - h["target_score_bucket"])
        speed = SPEED_POINTS[a.swing_bucket][slug]
        if a.score_bucket == 5 and a.swing_bucket in (4, 5):
            speed = min(speed, OVER92_SPEED_CAP)
        r = h["ratings"]
        ch = 0.0
        for c in chars:
            if c == "forgiveness": ch += r["forgiveness"]
            elif c == "distance": ch += r["distance"]
            elif c == "forged feel": ch += 2 * r["feel"]
            elif c == "distance control": ch += r["distance_control"]
            elif c == "workability": ch += r["workability"]
            elif c == "more offset": ch += r["offset"]
            elif c == "less offset": ch += 5 - r["offset"]   # INTERPRETATION of "inverse"
        pen = FORGIVENESS_PENALTY.get(slug, 0) if "forgiveness" in chars else 0
        total = skill + speed + ch + pen
        out.append((slug, float(total), {"skill": skill, "speed": speed, "characteristics": ch,
                                         "forgiveness_penalty": pen}))
    # ties: higher skill match first, then product order (INTERPRETATION)
    out.sort(key=lambda t: (-t[1], -t[2]["skill"], P.HEAD_ORDER.index(t[0])))
    return out


# ------------------------------------------------------------------ shafts
def flex_for(a: Answers) -> str:
    return {1: "lady" if a.sex == "female" else "senior", 2: "senior", 3: "regular",
            4: "stiff", 5: "x-stiff"}[a.swing_bucket]


def material_for(a: Answers, flex: str) -> str:
    if flex in ("lady", "senior") or a.sex == "female" or a.age >= 70:
        return "graphite"
    return "steel"


def _tempo_ok(shaft: dict, tempo_profile: str) -> bool:
    tp = shaft.get("tempo_profile")
    return (not tp) or tp == "any" or "any" in tp or tempo_profile in tp


def pick_shaft(head_slug: str, a: Answers, trace: list[str]) -> dict:
    flex = flex_for(a)
    mat = material_for(a, flex)
    tempo = TEMPO_TO_PROFILE[a.tempo]
    pool = [s for s in P.shafts_for_head(head_slug) if s["material"] == mat]
    target = P.flex_index(flex)
    # nearest available flex within the material (relaxation step 3)
    avail = sorted({P.flex_index(s["flex"]) for s in pool}, key=lambda i: (abs(i - target), -i))
    stock = P.head(head_slug)["stock_shafts"]
    for fi in avail:
        cands = [s for s in pool if P.flex_index(s["flex"]) == fi]
        for rule, ok in (("flight+tempo", lambda s: a.flight in s["flight_profile"] and _tempo_ok(s, tempo)),
                         ("flight only", lambda s: a.flight in s["flight_profile"]),
                         ("tempo only", lambda s: _tempo_ok(s, tempo)),
                         ("any", lambda s: True)):
            m = [s for s in cands if ok(s)]
            if m:
                m.sort(key=lambda s: (s["model"] not in stock, cands.index(s)))
                pick = m[0]
                if fi != target:
                    trace.append(f"shaft: no {mat} {flex} shaft for {head_slug}; nearest flex {pick['flex']}")
                trace.append(f"shaft: {mat} because " + (
                    "flex is lady/senior" if flex in ("lady", "senior") else
                    "golfer is female" if a.sex == "female" else
                    "age 70+" if a.age >= 70 else "default steel"))
                trace.append(f"shaft: flex {flex} from swing bucket {a.swing_bucket}; model matched on {rule}")
                return pick
    raise ValueError(f"no shaft available for {head_slug}")


# ------------------------------------------------------------------ length, lie, grip
def length_for(a: Answers, flex: str, trace: list[str]) -> float:
    """Length from height (PING colour code chart length row = the pack's table),
    plus 1 in back for ladies-flex shafts, capped to +-1.5 in."""
    rows = P.load()["current_tool_rules"]["length_by_height_cm"]
    base = rows[-1]["length_in"]
    for r in rows:
        if r["below_cm"] is not None and a.height_cm < r["below_cm"]:
            base = r["length_in"]
            break
    L = base
    if flex == "lady":
        L += P.load()["current_tool_rules"]["lady_flex_length_adjustment_in"]
    L = max(-1.5, min(1.5, round(L * 2) / 2))
    trace.append(f"length: {base:+.1f} in from height {a.height_cm:.0f} cm"
                 + (", +1 in ladies-flex add-back" if flex == "lady" else "") + f" -> {L:+.1f}")
    return L


def lie_continuous(height_cm: float, wtf_cm: float) -> float:
    a0, b, c = LIE_PLANE
    return a0 + b * height_cm + c * wtf_cm


def lie_for(a: Answers, trace: list[str]) -> int:
    raw = lie_continuous(a.height_cm, a.wrist_to_floor_cm)
    lie = int(max(-3, min(3, round(raw))))
    trace.append(f"lie: PING-style static chart {raw:+.2f} deg -> {lie:+d}")
    return lie


def grip_for(a: Answers, trace: list[str]) -> str:
    g = P.load()["current_tool_rules"]["grip_by_glove_size"][a.glove]
    trace.append(f"grip: {g} from glove {a.glove}")
    return g


STOCK_GRIPS = ("standard", "midsize")      # Takomo, 5 Nov 2026: midsize is also stock


def purchase_for(head_slug: str, sh: dict, length: float, lie: int, grip: str) -> str:
    stock = (sh["material"] == "steel" and sh["model"] in P.head(head_slug)["stock_shafts"]
             and length == 0 and lie == 0 and grip in STOCK_GRIPS)
    return "stock" if stock else "custom"


# ------------------------------------------------------------------ main entry
def recommend(a: Answers, top_n: int = 2) -> list[Recommendation]:
    ranked = score_heads(a)
    recs = []
    for rank, (slug, total, parts) in enumerate(ranked[:top_n], start=1):
        trace = [f"head: {slug} total {total:.0f} = skill {parts['skill']} + speed {parts['speed']} "
                 f"+ characteristics {parts['characteristics']:.0f} + forgiveness penalty {parts['forgiveness_penalty']}"]
        sh = pick_shaft(slug, a, trace)
        L = length_for(a, sh["flex"], trace)
        lie = lie_for(a, trace)
        grip = grip_for(a, trace)
        buy = purchase_for(slug, sh, L, lie, grip)
        trace.append(f"purchase: {buy}")
        recs.append(Recommendation(rank, slug, total, parts, sh["slug"], sh["model"], sh["flex"],
                                   sh["material"], L, lie, grip, a.handedness, buy, trace))
    return recs


def answers_from_row(row) -> Answers:
    """Build Answers from a questionnaire.csv row (dict-like)."""
    chars = [c for c in str(row["characteristics"]).split("|") if c and c != "nan"]
    if str(row.get("speed_or_carry")) == "speed":
        sb = swing_bucket_from_speed(float(row["reported_7i_speed_mph"]))
    else:
        sb = swing_bucket_from_carry(float(row["reported_7i_carry_yd"]))
    return Answers(age=int(row["age"]), sex=row["sex"], score_bucket=int(row["score_bucket"]),
                   swing_bucket=sb, characteristics=chars, flight=row["flight"], tempo=row["tempo"],
                   height_cm=float(row["height_cm"]), wrist_to_floor_cm=float(row["wrist_to_floor_cm"]),
                   glove=row["glove"], handedness=row["handedness"],
                   rounds_per_year=row.get("rounds_per_year"))
