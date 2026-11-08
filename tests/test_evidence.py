"""Evidence features: strike test, game description, head comparison, per-decision evidence."""
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import evidence, fitting
from app.server import app

client = TestClient(app)
HEEL, TOE = (200.0, 300.0), (800.0, 300.0)      # 600 px = 60 mm scoring lines: 10 px per mm
GOLFER = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
          "speed_or_carry": "carry", "carry_yd": 145, "speed_mph": None, "characteristics": ["forgiveness"],
          "flight": "mid", "tempo": "moderate", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}


def face(offsets_mm, angle=0.0):
    """Synthetic clubface with impact tape, grooves and dark strike marks at known heel-toe offsets."""
    img = np.full((600, 1000, 3), (70, 70, 75), np.uint8)
    cv2.rectangle(img, (150, 170), (850, 430), (225, 225, 225), -1)          # tape
    for y in range(200, 420, 22):
        cv2.line(img, (200, y), (800, y), (150, 150, 150), 2)                # grooves
    truth = []
    for i, off in enumerate(offsets_mm):
        x, y = 500 + off * 10, 290 + (i % 3 - 1) * 25
        cv2.ellipse(img, (int(x), int(y)), (26, 20), 0, 0, 360, (60, 60, 60), -1)
        truth.append((x, y))
    if angle:
        M = cv2.getRotationMatrix2D((500, 300), angle, 1)
        img = cv2.warpAffine(img, M, (1000, 600), borderMode=cv2.BORDER_REPLICATE)
        rot = lambda p: (M @ np.array([p[0], p[1], 1.0])).tolist()
        return img, [rot(t) for t in truth], rot(HEEL), rot(TOE)
    return img, truth, HEEL, TOE


@pytest.mark.parametrize("angle", [0.0, 12.0])
def test_detects_marks_not_grooves(angle):
    img, truth, heel, toe = face([-16, -7, 0, 8, 17], angle)   # spaced: touching marks merge (the golfer can add one)
    found = evidence.detect_marks(img, heel, toe)
    assert len(found) == 5
    for t in truth:
        assert min(np.hypot(f[0] - t[0], f[1] - t[1]) for f in found) < 6


def test_classification_from_kit_simulated_thresholds():
    tight = evidence.analyse_marks([(500 + o * 10, 300) for o in (-3, -1, 0, 2, 3)], HEEL, TOE)
    wide = evidence.analyse_marks([(500 + o * 10, 300) for o in (-18, -6, 2, 12, 22)], HEEL, TOE)
    toe = evidence.analyse_marks([(500 + o * 10, 300) for o in (6, 8, 9, 10, 12)], HEEL, TOE)
    assert tight["pattern"] == "clustered" and tight["bias"] == "centre"
    assert wide["pattern"] == "spread" and "spread widely" in wide["text"]
    assert toe["bias"] == "toe" and toe["mean_mm"] == pytest.approx(9.0) and "too short" in toe["notes"][0]
    with pytest.raises(evidence.StrikeError, match="at least 3"):
        evidence.analyse_marks([(500, 300)], HEEL, TOE)
    with pytest.raises(evidence.StrikeError, match="outside the grooves"):
        evidence.analyse_marks([(500, 300), (510, 300), (1300, 300)], HEEL, TOE)


def test_flight_words_follow_handedness():
    rh = evidence.flight_description({"handedness": "right", "ball_start": "left", "ball_curve": "right", "ball_height": "lower"})
    lh = evidence.flight_description({"handedness": "left", "ball_start": "left", "ball_curve": "right", "ball_height": "as_wanted"})
    assert rh == "The ball usually starts left, curves right (fades or slices for a right-handed golfer), flies lower than you want."
    assert "draws or hooks for a left-handed golfer" in lh and "flies" not in lh


def test_player_summary_describes_without_labels():
    s = evidence.player_summary(dict(GOLFER, score_representative="several", lose_shots="irons"), None)
    assert s == ("You usually score 85–92 (over several recent rounds), you lose most shots with your irons, "
                 "you report inconsistent iron contact, and you want more help on mishits.")
    assert "beginner" not in s.lower() and "intermediate" not in s.lower()


def test_head_comparison_and_fitter_review():
    result = fitting.recommend(fitting.answers_from_form(GOLFER))
    c = evidence.head_comparison(result, GOLFER, None)
    assert c["best"] == result["best"]["head_name"] and c["alternative"] == result["runner_up"]["head_name"]
    assert "still need to check" in c["contact"] and c["fitter_review"] is None
    assert c["inputs"] and c["tradeoff"]
    # a players' iron with a wide strike pattern -> fitter review
    pro = dict(GOLFER, score_bucket=1, speed_or_carry="speed", speed_mph=92, characteristics=["workability", "forged feel"])
    r = fitting.recommend(fitting.answers_from_form(pro))
    assert r["best"]["head"] in ("301-cb", "301-mb")
    spread = evidence.analyse_marks([(500 + o * 10, 300) for o in (-18, -6, 2, 12, 22)], HEEL, TOE)
    assert "fitter" in evidence.head_comparison(r, pro, spread)["fitter_review"]


