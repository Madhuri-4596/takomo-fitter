"""Swing tempo from a face-on swing video, using the kit's camera/tempo.py.

The browser tracks the hands with MediaPipe Pose (on device) and sends only the hand-height
track: frame times and image heights. Here it is resampled onto an even time grid and passed
to the kit's tempo_from_wrist_y, which finds takeaway, top and impact. Tempo is classified by
the kit's data-dictionary rule on total swing time (quick < 1.05 s, smooth > 1.35 s).

Tempo is evidence for the shaft model only (TEMPO_TO_PROFILE in the kit's baseline).
"""
from __future__ import annotations

import sys

import numpy as np

from .fitting import KIT

sys.path.insert(0, str(KIT / "camera"))
from tempo import tempo_from_wrist_y  # noqa: E402

QUICK_BELOW_S, SMOOTH_ABOVE_S = 1.05, 1.35        # kit docs/DATA_DICTIONARY.md: true_tempo from total swing time
MIN_FRAMES, MIN_DETECTION = 15, 0.6
LOW_FPS = 25.0                                    # prototype: one frame is then > 40 ms of a ~0.3 s downswing
# The kit's takeaway detector triggers at 4 % of the swing height, so a smooth takeaway reads ~0.05-0.07 s late
# (the kit's own test allows 0.08 s). Totals this close to a class line are reported as close calls.
BOUNDARY_MARGIN_S = 0.06


# Single-frame pose estimates jitter by ~1-2 % of the frame height; near the top of the swing one noisy
# frame can look higher than the real top and move it early. A 3-frame median removes single-frame spikes
# and moves a peak by at most one frame (the kit's own smoothing is one frame at 30 fps).
SPIKE_FRAMES = 3


