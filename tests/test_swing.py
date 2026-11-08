"""Swing-video tempo: the kit's tempo_from_wrist_y on browser hand tracks, and its effect on the shaft."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import evidence, swing
from app.server import app

client = TestClient(app)
ANDERS = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
          "speed_or_carry": "speed", "speed_mph": 75, "carry_yd": None, "characteristics": ["distance control", "more offset"],
          "flight": "mid", "tempo": "smooth", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}


def hand_y(t, back, down, t0=0.5):
    """Image height of the hands (px, y down): address, backswing up, downswing to impact, higher finish."""
    ease = lambda u: 0.5 - 0.5 * math.cos(math.pi * min(1, max(0, u)))
    addr, top, finish = 500.0, 300.0, 270.0
    if t < t0:
        return addr
    if t < t0 + back:
        return addr - (addr - top) * ease((t - t0) / back)
    if t < t0 + back + down:
        return top + (addr - top) * ease((t - t0 - back) / down)
    return addr - (addr - finish) * ease((t - t0 - back - down) / 0.35)


def track(back, down, fps=30.0, jitter=0.004, seconds=None, seed=0):
    rng = np.random.default_rng(seed)
    n = int((seconds or (0.5 + back + down + 0.8)) * fps)
    t = np.arange(n) / fps + rng.uniform(-jitter, jitter, n)          # browsers deliver slightly uneven frame times
    return (t * 1000).tolist(), [hand_y(x, back, down) for x in t]


# kit tolerances (tests/test_camera.py): backswing 0.08 s, its takeaway threshold reads smooth takeaways late
@pytest.mark.parametrize("back,down,tempo", [(0.9, 0.3, "moderate"), (0.7, 0.25, "quick"), (1.25, 0.35, "smooth")])
def test_tempo_from_hand_track(back, down, tempo):
    t, y = track(back, down)
    r = swing.analyse(t, y)
    assert r["backswing_s"] == pytest.approx(back, abs=0.12)     # 30 fps: kit takeaway reads late + spike filter, ~2 frames
    assert r["downswing_s"] == pytest.approx(down, abs=0.04)
    assert r["tempo"] == tempo and r["fps"] == pytest.approx(30, abs=1)
    assert not r["warnings"]


def test_low_fps_and_lost_tracking_are_flagged():
    t, y = track(0.9, 0.3, fps=15)
    assert any("Low frame rate" in w for w in swing.analyse(t, y)["warnings"])
    t, y = track(0.9, 0.3)
    vis = [0.9] * len(y)
    for i in range(0, len(vis), 4):
        vis[i] = 0.1
    assert any("tracked in" in w for w in swing.analyse(t, y, vis)["warnings"])
    with pytest.raises(swing.SwingError, match="too short"):
        swing.analyse(t[:5], y[:5])
    with pytest.raises(swing.SwingError, match="full swing"):
        swing.analyse(t, [500.0] * len(t))


def test_slow_motion_time_scale():
    t, y = track(0.9, 0.3, fps=240, jitter=0.0005)                     # real swing, captured at 240 fps
    t_played = [x * 8 for x in t]                                      # decoded from a file that plays it at 30 fps
    assert swing.analyse(t_played, y)["tempo"] == "smooth"             # 8x too slow without the time scale...
    r = swing.analyse(t_played, y, time_scale=1 / 8)
    assert r["backswing_s"] == pytest.approx(0.9, abs=0.1) and r["tempo"] == "moderate"   # ...right with it


def test_close_to_a_class_line_is_flagged():
    t, y = track(0.8, 0.3)                                             # reads ~1.04 s: on the quick/moderate line
    r = swing.analyse(t, y)
    assert any("close to the 1.05 s line" in w for w in r["warnings"])


def test_tempo_changes_shaft_and_flight_height():
    e = evidence.tempo_effect(ANDERS, "quick")
    assert e["current_shaft_model"] == "KBS Tour Lite" and e["shaft_model"] == "KBS Tour"
    assert e["changes_shaft"] and e["height_change"] == "lower" and "slightly lower ball flight" in e["text"]
    same = evidence.tempo_effect(ANDERS, "smooth")
    assert not same["changes_shaft"] and "same shaft" in same["text"]
    wish = evidence.tempo_effect(dict(ANDERS, ball_height="lower"), "quick")
    assert "wanted flight" in wish["text"]


def test_api_and_session():
    t, y = track(0.7, 0.25)
    r = client.post("/api/swing/tempo", json={"t_ms": t, "y": y, "answers": ANDERS}).json()
    assert r["tempo"] == "quick" and r["tempo_effect"]["shaft_model"] == "KBS Tour" and len(r["track"]["t"]) > 20
    sid = client.post("/api/session").json()["session_id"]
    h = {"X-Session-Token": sid}
    client.put("/api/session/profile", headers=h, json={"values": ANDERS})
    st = client.post("/api/session/swing", headers=h, json={"t_ms": t, "y": y}).json()
    ev = st["evidence"]
    assert ev["swing_test"]["tempo"] == "quick" and "track" not in ev["swing_test"]
    assert st["profile"]["tempo"] == "smooth"                          # evidence never changes the answer
    assert ev["tempo_effect"]["changes_shaft"]
    model = next(d for d in ev["decisions"] if d["decision"] == "Shaft model")
    assert "suggests quick" in model["evidence"][0]["what"] and "swing video's tempo" in model["improve"][0]
    assert client.post("/api/swing/tempo", json={"t_ms": t[:5], "y": y[:5]}).status_code == 422


def test_from_events_classifies_with_kit_rule():
    r = swing.from_events(0.2, 1.1, 1.4, 60)
    assert r["backswing_s"] == pytest.approx(0.9) and r["downswing_s"] == pytest.approx(0.3)
    assert r["tempo"] == "moderate" and r["ratio"] == 3.0 and r["adjusted"]


@pytest.mark.parametrize("events", [(1.0, 0.5, 1.4), (0.0, 0.1, 0.2), (0.0, 3.0, 3.3)])
def test_from_events_rejects_unclear_swings(events):
    with pytest.raises(swing.SwingError):
        swing.from_events(*events, 60)


def test_combine_uses_median_and_flags_mixed_groups():
    r = swing.combine([{"backswing_s": 0.7, "downswing_s": 0.28}, {"backswing_s": 0.9, "downswing_s": 0.3},
                       {"backswing_s": 1.2, "downswing_s": 0.33}])
    assert r["backswing_s"] == pytest.approx(0.9) and r["tempo"] == "moderate" and r["swings"] == 3
    assert any("different tempo groups" in w for w in r["warnings"])
    one = swing.combine([{"backswing_s": 0.9, "downswing_s": 0.3}])
    assert any("three give a steadier" in w for w in one["warnings"])


def test_experimental_hand_speed_reads_a_known_speed():
    """Hands moving at 20 mph (894 cm/s) at 0.2 cm per pixel, 240 fps."""
    t = np.arange(0, 0.5, 1 / 240)
    x = 4470.0 * t                                   # px/s = 894 cm/s / 0.2 cm/px
    y = np.zeros_like(t)
    mph = swing.hand_speed(t, x, y, np.ones_like(t, bool), 0.1, 0.4, 0.2)
    assert mph == pytest.approx(20.0, abs=0.2)
    r = swing.speed_range(mph, 240)
    assert r["available"] and r["used_by_fit"] is False and (r["club_mph_low"], r["club_mph_high"]) == (60, 80)
    assert not r["warnings"]
    assert swing.speed_range(mph, 30)["warnings"]                     # low frame rate: probably reads low
    assert swing.speed_range(None, 60)["available"] is False
    assert swing.speed_range(7, 60)["available"] is False             # too slow to be a full swing


def test_experimental_speed_needs_a_scale_and_is_combined():
    t, y = track(0.9, 0.3)
    r = swing.analyse(t, y)
    assert r["speed_experimental"]["available"] is False             # no height or body length: no scale
    c = swing.combine([{"backswing_s": 0.9, "downswing_s": 0.3, "hand_mph": h} for h in (18, 20, 22)])
    assert c["speed_experimental"]["hand_mph"] == 20.0 and c["speed_experimental"]["used_by_fit"] is False
