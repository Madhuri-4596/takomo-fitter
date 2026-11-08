"""Entered-height scale (no paper, no image upload) and the 'check only when needed' rule."""
import pytest
from fastapi.testclient import TestClient

from app import fitting, photo
from app.server import app

client = TestClient(app)
ANDERS = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
          "speed_or_carry": "carry", "carry_yd": 145, "speed_mph": None, "characteristics": ["distance control", "more offset"],
          "flight": "mid", "tempo": "smooth", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}


def test_ratio_times_entered_height():
    # head at y=100, floor at y=1872 (1772 px), wrist 849 px above the floor -> 849/1772 of the height
    m = photo.measure_with_height([500, 100], [620, 1023], [500, 1872], 177.2)
    assert m["wrist_to_floor_cm"] == pytest.approx(84.9, abs=0.05)
    assert m["height_cm"] == 177.2 and m["calibration"] == "entered_height" and m["assumed_error_cm"] == 3.0
    assert not m["warnings"]


def test_measured_along_the_body_axis_not_the_image_vertical():
    # the same body leaning 5 degrees in the image gives the same ratio
    import math
    a = math.radians(5)
    rot = lambda x, y: [x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)]
    m = photo.measure_with_height(rot(500, 100), rot(500, 1023), rot(500, 1872), 177.2)
    assert m["wrist_to_floor_cm"] == pytest.approx(84.9, abs=0.05)
    assert not m["warnings"]


def test_tilted_camera_and_odd_ratio_warn():
    m = photo.measure_with_height([300, 100], [560, 1023], [700, 1872], 177.2)        # ~13 degrees
    assert any("tilted" in w for w in m["warnings"])
    m = photo.measure_with_height([500, 100], [500, 1300], [500, 1872], 177.2)        # 32 % of height
    assert any("crease" in w for w in m["warnings"])


def test_barefoot_capture_adds_shoe_allowance():
    m = photo.measure_with_height([500, 100], [500, 1023], [500, 1872], 175.0, footwear="barefoot")
    assert m["height_cm"] == 177.5 and m["wrist_to_floor_cm"] == pytest.approx(175 * 849 / 1772 + 2.5, abs=0.05)


def test_endpoint_takes_points_only_and_validates():
    r = client.post("/api/photo/measure-height", json={"head_top": [500, 100], "wrist": [500, 1023], "floor": [500, 1872], "height_cm": 177.2})
    assert r.status_code == 200 and r.json()["wrist_to_floor_cm"] == pytest.approx(84.9, abs=0.05)
    r = client.post("/api/photo/measure-height", json={"head_top": [500, 1872], "wrist": [500, 1023], "floor": [500, 100], "height_cm": 177.2})
    assert r.status_code == 422 and "wrong order" in r.json()["detail"]
    r = client.post("/api/photo/measure-height", json={"head_top": [500, 100], "wrist": [500, 1023], "floor": [500, 1872], "height_cm": 80})
    assert r.status_code == 422


def test_tape_check_only_when_error_could_flip_stock_or_custom():
    measured = {"height_cm": 177.8, "wrist_to_floor_cm": 84.3}
    lie = fitting.lie_detail(177.8, 84.3)
    assert lie["lie"] == 0 and 2.0 < lie["edge_cm"] < 3.0
    by_height = fitting.compare(ANDERS, measured, 3.0, "entered_height")
    assert by_height["after"]["best"]["purchase"] == "stock"
    assert by_height["confidence"]["level"] == "check needed" and by_height["confidence"]["tape_check"]
    assert by_height["confidence"]["text"] == ("Estimated using your entered height and the camera image. This result is close "
                                               "to a fitting boundary. Please check with a tape before ordering custom.")
    assert by_height["boundary"]["could_flip_purchase"] and by_height["boundary"]["alt_purchase"] == "custom"
    by_sheet = fitting.compare(ANDERS, measured, 2.0, "printed_sheet")
    assert by_sheet["confidence"]["level"] == "confident" and not by_sheet["confidence"]["tape_check"]


def test_close_call_without_purchase_flip_is_a_soft_note():
    # oversize grip: custom either way, so a lie near a boundary can't flip stock/custom
    measured = {"height_cm": 177.8, "wrist_to_floor_cm": 84.3}
    r = fitting.compare(dict(ANDERS, glove="XL"), measured, 3.0, "entered_height")
    assert r["after"]["best"]["purchase"] == "custom"
    assert r["confidence"]["level"] == "close call" and not r["confidence"]["tape_check"]


def test_session_uses_the_method_error_and_tells_the_agent():
    sid = client.post("/api/session").json()["session_id"]
    h = {"X-Session-Token": sid}
    client.put("/api/session/profile", headers=h, json={"values": ANDERS})
    client.post("/api/session/measurement", headers=h, json={"height_cm": 177.8, "wrist_to_floor_cm": 84.3, "source": "photo",
                                                              "detail": {"calibration": "entered_height"}})
    client.post("/api/session/measurement/confirm", headers=h)
    tool = client.post("/api/session/recommendation", headers=h).json()["tool_result"]
    assert tool["spec"]["build"] == "stock"
    assert "could change stock or custom" in tool["close_calls"][0]
    assert tool["next_useful_check"].startswith("Estimated using your entered height")