def _median(y: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return y
    pad = np.pad(y, (k // 2, k // 2), mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)


class SwingError(ValueError):
    pass


def analyse(t_ms, y, visibility=None, time_scale: float = 1.0, x=None, body_px: float | None = None,
            height_cm: float | None = None) -> dict:
    t = np.asarray(t_ms, float) / 1000.0 * time_scale
    y = np.asarray(y, float)
    vis = np.ones_like(y) if visibility is None else np.asarray(visibility, float)
    if len(t) != len(y) or len(vis) != len(y):
        raise SwingError("The hand track is incomplete.")
    if len(t) < MIN_FRAMES:
        raise SwingError("The video is too short. Record one full swing, from address to finish.")
    order = np.argsort(t)
    xo = np.asarray(x, float)[order] if x is not None and len(x) == len(order) else None   # sideways track, same order
    t, y, vis = t[order] - t[order][0], y[order], vis[order]
    seen = (vis >= 0.5) & np.isfinite(y)
    detection = float(seen.mean())
    if detection < MIN_DETECTION:
        raise SwingError("We lost your hands in too many frames. Film face-on with your whole body and the club in view.")
    dts = np.diff(t[seen])
    dts = dts[dts > 0]
    if not len(dts):
        raise SwingError("The video frames have no usable timing.")
    fps = float(min(240.0, max(10.0, 1.0 / np.median(dts))))
    grid = np.arange(t[seen][0], t[seen][-1], 1.0 / fps)
    ys = _median(np.interp(grid, t[seen], y[seen]), SPIKE_FRAMES)
    try:
        r = tempo_from_wrist_y(ys, fps)
    except ValueError as e:
        raise SwingError(f"We couldn't find a full swing in the video ({e}). Record one swing from address to finish.")
    back, down = r["backswing_s"], r["downswing_s"]
    total = back + down
    tempo = "quick" if total < QUICK_BELOW_S else "smooth" if total > SMOOTH_ABOVE_S else "moderate"
    warnings = []
    if fps < LOW_FPS:
        warnings.append(f"Low frame rate ({fps:.0f} fps): each frame is {1000 / fps:.0f} ms, a big share of a downswing. "
                        "60 fps or more is better.")
    if detection < 0.85:
        warnings.append(f"Your hands were tracked in {detection:.0%} of frames; the timing is less certain.")
    near = [b for b in (QUICK_BELOW_S, SMOOTH_ABOVE_S) if abs(total - b) <= BOUNDARY_MARGIN_S]
    if near:
        other = ("quick" if tempo == "moderate" else "moderate") if near[0] == QUICK_BELOW_S else ("smooth" if tempo == "moderate" else "moderate")
        warnings.append(f"{total:.2f} s is close to the {near[0]} s line between {tempo} and {other}; a second swing would settle it.")
    if r["ratio"] and not 2.0 <= r["ratio"] <= 5.0:
        warnings.append(f"A backswing-to-downswing ratio of {r['ratio']}:1 is unusual (most golfers are 3 to 4). "
                        "Check the video shows one normal swing.")
    text = (f"Backswing {back:.2f} s, downswing {down:.2f} s (ratio {r['ratio']}:1), {total:.2f} s in total: "
            f"a {tempo} tempo by the kit's rule (quick under {QUICK_BELOW_S} s, smooth over {SMOOTH_ABOVE_S} s).")
    side = handedness_from_track(t, xo, y, seen, grid, r, body_px)
    return {**r, "total_s": round(total, 3), "tempo": tempo, "fps": round(fps, 1), "frames": int(len(t)),
            "handedness_seen": side,
            "detection_rate": round(detection, 2), "time_scale": time_scale, "text": text, "warnings": warnings,
            "uncertainty_s": round(2 / fps, 3),
            "speed_experimental": speed_from_track(t_ms, x, y, visibility, time_scale, r["top_frame"], r["impact_frame"],
                                                   grid, fps, body_px, height_cm),
            "track": {"t": [round(v, 3) for v in grid.tolist()], "y": [round(v, 1) for v in ys.tolist()]}}


def handedness_from_track(t, x, y, seen, grid, r, body_px) -> str | None:
    """Face-on, the hands go back toward the golfer's trailing side. The golfer faces the camera, so a right-hander's
    hands move to the image's LEFT (smaller x in the camera's own pixels; the preview mirror is display only).
    Returns None unless the move is clear: a third of the body length, or 12% of the hands' rise, sideways."""
    if x is None:
        return None
    xs_raw = np.asarray(x, float)
    if len(xs_raw) != len(t) or not seen.any():
        return None
    xs = np.interp(grid, t[seen], xs_raw[seen])
    a, top = r["takeaway_frame"], r["top_frame"]
    if a < 1 or top <= a or top >= len(xs):
        return None
    x0, x1 = float(np.median(xs[:a])), float(xs[top])
    ys = np.interp(grid, t[seen], y[seen])
    rise = float(np.median(ys[:a]) - ys[top])
    scale = max(0.33 * body_px if body_px else 0.0, 0.12 * rise, 20.0)
    dx = x1 - x0
    if abs(dx) < scale:
        return None
    return "right" if dx < 0 else "left"


def _classify(back: float, down: float) -> tuple[str, list[str]]:
    total = back + down
    tempo = "quick" if total < QUICK_BELOW_S else "smooth" if total > SMOOTH_ABOVE_S else "moderate"
    warnings = []
    near = [b for b in (QUICK_BELOW_S, SMOOTH_ABOVE_S) if abs(total - b) <= BOUNDARY_MARGIN_S]
    if near:
        other = ("quick" if tempo == "moderate" else "moderate") if near[0] == QUICK_BELOW_S else ("smooth" if tempo == "moderate" else "moderate")
        warnings.append(f"{total:.2f} s is close to the {near[0]} s line between {tempo} and {other}.")
    return tempo, warnings


def from_events(takeaway_s: float, top_s: float, impact_s: float, fps: float) -> dict:
    """Tempo from takeaway, top and impact times the golfer placed (or corrected) on the video, in real seconds."""
    if not takeaway_s < top_s < impact_s:
        raise SwingError("The markers must be in order: takeaway, then top, then impact.")
    back, down = top_s - takeaway_s, impact_s - top_s
    if not (0.4 <= back <= 2.5 and 0.12 <= down <= 0.8):
        raise SwingError(f"Backswing {back:.2f} s and downswing {down:.2f} s don't look like one normal swing. "
                         "Check the markers, or retake the swing.")
    tempo, warnings = _classify(back, down)
    ratio = round(back / down, 1)
    return {"backswing_s": round(back, 3), "downswing_s": round(down, 3), "ratio": ratio, "total_s": round(back + down, 3),
            "tempo": tempo, "fps": round(float(fps), 1), "warnings": warnings, "adjusted": True,
            "text": f"Backswing {back:.2f} s, downswing {down:.2f} s (ratio {ratio}:1), {back + down:.2f} s in total: "
                    f"a {tempo} tempo by the kit's rule (quick under {QUICK_BELOW_S} s, smooth over {SMOOTH_ABOVE_S} s)."}


def combine(swings: list[dict]) -> dict:
    """Several accepted swings -> one tempo: the median backswing and downswing, classified by the kit's rule."""
    if not swings:
        raise SwingError("No swings accepted yet.")
    backs = np.array([float(s["backswing_s"]) for s in swings])
    downs = np.array([float(s["downswing_s"]) for s in swings])
    for b, d in zip(backs, downs):
        if not (0.2 <= b <= 3.0 and 0.08 <= d <= 1.0):
            raise SwingError("One of the swings has impossible timing. Retake it.")
    back, down = float(np.median(backs)), float(np.median(downs))
    tempo, warnings = _classify(back, down)
    totals = backs + downs
    spread = float(totals.max() - totals.min()) if len(totals) > 1 else 0.0
    per = [("quick" if t < QUICK_BELOW_S else "smooth" if t > SMOOTH_ABOVE_S else "moderate") for t in totals]
    if len(set(per)) > 1:
        warnings.append(f"Your swings fell in different tempo groups ({', '.join(per)}); the middle one is used.")
    if len(swings) < 3:
        warnings.append(f"Based on {len(swings)} swing{'s' if len(swings) > 1 else ''}; three give a steadier reading.")
    ratio = round(back / down, 1)
    fps = min(float(s.get("fps") or 30) for s in swings)
    n = len(swings)
    text = (f"Median of {n} swing{'s' if n > 1 else ''}: backswing {back:.2f} s, downswing {down:.2f} s (ratio {ratio}:1), "
            f"{back + down:.2f} s in total: a {tempo} tempo by the kit's rule (quick under {QUICK_BELOW_S} s, smooth over {SMOOTH_ABOVE_S} s).")
    return {"backswing_s": round(back, 3), "downswing_s": round(down, 3), "ratio": ratio, "total_s": round(back + down, 3),
            "tempo": tempo, "fps": round(fps, 1), "swings": n, "spread_s": round(spread, 3),
            "adjusted": any(s.get("adjusted") for s in swings), "per_swing_total_s": [round(float(t), 3) for t in totals],
            "speed_experimental": combine_speed(swings),
            "warnings": warnings, "text": text}


# ------------------------------------------------------------------ EXPERIMENTAL: clubhead speed range from hand speed
# Not validated and never used by the fitting engine. Shown as a wide range next to the real speed source.
# Scale: shoulder-to-ankle length in the video ~ 0.78 x stature (Drillis & Contini body-segment proportions:
# shoulder height 0.818 H, ankle 0.039 H). Clubhead speed ~ hand speed x 3.0-4.0 for a 7-iron: an ASSUMED lever
# ratio that varies with each golfer's release. Face-on video misses motion towards the camera and pose tracking
# lags fast hands, so the hand speed is likely under-read, most at low frame rates.
SHOULDER_ANKLE_FRAC = 0.818 - 0.039
CLUB_HAND_RATIO = (3.0, 4.0)
CM_S_PER_MPH = 44.704
SPEED_MIN_FPS = 50.0
HAND_MPH_MIN = 10.0                 # below this the clip isn't a full swing, or tracking lost the hands


def hand_speed(t_s: np.ndarray, x: np.ndarray, y: np.ndarray, seen: np.ndarray, top_s: float, impact_s: float,
               cm_per_px: float) -> float | None:
    """Peak hand speed (mph) between the top and just after impact, from frame-to-frame motion of the hands."""
    idx = np.where(seen & (t_s >= top_s - 1e-6) & (t_s <= impact_s + 0.05))[0]
    if len(idx) < 3:
        return None
    xs, ys, ts = _median(x[idx], 3), _median(y[idx], 3), t_s[idx]
    dt = np.diff(ts)
    ok = dt > 0
    if not ok.any():
        return None
    v = np.hypot(np.diff(xs), np.diff(ys))[ok] / dt[ok] * cm_per_px / CM_S_PER_MPH
    return float(np.max(v))


def speed_range(hand_mph: float | None, fps: float, note: str | None = None) -> dict:
    if not hand_mph or hand_mph > 45:
        return {"available": False, "reason": note or "The hands couldn't be followed clearly enough through the downswing."}
    if hand_mph < HAND_MPH_MIN:
        return {"available": False, "reason": f"The hands only reached about {hand_mph:.0f} mph, too slow for a full swing "
                                              "(usually 15 to 25 mph). Check the video shows one full-speed swing, face-on."}
    lo, hi = (5 * round(hand_mph * r / 5) for r in CLUB_HAND_RATIO)
    warn = []
    if fps < SPEED_MIN_FPS:
        warn.append(f"At {fps:.0f} fps the fastest part of the downswing falls between frames, so this probably reads low.")
    return {"available": True, "experimental": True, "hand_mph": round(hand_mph, 1), "club_mph_low": lo, "club_mph_high": hi,
            "used_by_fit": False, "warnings": warn,
            "text": f"Experimental: your hands reached about {hand_mph:.0f} mph, which suggests a 7-iron club speed of roughly "
                    f"{lo}–{hi} mph. Not used for your fit: the link between hand and club speed differs a lot between "
                    f"golfers, and a phone camera misses some of the motion. A simulator or launch monitor gives the real number."}


def speed_from_track(t_ms, x, y, visibility, time_scale: float, top_frame: int, impact_frame: int, grid: np.ndarray,
                     fps: float, body_px: float | None, height_cm: float | None) -> dict:
    if not (body_px and height_cm and x is not None):
        return speed_range(None, fps, "Needs your height and your whole body in view to set the scale.")
    t = np.asarray(t_ms, float) / 1000.0 * time_scale
    order = np.argsort(t)
    t = t[order] - t[order][0]
    xs, ys = np.asarray(x, float)[order], np.asarray(y, float)[order]
    vis = np.ones_like(ys) if visibility is None else np.asarray(visibility, float)[order]
    seen = (vis >= 0.5) & np.isfinite(xs) & np.isfinite(ys)
    cm_per_px = height_cm * SHOULDER_ANKLE_FRAC / body_px
    top_s, impact_s = grid[min(top_frame, len(grid) - 1)], grid[min(impact_frame, len(grid) - 1)]
    return speed_range(hand_speed(t, xs, ys, seen, top_s, impact_s, cm_per_px), fps)


def combine_speed(swings: list[dict]) -> dict | None:
    hands = [float(s["hand_mph"]) for s in swings if s.get("hand_mph")]
    if not hands:
        return None
    fps = min(float(s.get("fps") or 30) for s in swings)
    return speed_range(float(np.median(hands)), fps)
