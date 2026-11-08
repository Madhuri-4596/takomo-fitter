"""Voice-side rules: staging, clarification, confirmation, invalidation, webhook auth."""
import os

from fastapi.testclient import TestClient

from app.server import app

client = TestClient(app)

FULL = {"age": 44, "sex": "male", "handedness": "right", "rounds_per_year": "31-50", "score_bucket": 4,
        "speed_or_carry": "speed", "speed_mph": 75, "carry_yd": None, "characteristics": ["forgiveness"],
        "flight": "mid", "tempo": "moderate", "height_cm": 177.8, "wrist_to_floor_cm": 88.9, "glove": "M"}


def new():
    sid = client.post("/api/session").json()["session_id"]
    return {"X-Session-Token": sid}, sid


def propose(h, *updates):
    r = client.post("/api/session/propose", headers=h, json={"updates": list(updates)})
    assert r.status_code == 200, r.text
    return r.json()


def test_session_requires_token():
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/session", headers={"X-Session-Token": "nope"}).status_code == 401


def test_spoken_answers_are_staged_not_committed():
    h, _ = new()
    out = propose(h, {"field": "height", "value": "5 11", "unit": "ft_in"})
    assert out["results"][0]["status"] == "staged_for_review"
    assert out["results"][0]["read_back"].startswith("height 180.3 cm (5 ft 11 in)")
    st = out["state"]
    assert "height_cm" not in st["profile"] and "height" in st["staged_for_review"]
    assert "height_cm" in st["missing_fields"]
    st = client.post("/api/session/confirm", headers=h, json={"keys": ["height"]}).json()["state"]
    assert st["profile"]["height_cm"] == 180.3 and not st["staged_for_review"]


def test_ambiguous_values_ask_for_clarification():
    h, _ = new()
    cases = [
        ({"field": "seven_iron_speed", "value": "105", "unit": "mph", "kind": "ball"}, "ball speed"),
        ({"field": "seven_iron_speed", "value": "my ball speed is 110"}, "ball speed"),
        ({"field": "seven_iron_carry", "value": "160", "unit": "yards", "kind": "total"}, "carry"),
        ({"field": "seven_iron_carry", "value": "150 with roll"}, "carry"),
        ({"field": "score", "value": "79", "kind": "best"}, "usually shoot"),
        ({"field": "height", "value": "100"}, "centimetres or inches"),                  # fits neither cm nor inches
        ({"field": "height", "value": "180", "unit": "in"}, "doesn't look right"),      # misheard unit
        ({"field": "wrist_to_floor", "value": "50"}, "centimetres or inches"),
    ]
    out = propose(h, *[c for c, _ in cases])
    for (case, needle), r in zip(cases, out["results"]):
        assert r["status"] == "needs_clarification", case
        assert needle in r["ask"], (case, r["ask"])
    assert out["state"]["staged_for_review"] == {}


def test_unit_conversions_and_correction_replaces_staged_value():
    h, _ = new()
    out = propose(h, {"field": "seven_iron_speed", "value": "120", "unit": "kmh", "kind": "club"},
                  {"field": "seven_iron_carry", "value": "140", "unit": "m", "kind": "carry"},
                  {"field": "wrist_to_floor", "value": "34", "unit": "in"})
    assert [r["read_back"] for r in out["results"]] == [
        "7-iron club speed 74.6 mph", "7-iron carry 153 yards in the air", "wrist-to-floor 86.4 cm (34.0 in)"]
    # golfer: "no, 35 inches" -> same field restaged with the corrected value
    out = propose(h, {"field": "wrist_to_floor", "value": "35", "unit": "in"})
    assert out["state"]["staged_for_review"]["wrist_to_floor"]["values"] == {"wrist_to_floor_cm": 88.9}


