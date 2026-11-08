"""Browser test of the real camera on the fitter frontend (app/static/fitter): body measurement and swing tempo.

Chrome's camera is fed a still photo of a person (measuring) and a synthetic swing video (swing step), so the real
MediaPipe tracking, the height-scaled measurement and the kit's tempo analysis all run.

  .venv/Scripts/python tools/e2e_fitter_camera.py [--url http://127.0.0.1:8765/]
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

import cv2
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
T = Path(tempfile.gettempdir())
TRUTH = json.loads((ROOT / "app" / "static" / "sample" / "sample-body.json").read_text())


def check(ok, msg):
    if not ok:
        raise AssertionError(msg)
    print("  ok -", msg)


def fake_feeds():
    img = cv2.imread(str(ROOT / "app/static/sample/sample-body.jpg"))
    h, w = img.shape[:2]
    img = cv2.resize(img, (int(w * 960 / h) // 2 * 2, 960))
    with open(T / "fake_body.mjpeg", "wb") as f:
        buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        for _ in range(90):
            f.write(buf)
    swing = T / "takomo_swing.webm"
    if not swing.exists():
        raise SystemExit("run tools/make_swing_video.py first (creates the synthetic swing video)")
    cap = cv2.VideoCapture(str(swing))
    with open(T / "fake_swing.mjpeg", "wb") as f:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            f.write(cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())


def page_with(p, feed, url, errors):
    # visible window: live swing catching needs the GPU's tracking speed (a hidden browser tracks ~15x slower)
    b = p.chromium.launch(headless=False, args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                f"--use-file-for-fake-video-capture={feed}"])
    pg = b.new_context(viewport={"width": 390, "height": 844}, permissions=["camera"]).new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    pg.wait_for_function("window.FitterCamera && window.FitterBackend && window.FitterUI")
    return b, pg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    a = ap.parse_args()
    fake_feeds()
    errors = []
    with sync_playwright() as p:
        print("measure: live camera, body tracking, height-scaled wrist-to-floor")
        b, pg = page_with(p, T / "fake_body.mjpeg", a.url, errors)
        h = TRUTH["height_cm"]
        pg.evaluate("""async (h) => { const tok = await FitterBackend.session();
            const st = await FitterBackend.api('/api/session/profile', {method: 'PUT', json: {values: {height_cm: h}}});
            FitterBackend.setState(st); }""", h)
        pg.evaluate("FitterUI.startMeasure()")
        expect(pg.locator("#mcam video.realcam")).to_have_count(1)
        check(True, "the real camera plays inside their camera card")
        expect(pg.locator("#readback")).to_be_visible(timeout=90000)
        m = pg.evaluate("FitterCamera._debug().measured")
        err = abs(m["wrist_to_floor_cm"] - TRUTH["wrist_to_floor_cm"])
        check(err < 3, f"automatic wrist-to-floor {m['wrist_to_floor_cm']} cm vs truth {TRUTH['wrist_to_floor_cm']} cm (head top estimated from the eyes)")
        expect(pg.locator("#rb-heard")).to_contain_text(re.compile("give or take an inch|Measured with the sheet"))
        before = m["wrist_to_floor_cm"]
        # drag the wrist marker 40 px down on the frozen photo: the number must re-measure (shorter)
        pg.wait_for_timeout(1200)                     # let the read-back panel finish sliding in before aiming at a dot
        box = pg.locator("#mcam canvas.realcam-overlay").bounding_box()
        wr = pg.evaluate("""() => { const F = FitterCamera._debug().frozen, c = document.querySelector('#mcam canvas.realcam-overlay');
            const v = document.querySelector('#mcam video.realcam'), W = v.videoWidth, H = v.videoHeight;
            const s = Math.min(c.clientWidth / W, c.clientHeight / H), ox = (c.clientWidth - W * s) / 2, oy = (c.clientHeight - H * s) / 2;
            const x = v.classList.contains('mirror') ? 1 - F.wr.x : F.wr.x;   // the front camera is shown mirrored
            return [ox + x * W * s, oy + F.wr.y * H * s]; }""")
        pg.mouse.move(box["x"] + wr[0], box["y"] + wr[1]); pg.mouse.down()
        pg.mouse.move(box["x"] + wr[0], box["y"] + wr[1] + 40, steps=5); pg.mouse.up()
        pg.wait_for_function("b => FitterCamera._debug().measured.wrist_to_floor_cm < b - 1", arg=before, timeout=10000)
        check(True, f"dragging the wrist dot re-measures: {before} -> {pg.evaluate('FitterCamera._debug().measured.wrist_to_floor_cm')} cm")
        pg.click("#rb-yes")
        # a deployed server answers in tens of ms, not instantly: wait for the confirmed measurement to be saved
        pg.wait_for_function("FitterBackend.api('/api/session').then(st => !!(st.measurement && st.measurement.source === 'photo'))", timeout=10000)
        st = pg.evaluate("FitterBackend.api('/api/session')")
        check(st["measurement"] and st["measurement"]["source"] == "photo", "That's right saves it as a camera measurement")
        expect(pg.locator("#a-swing")).to_be_enabled()
        pg.screenshot(path=str(T / "fitter_measure.png"))
        b.close()

        print("swing: record on the phone, hand track to the kit's tempo analysis")
        b, pg = page_with(p, T / "fake_swing.mjpeg", a.url, errors)
        pg.evaluate("FitterUI.startSwing(true)")
        expect(pg.locator("#cam video.realcam")).to_have_count(1)
        expect(pg.locator("#cam-line")).to_contain_text("Just swing", timeout=60000)
        # no tap: the looping test video swings, and the camera catches it by itself
        # the camera keeps watching: a catch that doesn't add up is ignored and the next swing is caught (the test video loops)
        pg.wait_for_function("FitterCamera._debug().swings.length >= 1", timeout=180000)
        sw = pg.evaluate("FitterCamera._debug().swings")
        line = pg.inner_text("#cam-line")
        check(len(sw) >= 1, f"swing caught live and read, no tap: {sw[0] if sw else line}")
        check(":" in pg.inner_text("#cam-tempo") and "?" not in pg.inner_text("#cam-tempo"), f"tempo shown in their readout: {pg.inner_text('#cam-tempo')}")
        pg.screenshot(path=str(T / "fitter_swing.png"))
        print("swing clip upload: the exact file, checked and timed for real")
        pg.locator("#clip-file").set_input_files(str(T / "takomo_swing.webm"))
        pg.wait_for_function("FitterUI.S.clips.length === 1 || /won.t work/.test(document.querySelector('#sheet-clip-h').textContent)", timeout=240000)
        sw = pg.evaluate("FitterCamera._debug().swings")[-1]
        check(abs(sw["backswing_s"] - 0.9) <= 0.15 and abs(sw["downswing_s"] - 0.3) <= 0.08,
              f"clip timed: backswing {sw['backswing_s']} s, downswing {sw['downswing_s']} s (truth 0.9 / 0.3)")
        check("fps" in pg.inner_text("#gate-done").lower() and "30 fps" in pg.inner_text("#gate-done").lower() or "31 fps" in pg.inner_text("#gate-done").lower(), f"their clip check shows real values: {pg.inner_text('#gate-done').replace(chr(10), ' ')[:120]}")
        b.close()
    if errors:
        raise SystemExit("page errors:\n" + "\n".join(errors))
    print("OK: real camera and swing verified on the fitter frontend")


if __name__ == "__main__":
    main()
