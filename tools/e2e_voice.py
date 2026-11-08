"""Browser test of the voice integration with a scripted stand-in for the ElevenLabs SDK.

The real @elevenlabs/client script is replaced by a fake that records what the app sends
(contextual updates, typed messages, user-activity pings, mute) and lets the test play the
agent's side: call client tools, emit transcript lines, drop the connection. The fitting
backend, camera pipeline and all app code are real. Chromium's fake media devices provide the
microphone and a camera feed of the sample photo.

  .venv/Scripts/python tools/e2e_voice.py [--url http://127.0.0.1:8765]

It cannot judge how the real agent speaks; that needs a manual session with a real agent
(see README, "Voice: manual checks").
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TRUTH = json.loads((ROOT / "app" / "static" / "sample" / "sample-body.json").read_text())
CAMERA_FEED = Path(tempfile.gettempdir()) / "takomo_sample_camera.y4m"


def make_camera_feed():
    """The sample photo as a Y4M video for Chromium's --use-file-for-fake-video-capture."""
    import cv2
    img = cv2.imread(str(ROOT / "app" / "static" / "sample" / "sample-body.jpg"))
    h, w = img.shape[:2]
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
    with open(CAMERA_FEED, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(2):
            f.write(b"FRAME\n" + yuv)

FAKE_SDK = """
window.__log = { sessions: [], ctx: [], sent: [], activity: 0 };
window.ElevenLabsClient = { Conversation: { async startSession(opts) {
  const conv = {
    opts, muted: false, open: true,
    setMicMuted(m) { this.muted = m; },
    sendUserMessage(t) { __log.sent.push(t); },
    sendContextualUpdate(t) { __log.ctx.push(t); },
    sendUserActivity() { __log.activity++; },
    async endSession() { if (!this.open) return; this.open = false; __log.open--; opts.onDisconnect && opts.onDisconnect({ reason: 'user' }); },
    getId() { return 'conv_fake'; },
  };
  __log.sessions.push({ textOnly: !!opts.textOnly, connectionType: opts.connectionType, hasToken: !!opts.conversationToken,
                        hasSignedUrl: !!opts.signedUrl, sessionVar: opts.dynamicVariables && opts.dynamicVariables.secret__fitting_session });
  __log.open = (__log.open || 0) + 1;
  window.__conv = conv;
  setTimeout(() => opts.onConnect && opts.onConnect({ conversationId: 'conv_fake' }), 20);
  return conv;
} } };
window.__tool = async (name, params) => {
  const out = await window.__conv.opts.clientTools[name](params || {});
  return typeof out === 'string' ? out : JSON.parse(JSON.stringify(out));
};
window.__say = (role, message) => window.__conv.opts.onMessage({ role, message, source: role === 'agent' ? 'ai' : 'user', event_id: 1 });
window.__drop = () => { window.__conv.open = false; __log.open--; window.__conv.opts.onDisconnect({ reason: 'error', message: 'network', context: { type: 'close' } }); };
"""
PRIVACY = {"configured": True, "privacy": {"record_voice": False, "retention_days": 30, "delete_audio": True,
                                           "zero_retention_mode": False, "fit_tool_mode": "client"}}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def setup_routes(page):
    page.route("**/cdn.jsdelivr.net/npm/@elevenlabs/client@*/dist/lib.iife.js",
               lambda r: r.fulfill(status=200, content_type="application/javascript", body=FAKE_SDK))
    page.route("**/api/voice/config", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(PRIVACY)))
    page.route("**/api/voice/session", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"connection_type": "webrtc", "conversation_token": "fake-token"} if json.loads(r.request.post_data)["mode"] == "voice"
        else {"connection_type": "websocket", "signed_url": "wss://fake.example/conv"})))


def tool(page, name, params=None):
    return page.evaluate("([n, p]) => window.__tool(n, p)", [name, params or {}])