def test_golfer_can_edit_a_staged_value_before_confirming():
    h, _ = new()
    propose(h, {"field": "height", "value": "175", "unit": "cm"})
    st = client.post("/api/session/confirm", headers=h,
                     json={"keys": ["height"], "edits": {"height": {"field": "height", "value": "178", "unit": "cm"}}}).json()
    assert st["state"]["profile"]["height_cm"] == 178


def test_recommendation_needs_confirmed_inputs_and_goes_stale_on_change():
    h, _ = new()
    r = client.post("/api/session/recommendation", headers=h).json()["tool_result"]
    assert not r["ok"] and "height_cm" in r["missing"]
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    r = client.post("/api/session/recommendation", headers=h).json()
    tool, st = r["tool_result"], r["state"]
    assert tool["ok"] and tool["spec"]["lie"] == "+1 degrees" and tool["spec"]["build"] == "custom"
    # the card and the agent see the same recommendation
    assert st["full_recommendation"]["id"] == tool["recommendation_id"]
    assert st["full_recommendation"]["result"]["best"]["head_name"] == tool["spec"]["head"]
    assert not st["recommendation"]["stale"]
    # a spoken correction, once confirmed, invalidates it
    propose(h, {"field": "glove", "value": "XL"})
    assert not client.get("/api/session", headers=h).json()["recommendation"]["stale"]   # staged only
    client.post("/api/session/confirm", headers=h, json={"keys": ["glove"]})
    assert client.get("/api/session", headers=h).json()["recommendation"]["stale"]


def test_measurement_needs_its_own_confirmation():
    h, _ = new()
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    client.post("/api/session/recommendation", headers=h)
    client.post("/api/session/measurement", headers=h, json={"height_cm": 177.2, "wrist_to_floor_cm": 84.9, "source": "photo"})
    st = client.get("/api/session", headers=h).json()
    assert st["measurement"] is None and st["measurement_staged"]["wrist_to_floor_cm"] == 84.9
    assert not st["recommendation"]["stale"]
    st = client.post("/api/session/measurement/confirm", headers=h).json()
    assert st["measurement"]["source"] == "photo" and st["recommendation"]["stale"]
    tool = client.post("/api/session/recommendation", headers=h).json()["tool_result"]
    assert tool["spec"]["build"] == "stock" and tool["measured_with"] == "photo"
    assert {c["field"] for c in tool["changes_from_measurement"]} == {"Lie", "Build"}
    assert client.post("/api/session/measurement/confirm", headers=h).status_code == 409


def test_webhook_requires_secret_and_uses_the_session():
    os.environ["AGENT_TOOL_SECRET"] = "test-secret"
    h, sid = new()
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    assert client.post("/api/agent/fit", headers={"X-Fitting-Session": sid}).status_code == 401
    assert client.post("/api/agent/fit", headers={"Authorization": "Bearer wrong", "X-Fitting-Session": sid}).status_code == 401
    r = client.post("/api/agent/fit", headers={"Authorization": "Bearer test-secret", "X-Fitting-Session": sid}).json()
    assert r["ok"] and r["recommendation_id"] == 1
    assert client.get("/api/session", headers=h).json()["full_recommendation"]["id"] == 1
    r = client.post("/api/agent/fit", headers={"Authorization": "Bearer test-secret", "X-Fitting-Session": "gone"}).json()
    assert r["error"] == "session_not_found"


def test_current_clubs_is_staged_and_speed_source_is_evidence():
    h, _ = new()
    out = propose(h, {"field": "current_clubs", "value": "  old   cavity backs from 2015 "})
    assert out["results"][0]["read_back"] == "your current irons are old cavity backs from 2015"
    st = client.post("/api/session/confirm", headers=h, json={"keys": ["current_clubs"]}).json()["state"]
    assert st["profile"]["current_clubs"] == "old cavity backs from 2015"
    st = client.put("/api/session/profile", headers=h, json={"values": dict(FULL, speed_source="simulator")}).json()
    st = client.post("/api/session/recommendation", headers=h).json()["state"]
    flex = next(d for d in st["evidence"]["decisions"] if d["decision"] == "Shaft flex")
    assert flex["evidence"][0]["strength"].startswith("measured") and "simulator" in flex["evidence"][0]["what"]


