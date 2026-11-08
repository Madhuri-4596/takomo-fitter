"""Error margins measured on the Takomo Fitting Lab data (tools/calibrate.py writes app/calibration.json).

Falls back to the earlier prototype assumptions when the file is missing, so the app still runs.
"""
from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).with_name("calibration.json")
_FALLBACK = {"self_report": {
    "height_cm": {"bias": 0.0, "sd": 1.0}, "wrist_to_floor_cm": {"bias": 0.0, "sd": 3.0},
    "speed_mph": {"bias": 0.0, "sd": 3.0}, "carry_yd": {"bias": 0.0, "sd": 8.0}},
    "strike": {"sd_tertile_low_mm": 6.0, "sd_tertile_high_mm": 11.0}}

try:
    DATA = json.loads(_FILE.read_text(encoding="utf-8"))
    MEASURED = True
except (OSError, ValueError):
    DATA, MEASURED = _FALLBACK, False


def self_report(key: str) -> tuple[float, float]:
    """(bias, sd) of what golfers report minus the truth: bias > 0 means they over-report."""
    d = DATA["self_report"].get(key) or _FALLBACK["self_report"][key]
    return float(d["bias"]), float(d["sd"])


def strike_thresholds() -> tuple[float, float]:
    s = DATA.get("strike") or _FALLBACK["strike"]
    return float(s["sd_tertile_low_mm"]), float(s["sd_tertile_high_mm"])


MIN_N = 40          # fewer training golfers than this in a situation: fall back to the rules


def line_accuracy(key: str) -> float | None:
    """Share of training golfers in this situation whose spec line matched the reference fit (None if too few)."""
    row = (DATA.get("line_accuracy") or {}).get(key)
    return float(row["right"]) if row and row.get("n", 0) >= MIN_N else None
