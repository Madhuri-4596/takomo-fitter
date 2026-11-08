"""Ball-line lie test: lie from shot evidence instead of the static chart alone.

The golfer draws a vertical line on a ball, hits a few 7-irons off a hard mat with their own club, and photographs
the face after each shot. They tap the two ends of the transferred line and the heel and toe ends of the leading
edge; the kit's camera/ball_line.py turns that into a tilt (+ = top of the line leans toward the toe = club toe-up at
impact = needs flatter).

The tilt exaggerates (face angle also rotates the mark) and the golfer's own club has an unknown lie, so the lie need
is estimated with a model FITTED on the kit's training golfers (tools/fit_ball_line.py):

    need ~ a * static-chart reading + b * mean tilt + c

On the held-out dev golfers this puts the lie within 1 degree of the reference for 94 % of golfers with a
self-measured body (80 % from the chart alone) and 95 % with a measured body (86 %). Exact matches: 46 % / 49 %.
"""
from __future__ import annotations

import sys

import numpy as np

from . import calibration
from .fitting import KIT, lie_detail

sys.path.insert(0, str(KIT / "camera"))
from ball_line import tilt_deg  # noqa: E402

MIN_SHOTS, MAX_SHOTS = 3, 10
MAX_ABS_TILT = 40.0                  # beyond this the marked points aren't a transferred line on a face
SPREAD_WARN_DEG = 8.0                # shot-to-shot spread (1 SD) that makes the average shaky
# fallback if calibration.json has no fitted model: the kit's rule of thumb, 1 deg of tilt ~ 1 deg of lie
_FALLBACK = {"coef_static": 1.0, "coef_tilt": -1.0, "intercept": 0.0, "resid_sd_deg": 1.0,
             "dev_with_ball_line": None, "dev_static_only": None}


class BallLineError(ValueError):
    pass


def tilt(line_bottom, line_top, heel, toe) -> float:
    pts = [line_bottom, line_top, heel, toe]
    if any(len(p) != 2 for p in pts):
        raise BallLineError("Each point needs an x and a y.")
    if np.hypot(toe[0] - heel[0], toe[1] - heel[1]) < 20 or np.hypot(line_top[0] - line_bottom[0], line_top[1] - line_bottom[1]) < 10:
        raise BallLineError("The points are too close together. Tap both ends of the line and both ends of the leading edge.")
    t = tilt_deg(line_bottom, line_top, heel, toe)
    if abs(t) > MAX_ABS_TILT:
        raise BallLineError(f"A tilt of {t:.0f} degrees isn't a ball line on the face. Check the four points.")
    return t


def model(measured_body: bool) -> dict:
    m = (calibration.DATA.get("ball_line") or {}).get("true" if measured_body else "self")
    return m or _FALLBACK


def estimate(tilts: list[float], height_cm: float, wrist_to_floor_cm: float, measured_body: bool) -> dict:
    t = np.asarray(tilts, float)
    if len(t) < MIN_SHOTS:
        raise BallLineError(f"Use at least {MIN_SHOTS} shots: one shot's line is too noisy to trust.")
    m = model(measured_body)
    static = lie_detail(height_cm, wrist_to_floor_cm)["raw"]
    mean, sd = float(t.mean()), float(t.std(ddof=1)) if len(t) > 1 else 0.0
    need = m["coef_static"] * static + m["coef_tilt"] * mean + m["intercept"]
    lie = int(np.clip(np.round(need), -3, 3))
    lean = ("toward the toe: the club sat toe-up at impact, which points flatter" if mean > 0.5 else
            "toward the heel: the club sat toe-down at impact, which points more upright" if mean < -0.5 else
            "about straight up: the club sat level at impact")
    warnings = []
    if sd > SPREAD_WARN_DEG:
        warnings.append(f"Your shots varied a lot ({sd:.0f} degrees between them). A few more shots would steady the average.")
    acc = m.get("dev_with_ball_line")
    text = (f"Your {len(t)} ball-line shots leaned {abs(mean):.1f} degrees on average {lean}. Together with your height and "
            f"wrist-to-floor (chart {static:+.1f} degrees) that points to a lie of {lie:+d} degrees.")
    return {"shots": len(t), "mean_tilt_deg": round(mean, 1), "spread_deg": round(sd, 1), "static_raw": round(static, 2),
            "need_deg": round(float(need), 2), "lie_deg": lie, "measured_body": measured_body,
            "edge_deg": round(abs(need - (np.floor(need) + 0.5)), 2), "accuracy": acc, "warnings": warnings, "text": text}