def test_three_swings_saved_as_median_tempo():
    h, _ = new()
    sw = [{"backswing_s": b, "downswing_s": 0.3, "fps": 60} for b in (0.85, 0.9, 0.95)]
    st = client.post("/api/session/swings", headers=h, json={"swings": sw}).json()
    assert st["evidence"]["swing_test"]["swings"] == 3 and st["evidence"]["swing_test"]["tempo"] == "moderate"
    assert "tempo" not in st["profile"] or st["profile"]["tempo"] is None
    assert client.post("/api/swing/events", json={"takeaway_s": 1, "top_s": 0.5, "impact_s": 2}).status_code == 422


def test_spoken_number_words_are_understood():
    """Seen in the live noise tests: the agent passed 'five foot ten' and was asked to repeat it."""
    from app.session import parse_update, words_to_digits
    assert words_to_digits("a hundred and fifty yards") == "150 yards"
    assert words_to_digits("thirty four, no, thirty five") == "34, no, 35"
    for v, u in [("five foot ten", None), ("five foot ten", "ft_in"), ("5'10\"", None), ("5-10", "ft_in")]:
        assert parse_update({"field": "height", "value": v, "unit": u}, {})[0]["height_cm"] == 177.8
    assert parse_update({"field": "wrist_to_floor", "value": "thirty-five", "unit": "in"}, {})[0]["wrist_to_floor_cm"] == 88.9


def test_staged_measurement_shows_boundary_check_before_confirming():
    h, _ = new()
    client.put("/api/session/profile", headers=h, json={"values": FULL})
    st = client.post("/api/session/measurement", headers=h,
                     json={"height_cm": 177.8, "wrist_to_floor_cm": 88.9, "source": "tape"}).json()
    chk = st["measurement_check"]
    assert chk["available"] and chk["level"] in ("confident", "close call", "check needed")
    assert chk["build_after"] in ("stock", "custom")
    h2, _ = new()
    st = client.post("/api/session/measurement", headers=h2,
                     json={"height_cm": 177.8, "wrist_to_floor_cm": 88.9, "source": "tape"}).json()
    assert st["measurement_check"]["available"] is False


def test_sessions_survive_a_server_restart(tmp_path):
    from app.session import Store
    a = Store(tmp_path)
    s = a.create()
    s.set_profile({"age": 44, "glove": "M"})
    a.save(s)
    b = Store(tmp_path)                                  # a new process reading the same folder
    again = b.get(s.id)
    assert again is not None and again.profile["age"] == 44 and again.profile["glove"] == "M"
    assert b.get("../../etc/passwd") is None


def test_answers_persist_through_the_api(tmp_path):
    h, sid = new()
    client.put("/api/session/profile", headers=h, json={"values": {"age": 51}})
    from app.session import SESSION_DIR
    assert (SESSION_DIR / f"{sid}.json").exists()


def test_everyday_answers_for_mens_or_womens_fitting():
    from app.session import parse_update
    for said, want in [("men's", "male"), ("mens", "male"), ("a man", "male"), ("Male", "male"), ("men’s fitting", "male"),
                       ("women's", "female"), ("ladies", "female"), ("I'm a woman", "female"), ("female", "female")]:
        assert parse_update({"field": "sex", "value": said}, {})[0]["sex"] == want, said