def log(page):
    return page.evaluate("window.__log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--shots", default=str(ROOT / "screenshots"))
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    make_camera_feed()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                          f"--use-file-for-fake-video-capture={CAMERA_FEED}"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True,
                                  permissions=["microphone", "camera"])
        page = ctx.new_page()
        aborting = [False]          # the failing-tool check aborts a request on purpose
        page.on("console", lambda m: m.type == "error" and not (aborting[0] and "ERR_FAILED" in m.text) and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        setup_routes(page)
        page.goto(a.url)

        print("consent and start")
        page.click("#go-fitter")
        expect(page.locator("#consent")).to_be_visible()
        expect(page.locator("#consent-list")).to_contain_text("Audio recording is switched off for this assistant.")
        expect(page.locator("#consent-list")).to_contain_text("kept by ElevenLabs for 30 days")
        page.screenshot(path=shots / "v1-consent.png")
        page.evaluate("document.querySelector('#mic').click(); document.querySelector('#mic').click()")   # impatient double tap
        expect(page.locator("#voice-status")).to_have_text("Listening")
        s = log(page)["sessions"][0]
        check(s == {"textOnly": False, "connectionType": "webrtc", "hasToken": True, "hasSignedUrl": False,
                    "sessionVar": page.evaluate("Fitter.token()")}, "voice uses WebRTC with a server-issued token and passes the fitting session as a secret variable")
        check(len(log(page)["sessions"]) == 1 and log(page)["open"] == 1, "a double tap opens exactly one conversation")
        expect(page.locator("#voice-controls")).to_be_visible()

        print("get_fitting_state")
        st = tool(page, "get_fitting_state")
        check(st["ok"] and "height_cm" in st["missing_fields"] and "full_recommendation" not in st, "state lists missing fields")

        print("misunderstood numbers")
        r = tool(page, "propose_profile_update", {"updates": [{"field": "height", "value": "180"},
                                                               {"field": "seven_iron_speed", "value": "105", "unit": "mph", "kind": "ball"},
                                                               {"field": "score", "value": "79"}]})
        check([x["status"] for x in r["results"]] == ["needs_clarification"] * 3, "unitless height, ball speed and ambiguous score all need clarification")
        check("centimetres or inches" in r["results"][0]["ask"] and "ball speed" in r["results"][1]["ask"]
              and "usual score" in r["results"][2]["ask"], "the agent gets the right clarifying question")
        check(tool(page, "get_fitting_state")["staged_for_review"] == {}, "nothing is staged while it's unclear")
        r = tool(page, "propose_profile_update", {"updates": "not a list"})
        check(r == {"ok": False, "error": "invalid_parameters", "message": "updates must be a list of {field, value, unit?, kind?}"}, "invalid tool parameters are rejected with a structured error")

        print("unit correction and staging")
        tool(page, "propose_profile_update", {"updates": [{"field": "height", "value": "5 10", "unit": "ft_in", "heard_text": "five ten"},
                                                          {"field": "wrist_to_floor", "value": "34", "unit": "in"}]})
        r = tool(page, "propose_profile_update", {"updates": [{"field": "wrist_to_floor", "value": "35", "unit": "in", "heard_text": "no, thirty-five inches"}]})
        check(r["results"][0]["read_back"] == "wrist-to-floor 88.9 cm (35.0 in)", "spoken correction restages the same field")
        expect(page.locator('input[name="wtf"]')).to_have_value("")
        check(set(tool(page, "get_fitting_state")["staged_for_review"]) == {"height", "wrist_to_floor"},
              "read-back answers wait for the golfer's yes and do not reach the form yet")

        print("the golfer says yes: the agent saves")
        r = tool(page, "confirm_profile_update", {"fields": ["height", "glove"]})              # glove was never staged
        check(r["ok"] and r["saved"] == ["height"], "only staged answers can be saved")
        r = tool(page, "confirm_profile_update", {})
        check(r["ok"] and r["saved"] == ["wrist_to_floor"], "an empty list saves the rest")
        check(tool(page, "confirm_profile_update", {})["error"] == "nothing_staged", "nothing to save without a read-back first")
        st = tool(page, "get_fitting_state")
        check(st["profile"]["height_cm"] == 177.8 and st["profile"]["wrist_to_floor_cm"] == 88.9 and not st["staged_for_review"],
              "saved after the yes")
        expect(page.locator("#review")).to_have_count(0)
        page.click("#voice-hide")
        expect(page.locator('input[name="height"]')).to_have_value("177.8")
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        check(True, "the form shows the same saved profile, editable there")

        print("switch between voice, typing and the form")
        page.click(".demo summary")
        page.select_option("#demo-select", "DEMO-13")
        page.click("#demo-load")                                                   # golfer fills the rest in the form
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        page.wait_for_function("window.__log.ctx.some(c => c.includes('confirmed answers changed'))")
        check(True, "form edits during a conversation notify the agent")
        page.evaluate("Fitter.showScreen('chat')")
        page.click("#switch-mode")
        expect(page.locator("#voice-status")).to_have_text("Connected · typing")
        check(log(page)["sessions"][-1]["textOnly"] is True and log(page)["sessions"][-1]["connectionType"] == "websocket",
              "switching to typing starts a text-only session")
        check(log(page)["open"] == 1, "the voice session was ended first: still exactly one open")
        expect(page.locator("#mute")).to_be_hidden()
        page.fill("#composer-input", "My glove is a medium")
        page.click("#composer button")
        check(log(page)["sent"][-1] == "My glove is a medium", "typed message is sent to the agent")
        expect(page.locator("#transcript .user").last).to_have_text("My glove is a medium")
        page.click("#switch-mode")
        expect(page.locator("#voice-status")).to_have_text("Listening")
        check(log(page)["sessions"][-1]["textOnly"] is False, "and back to voice")
        page.evaluate("window.__conv.opts.onModeChange({mode: 'speaking'})")      # interruption: golfer types while agent talks
        expect(page.locator("#voice-status")).to_have_text("Speaking")
        before = log(page)["activity"]
        page.fill("#composer-input", "wait, one thing")
        page.click("#composer button")
        check(log(page)["activity"] > before and log(page)["sent"][-1] == "wait, one thing", "typing while the agent speaks signals activity and sends the message")
        page.click("#mute")
        check(page.evaluate("window.__conv.muted") and page.locator("#mute").get_attribute("aria-pressed") == "true", "mute mutes the microphone")
        page.click("#mute")

        print("recommendation matches the card")
        r = tool(page, "get_fit_recommendation")
        expect(page.locator("#verdict")).to_be_visible()
        card = page.locator("#verdict .fit")
        check(r["ok"] and card.get_attribute("data-rec-id") == str(r["recommendation_id"]), "card shows the same recommendation id")
        check(card.locator(".rec-card h3").text_content().strip() == r["spec"]["head"] and card.locator(".rec-card .build").text_content().strip().lower() == "custom build"
              and r["spec"]["build"] == "custom", "card head and build match what the agent was told")
        check(r["next_useful_check"].startswith("Measure wrist-to-floor"), "unmeasured fit suggests the photo as next check")

        print("spoken correction invalidates the fit")
        tool(page, "propose_profile_update", {"updates": [{"field": "glove", "value": "XL"}]})
        check(not tool(page, "get_fitting_state")["recommendation"]["stale"], "staged change does not invalidate")
        tool(page, "confirm_profile_update", {})
        expect(page.locator("#verdict .stale")).to_be_visible()
        check(tool(page, "get_fitting_state")["recommendation"]["stale"], "confirmed change marks the fit stale for agent and card")
        r = tool(page, "get_fit_recommendation")
        expect(page.locator("#verdict .fit")).to_have_attribute("data-rec-id", str(r["recommendation_id"]))
        expect(page.locator('#verdict .fit details[data-row="Grip"] .v')).to_contain_text("Oversize")
        check(r["spec"]["grip"] == "oversize" and "oversize grip" in " ".join(r["custom_reasons"]), "new fit matches the card")
        tool(page, "propose_profile_update", {"updates": [{"field": "glove", "value": "M"}]})
        tool(page, "confirm_profile_update", {})

        print("delayed and failing tool calls")
        page.route("**/api/session/recommendation", lambda route: (page.wait_for_timeout(3000), route.continue_()))
        r = tool(page, "get_fit_recommendation")
        check(r["ok"] and page.locator("#verdict .fit").get_attribute("data-rec-id") == str(r["recommendation_id"]), "a 3 s delay still returns and matches the card")
        page.unroute("**/api/session/recommendation")
        page.route("**/api/session/recommendation", lambda route: route.abort())
        aborting[0] = True
        r = tool(page, "get_fit_recommendation")
        aborting[0] = False
        check(r["ok"] is False and r["error"] == "tool_failed" and "say" in r, "a failed call returns a structured error")
        expect(page.locator("#transcript .sys").last).to_contain_text("That didn't work")
        page.unroute("**/api/session/recommendation")
        check(tool(page, "get_fitting_state")["profile"]["height_cm"] == 177.8, "confirmed answers survive the failure")

        print("disconnect and reconnect")
        n = len(log(page)["sessions"])
        page.evaluate("window.__drop()")                     # dropped without the golfer ending it: reconnects by itself
        expect(page.locator("#transcript .sys").last).to_contain_text("Reconnecting")
        expect(page.locator("#voice-status")).to_have_text("Listening")
        check(len(log(page)["sessions"]) == n + 1 and any(c.startswith("[app] Reconnected") for c in log(page)["ctx"]), "a dropped conversation reconnects by itself, with context")
        for _ in range(3):                                   # a real outage: after 3 tries in 5 minutes, ask the golfer
            page.evaluate("window.__drop()")
            page.wait_for_timeout(600)
        expect(page.locator("#voice-alert")).to_contain_text("connection dropped")
        page.click("#voice-alert-actions >> text=Reconnect")
        expect(page.locator("#voice-status")).to_have_text("Listening")
        check(True, "repeated drops stop auto-reconnecting and offer Reconnect")
        check(tool(page, "get_fitting_state")["profile"]["glove"] == "M", "profile kept across reconnect")

        print("camera via voice")
        r = tool(page, "open_measurement_step")
        check(r["opened"], "open_measurement_step opens the camera step")
        expect(page.locator("#cam")).to_be_visible()
        expect(page.locator("#cam-instruction")).to_have_text("Ready to capture", timeout=60000)
        status = tool(page, "get_measurement_status")
        check(status["camera"]["permission"] == "granted" and status["person"]["detected"] and status["method"] == "height"
              and status["status"] in ("camera_live", "countdown"), "measurement status reports the real camera and body-tracking state")
        page.screenshot(path=shots / "v3-camera.png")
        expect(page.locator("#tap-card")).to_be_visible(timeout=40000)            # automatic countdown, then capture
        cam_msgs = [c for c in log(page)["sent"] + log(page)["ctx"] if c.startswith("[camera]")]
        ready_msgs = [c for c in cam_msgs if "Ready to capture" in c]
        if not (len(ready_msgs) == 1 and len(cam_msgs) == len(set(cam_msgs))):
            print("   camera messages:", *[c[:110] for c in cam_msgs], sep=chr(10) + "     ")
        check(len(ready_msgs) == 1 and len(cam_msgs) == len(set(cam_msgs)),
              f"each camera instruction is announced once, even when readiness flickers ({len(cam_msgs)} messages)")
        check(any(c.startswith("[camera] (message from the app") for c in log(page)["sent"]), "a new camera state prompts the agent to speak it")
        check(log(page)["activity"] >= 3 and any("Do not speak" in c for c in log(page)["ctx"]), "agent kept quiet during the countdown")
        check(tool(page, "get_measurement_status")["status"] == "photo_captured_place_points", "status: photo captured, place markers")
        from e2e_measure import TRUTH, place_and_measure
        h, w = place_and_measure(page)
        status = tool(page, "get_measurement_status")
        m = status["measured_awaiting_confirmation"]
        check(status["status"] == "measured_awaiting_confirmation" and abs(m["wrist_to_floor_cm"] - TRUTH["wrist_to_floor_cm"] * h / TRUTH["height_cm"]) < 2.0,
              f"live-camera estimate {m['height_cm']} / {m['wrist_to_floor_cm']} cm, awaiting confirmation")
        page.evaluate("document.querySelector('#measure-confirm').click()")
        page.wait_for_timeout(300)
        check(tool(page, "get_fitting_state")["measurement"] is None, "a scripted click does not confirm the measurement")
        page.click("#measure-confirm")
        expect(page.locator("#swing-screen")).to_be_visible()                  # the journey moves on to the swing
        expect(page.locator("#voice-dock")).to_be_visible()                    # the assistant stays reachable
        expect(page.locator("#dock-status")).to_have_text("Listening")
        r = tool(page, "get_fit_recommendation")
        card = page.locator("#verdict .fit")
        expect(card).to_have_attribute("data-rec-id", str(r["recommendation_id"]))
        page.evaluate("Fitter.showScreen('verdict')")
        badge = "Stock set" if r["spec"]["build"] == "stock" else "Custom build"
        check(card.locator(".rec-card .build").text_content().strip().lower() == badge.lower() and card.locator(".rec-card h3").text_content().strip() == r["spec"]["head"]
              and r["measured_with"] == "photo", "measured fit matches the card")
        page.evaluate("Fitter.showScreen('chat')")
        page.screenshot(path=shots / "v4-fit-with-voice.png")
        page.click("#end")
        expect(page.locator("#voice-alert")).to_contain_text("Conversation ended")
        ctx.close()

        print("microphone and camera denied")
        # headless Chromium can't show (or deny) a permission prompt, so reject the way a browser does
        # when the user blocks access
        b2 = p.chromium.launch()
        ctx2 = b2.new_context(viewport={"width": 390, "height": 844})
        ctx2.add_init_script("navigator.mediaDevices.getUserMedia = () => "
                             "Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));")
        page = ctx2.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        setup_routes(page)
        page.goto(a.url)
        page.click("#go-fitter")
        page.click("#start-voice")
        expect(page.locator("#voice-alert")).to_contain_text("Microphone access is blocked")
        page.screenshot(path=shots / "v5-mic-denied.png")
        page.click("#voice-alert-actions >> text=Type instead")
        expect(page.locator("#voice-status")).to_have_text("Connected · typing")
        check(log(page)["sessions"][-1]["textOnly"] is True, "mic denied -> typing works")
        r = tool(page, "open_measurement_step")
        check(r["method"] == "paper" and r["height_known"] is False and "A4" in r["note"],
              "no height yet: the camera measures height too, with an A4 sheet as the scale")
        expect(page.locator("#cam-error")).to_contain_text("Camera access is blocked")
        st = tool(page, "get_measurement_status")
        check(st["camera"]["permission"] == "denied" and st["status"] == "camera_denied", "camera denied is reported, upload offered")
        expect(page.locator("#ms-intro .options")).to_be_visible()
        b2.close()
        browser.close()
    if errors:
        raise SystemExit("console errors:\n" + "\n".join(errors))
    print("OK: voice integration verified")


if __name__ == "__main__":
    main()
