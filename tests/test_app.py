import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import fitting
from app.server import app

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "app" / "static" / "sample"
client = TestClient(app)

BASE = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
        "speed_or_carry": "speed", "speed_mph": 75, "carry_yd": None, "characteristics": ["forgiveness"],
        "flight": "mid", "tempo": "moderate", "height_cm": 177.0, "wrist_to_floor_cm": 86.0, "glove": "M"}
SPEC_KEYS = {"head", "shaft", "shaft_model", "flex", "length_in", "lie_deg", "grip", "handedness", "purchase"}


def demo(gid):
    return next(d for d in client.get("/api/demo-golfers").json() if d["id"] == gid)


def test_every_demo_golfer_gets_a_complete_buildable_spec():
    golfers = client.get("/api/demo-golfers").json()
    assert len(golfers) == 16
    products = fitting.P.load()
    for g in golfers:
        r = client.post("/api/recommend", json=g["answers"])
        assert r.status_code == 200, (g["id"], r.text)
        best = r.json()["best"]
        assert SPEC_KEYS <= best.keys()
        assert best["head"] in fitting.P.shaft(best["shaft"])["heads"]   # shaft is offered on this head
        assert best["length_in"] in products["length_options_in"]
        assert best["lie_deg"] in products["lie_options_deg"]
        assert best["purchase"] in ("stock", "custom")


def test_results_match_the_kit_engine_exactly():
    for g in client.get("/api/demo-golfers").json():
        mine = client.post("/api/recommend", json=g["answers"]).json()["best"]
        kit = fitting.B.recommend(fitting.answers_from_form(g["answers"]))[0]
        assert (mine["head"], mine["shaft"], mine["length_in"], mine["lie_deg"], mine["grip"], mine["purchase"]) == \
               (kit.head, kit.shaft, kit.length_in, kit.lie_deg, kit.grip, kit.purchase)


def test_midsize_grip_is_still_stock():
    # Takomo, 5 Nov 2026: midsize grips are a stock option; only oversize forces custom
    a = dict(BASE, glove="L", wrist_to_floor_cm=84.0)
    best = client.post("/api/recommend", json=a).json()["best"]
    assert best["grip"] == "midsize" and best["material"] == "steel"
    assert best["length_in"] == 0 and best["lie_deg"] == 0
    assert best["purchase"] == "stock"
    over = client.post("/api/recommend", json=dict(a, glove="XL")).json()["best"]
    assert over["grip"] == "oversize" and over["purchase"] == "custom"
    assert any("oversize" in r for r in over["why"]["custom_reasons"])


def test_left_handed_is_carried_through():
    best = client.post("/api/recommend", json=dict(BASE, handedness="left")).json()["best"]
    assert best["handedness"] == "left"


def test_bad_input_is_rejected():
    r = client.post("/api/recommend", json=dict(BASE, wrist_to_floor_cm=300))
    assert r.status_code == 422 and "wrist_to_floor_cm" in r.json()["detail"]
    r = client.post("/api/recommend", json=dict(BASE, characteristics=["a", "b", "c", "d"]))
    assert r.status_code == 422


def test_sample_photo_measures_close_to_its_truth():
    # the sample's truth points were placed by eye on the source portrait (+-5 px, about +-0.8 cm)
    truth = json.loads((SAMPLE / "sample-body.json").read_text())
    img = (SAMPLE / "sample-body.jpg").read_bytes()
    r = client.post("/api/photo/sheet", files={"image": ("s.jpg", img, "image/jpeg")}).json()
    assert r["ok"] and r["markers_found"] == [0, 1, 2, 3]
    p = truth["points"]
    r = client.post("/api/photo/measure", files={"image": ("s.jpg", img, "image/jpeg")},
                    data={"points": json.dumps({"head_top": p["head_top"], "wrist": p["wrist"], "floor": p["floor"]})})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["height_cm"] == pytest.approx(truth["height_cm"], abs=1.5)
    assert m["wrist_to_floor_cm"] == pytest.approx(truth["wrist_to_floor_cm"], abs=1.5)


def test_photo_without_sheet_is_explained():
    import cv2
    import numpy as np
    ok, buf = cv2.imencode(".jpg", np.full((400, 300, 3), 200, np.uint8))
    r = client.post("/api/photo/sheet", files={"image": ("x.jpg", buf.tobytes(), "image/jpeg")}).json()
    assert not r["ok"] and "0 of the 4" in r["message"]
    r = client.post("/api/photo/sheet", files={"image": ("x.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 422


def test_demo13_photo_turns_custom_into_stock():
    g = demo("DEMO-13")
    r = client.post("/api/compare", json={"answers": g["answers"], "measured": g["simulated_photo"]}).json()
    assert r["before"]["best"]["purchase"] == "custom" and r["after"]["best"]["purchase"] == "stock"
    assert {c["field"] for c in r["changes"]} == {"Lie", "Build"}
    assert any("stock set" in s for s in r["story"])
