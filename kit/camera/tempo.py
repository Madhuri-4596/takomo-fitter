"""Swing tempo from a wrist (or hands) trajectory.

Input: per-frame vertical image position of the lead wrist (pixels, y down) and
the frame rate. Output: takeaway, top and impact frames, backswing and
downswing times and the backswing:downswing ratio (tour players cluster near
3:1, per John Novosel's Tour Tempo research).

The trajectory can come from any pose model (MediaPipe Pose landmark 15/16,
RTMPose, YOLO-pose ...) run on a face-on video.
  python camera/tempo.py --csv wrist.csv --fps 240     (columns: frame, y)
"""
from __future__ import annotations

import argparse
import json

import numpy as np


def _smooth(y, k):
    if k <= 1:
        return y
    w = np.ones(k) / k
    return np.convolve(np.pad(y, (k // 2, k - 1 - k // 2), mode="edge"), w, mode="valid")


def tempo_from_wrist_y(y, fps, still_px=None):
    """Find takeaway, top of backswing and impact from the lead wrist's image height.

    Top = the first high point after takeaway that is a local extreme over
    +-0.15 s (so a high finish is not mistaken for the top). Impact = the lowest
    hand position within 0.6 s after the top (hands need not return exactly to
    address height).
    """
    y = np.asarray(y, float)
    k = max(1, int(round(fps / 60)))
    ys = _smooth(y, k)
    n0 = max(3, int(0.15 * fps))
    address = np.median(ys[:n0])
    span = address - ys.min()
    if span <= 0:
        raise ValueError("no backswing found (wrist never rises)")
    thr = still_px if still_px is not None else 0.04 * span
    moving = np.where(address - ys > thr)[0]
    if not len(moving):
        raise ValueError("no backswing found")
    takeaway = int(moving[0])
    while takeaway > 0 and address - ys[takeaway - 1] > 0.005 * span:
        takeaway -= 1
    w = max(2, int(0.15 * fps))
    top = None
    for i in range(takeaway + 1, len(ys) - 1):
        lo, hi = max(takeaway, i - w), min(len(ys), i + w + 1)
        if ys[i] <= ys[lo:hi].min() and address - ys[i] > 0.5 * span:
            top = i
            break
    if top is None:
        top = int(np.argmin(ys))
    win = ys[top: min(len(ys), top + int(0.6 * fps) + 1)]
    impact = int(top + np.argmax(win))
    back = (top - takeaway) / fps
    down = (impact - top) / fps
    return {"takeaway_frame": takeaway, "top_frame": top, "impact_frame": impact,
            "backswing_s": round(back, 4), "downswing_s": round(down, 4),
            "ratio": round(back / down, 2) if down > 0 else None,
            "frame_uncertainty_s": round(1 / fps, 4)}


def main(argv=None):
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--fps", type=float, required=True)
    a = ap.parse_args(argv)
    d = pd.read_csv(a.csv)
    print(json.dumps(tempo_from_wrist_y(d["y"].to_numpy(), a.fps), indent=2))


if __name__ == "__main__":
    main()
