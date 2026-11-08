"""Ball-line lie test: kit geometry, the fitted model, and how it enters the fit."""
import pytest
from fastapi.testclient import TestClient

from app import ball_line, calibration
from app.server import app

client = TestClient(app)
FULL = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
        "speed_or_carry": "speed", "speed_mph": 75, "characteristics": ["forgiveness"], "flight": "mid",
        "tempo": "moderate", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}


def test_tilt_uses_the_kit_geometry():
    heel, toe = (100, 500), (500, 500)                    # level leading edge
    assert ball_line.tilt((300, 480), (300, 380), heel, toe) == 0.0
    assert ball_line.tilt((300, 480), (320, 380), heel, toe) > 0          # top leans toward the toe
    with pytest.raises(ball_line.BallLineError):
        ball_line.tilt((300, 480), (300, 478), heel, toe)                  # line too short


def test_model_is_fitted_on_the_kit_data_and_points_the_right_way():
    m = ball_line.model(False)
    assert calibration.DATA.get("ball_line") and m["coef_tilt"] < 0      # toe-up mark -> flatter
    assert m["dev_with_ball_line"]["within_1"] > m["dev_static_only"]["within_1"]
    toe_up = ball_line.estimate([8, 10, 9], 177.8, 88.9, False)
    heel_down = ball_line.estimate([-8, -10, -9], 177.8, 88.9, False)
    assert toe_up["lie_deg"] < heel_down["lie_deg"]
    with pytest.raises(ball_line.BallLineError):
        ball_line.estimate([5, 6], 177.8, 88.9, False)                     # fewer than 3 shots


def test_ball_line_sets_the_lie_and_the_build_follows_the_kit_rule():
    h = {"X-Session-Token": client.post("/api/session").json()["session_id"]}
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    before = client.post("/api/session/recommendation", headers=h).json()["state"]["full_recommendation"]["result"]["best"]
    st = client.post("/api/session/ball-line", headers=h, json={"tilts": [12, 14, 13, 15, 12]}).json()
    r = st["full_recommendation"]["result"]
    best = r["best"]
    assert r["ball_line"]["shots"] == 5 and best["lie_deg"] == r["ball_line"]["lie_deg"]
    assert best["lie_deg"] < before["lie_deg"] or best["lie_deg"] == -3                # toe-up marks: flatter
    assert best["why"]["lie_source"] == "ball_line" and "ball-line" in best["why"]["lie"]
    assert (best["purchase"] == "custom") == bool(best["why"]["custom_reasons"])
    assert st["evidence"]["ball_line_test"]["shots"] == 5
    assert client.post("/api/session/ball-line", headers=h, json={"tilts": [1, 2]}).status_code == 422
    st = client.post("/api/session/ball-line", headers=h, json={"tilts": None}).json()
    assert "ball_line" not in st["full_recommendation"]["result"]                       # cleared: chart again


def test_lie_confidence_follows_the_ball_line_test():
    h = {"X-Session-Token": client.post("/api/session").json()["session_id"]}
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    client.post("/api/session/recommendation", headers=h)
    st = client.post("/api/session/ball-line", headers=h, json={"tilts": [2, 3, 1]}).json()
    lie = st["evidence"]["confidence"]["lines"]["Lie"]
    assert lie["key"] == "Lie|ball_line" and "ball-line shots" in lie["why"]
    assert "ball-line test" not in (lie["next_step"] or "")            # never asks for the test it already has