def test_evidence_map_marks_carry_as_fallback():
    result = fitting.recommend(fitting.answers_from_form(GOLFER))
    rows = {r["decision"]: r for r in evidence.evidence_map(GOLFER, None, None, None, result)}
    assert set(rows) == {"Iron head", "Shaft flex", "Shaft model", "Length and lie", "Grip"}
    assert rows["Shaft flex"]["evidence"][0]["strength"].startswith("fallback")
    assert any("launch monitor" in i for i in rows["Shaft flex"]["improve"])
    assert any("strike test" in i for i in rows["Iron head"]["improve"])
    assert any("photo step" in i for i in rows["Length and lie"]["improve"])


def test_session_strike_explains_but_does_not_change_the_fit():
    sid = client.post("/api/session").json()["session_id"]
    h = {"X-Session-Token": sid}
    client.put("/api/session/profile", headers=h, json={"values": dict(GOLFER, lose_shots="irons", ball_start="nonsense")})
    st = client.get("/api/session", headers=h).json()
    assert st["profile"]["lose_shots"] == "irons" and "ball_start" not in st["profile"]      # invalid enum ignored
    rec = client.post("/api/session/recommendation", headers=h).json()["state"]["full_recommendation"]
    img, truth, heel, toe = face([-18, -6, 2, 12, 22])
    ok, buf = cv2.imencode(".png", img)
    found = client.post("/api/strike/detect", files={"image": ("f.png", buf.tobytes(), "image/png")},
                        data={"heel": json.dumps(heel), "toe": json.dumps(toe)}).json()["marks"]
    assert len(found) == 5
    st = client.post("/api/session/strike", headers=h, json={"marks": found, "heel": heel, "toe": toe}).json()
    assert st["evidence"]["strike_test"]["pattern"] == "spread"
    assert "inconsistent iron contact" in st["evidence"]["player_summary"]
    assert st["full_recommendation"]["id"] == rec["id"] and not st["recommendation"]["stale"]
    # voice can give the new answers too
    r = client.post("/api/session/propose", headers=h, json={"updates": [{"field": "ball_curve", "value": "right"},
                                                                         {"field": "lose_shots", "value": "the moon"}]}).json()
    assert r["results"][0]["status"] == "staged_for_review" and r["results"][1]["status"] == "needs_clarification"


def test_confidence_levels_come_from_the_kit_data():
    """Levels are looked up from accuracy measured on the training split (tools/calibrate.py)."""
    from app import calibration
    assert calibration.MEASURED and calibration.DATA["line_accuracy"]
    bias, sd = calibration.self_report("carry_yd")
    assert bias > 5 and sd > 3                              # golfers over-report carry
    form = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
            "speed_or_carry": "carry", "carry_yd": 150, "characteristics": ["forgiveness"], "flight": "mid",
            "tempo": "moderate", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}
    c = evidence.verdict_confidence(form, None, None, None)
    assert c["lines"]["Shaft"]["level"] != "High"           # flex from carry is never High on the kit data
    assert c["lines"]["Lie"]["level"] == "Low"              # self-measured wrist-to-floor
    assert "times in 10" in (c["lines"]["Lie"]["why"] or "")
    lo, hi = calibration.strike_thresholds()
    assert evidence.STRIKE_SD_CLUSTERED_MM == lo and evidence.STRIKE_SD_SPREAD_MM == hi


def test_trust_summary_is_honest():
    """The fit says how sure it is, what was measured vs answered, and what's least certain."""
    from fastapi.testclient import TestClient
    from app.server import app
    c = TestClient(app)
    h = {"X-Session-Token": c.post("/api/session").json()["session_id"]}
    form = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
            "speed_or_carry": "carry", "carry_yd": 150, "characteristics": ["forgiveness"], "flight": "mid",
            "tempo": "moderate", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}
    c.put("/api/session/profile", headers=h, json={"values": form})
    t = c.post("/api/session/recommendation", headers=h).json()["state"]["evidence"]["trust"]
    assert sum(t["counts"].values()) == 6
    assert t["measured"] == [] and "7-iron carry" in t["from_answers"] and "height and wrist-to-floor" in t["from_answers"]
    assert t["least_certain"]["part"] in ("lie", "shaft flex", "shaft model", "head", "length", "grip")
    assert t["tested_on_held_out"]["High"]["in_10"] >= t["tested_on_held_out"]["Low"]["in_10"]
    assert "simulated" in t["basis"]
    c.post("/api/session/measurement", headers=h, json={"height_cm": 178, "wrist_to_floor_cm": 86, "source": "tape"})
    c.post("/api/session/measurement/confirm", headers=h)
    t = c.post("/api/session/recommendation", headers=h).json()["state"]["evidence"]["trust"]
    assert t["measured"] and "tape measure" in t["measured"][0]