def test_natural_answers_are_understood_without_a_question():
    """Seen on a live call: ordinary answers bounced back as clarifying questions and the assistant looped."""
    from app.session import parse_update
    cases = {("handedness", "I'm left-handed"): "left-handed", ("rounds_per_year", "once a week"): "31-50",
             ("score", "around 88"): "usual score 88", ("score", "low 90s"): "usual score 92", ("score", "mid eighties"): "usual score 85",
             ("seven_iron_speed", "75 mph"): "75 mph", ("seven_iron_speed", "about 80"): "80 mph",
             ("seven_iron_carry", "140 yards"): "140 yards", ("seven_iron_carry", "160 metres"): "175 yards",
             ("height", "180"): "180 cm", ("height", "5'10\""): "177.8 cm", ("wrist_to_floor", "34 inches"): "86.4 cm",
             ("wrist_to_floor", "34"): "86.4 cm", ("flight", "higher please"): "high", ("tempo", "pretty quick"): "quick",
             ("glove", "medium large"): "M/L", ("glove", "men's large"): "glove size L",
             ("characteristics", "I want more forgiveness"): "forgiveness"}
    for (field, said), want in cases.items():
        assert want in parse_update({"field": field, "value": said}, {})[1], (field, said)


# ---- Your game (six questions): handicap, driver speed, sim tag, ball flight now -> wanted, handedness from the swing
def test_a_handicap_counts_as_handicap_plus_72():
    from app.session import parse_update
    assert parse_update({"field": "score", "value": "about a 16 handicap"}, {})[0] == {"score_bucket": 4, "score_value": 88.0}
    assert parse_update({"field": "score", "value": "plus 2"}, {})[0]["score_value"] == 70.0
    assert parse_update({"field": "score", "value": "88"}, {})[0]["score_value"] == 88.0


def test_driver_speed_is_scaled_and_tagged_estimated_and_a_sim_speed_is_tagged_sim():
    from app.session import parse_update
    v, _ = parse_update({"field": "seven_iron_speed", "value": "driver about 100"}, {})
    assert v["speed_mph"] == 80.0 and v["speed_source"] == "own_estimate"
    v, _ = parse_update({"field": "seven_iron_speed", "value": "78 on a sim"}, {})
    assert v["speed_mph"] == 78.0 and v["speed_source"] == "simulator"
    v, _ = parse_update({"field": "seven_iron_carry", "value": "150 yards"}, {})
    assert v["carry_yd"] == 150.0 and v["speed_source"] is None


def test_wanted_flight_is_relative_to_the_flight_now_and_feeds_the_rules():
    from app.session import parse_update
    assert parse_update({"field": "flight_current", "value": "mid I'd say"}, {})[0] == {"flight_current": "mid"}
    assert parse_update({"field": "flight_desired", "value": "higher"}, {"flight_current": "mid"})[0] == {"flight": "high"}
    assert parse_update({"field": "flight_desired", "value": "same is fine"}, {"flight_current": "mid"})[0] == {"flight": "mid"}
    assert parse_update({"field": "flight_desired", "value": "lower"}, {"flight_current": "low"})[0] == {"flight": "low"}


def test_handedness_is_read_off_a_face_on_swing_only_when_clear():
    import numpy as np
    from app import swing
    t = np.arange(0, 2.4, 1 / 60)
    def run(sign):
        p = np.clip(np.where(t < 1.4, (t - 0.5) / 0.9, 1 - (t - 1.4) / 0.3), 0, 1) * (t >= 0.5) * (t < 1.7)
        y, x = 600 - 250 * np.sin(p * np.pi / 2), 400 + sign * 160 * np.sin(p * np.pi / 2)
        return swing.analyse(list(t * 1000), list(y), [1] * len(t), 1.0, list(x), 300.0, 178)["handedness_seen"]
    assert run(-1) == "right" and run(1) == "left" and run(0) is None


def test_the_head_without_priorities_is_never_sure_and_says_why():
    h, _ = new()
    client.put("/api/session/profile", headers=h, json={"values": {**FULL, "characteristics": [], "flight_current": "mid"}})
    r = client.post("/api/session/recommendation", headers=h).json()
    head = client.get("/api/session", headers=h).json()["evidence"]["confidence"]["lines"]["Head"]
    assert r["tool_result"]["ok"] and head["level"] != "High" and "8 times in 10" in head["why"]
