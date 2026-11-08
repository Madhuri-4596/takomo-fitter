"""Browser test of the body-measurement step (MediaPipe pose + sheet calibration), phone size.

Start the server first, then:
  .venv/Scripts/python tools/e2e_measure.py [--url http://127.0.0.1:8765]

Uses the real-person sample photo (public-domain portrait composited with the sheet; true
points placed by eye, +-0.8 cm) through the real pipeline: on-device pose tracking for the
wrist suggestion and framing checks, server-side ArUco calibration, the kit's geometry.
Chromium's fake camera plays the same photo as a live feed.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "app" / "static" / "sample"
TRUTH = json.loads((SAMPLE / "sample-body.json").read_text())
SRC_W = TRUTH["image_size"][0]
TMP = Path(tempfile.gettempdir()) / "takomo_e2e_measure"
TOL = 2.0        # cm: truth placed by eye (+-0.8), marker calibration (<=0.6 on fixtures), browser JPEG re-encode


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def to_client(page, x, y, src_w=SRC_W):
    """Client position of source-image pixel (x, y) on the editor canvas, through the app's resize and zoom."""
    page.evaluate("document.querySelector('#photo-canvas').scrollIntoView({block: 'center', behavior: 'instant'})")
    page.wait_for_function("() => new Promise(ok => { const y = scrollY; setTimeout(() => ok(scrollY === y), 150); })")
    return page.evaluate("""([x, y, sw]) => {
        const c = document.querySelector('#photo-canvas'), d = Measure._debug();
        const k = c.width / sw; x *= k; y *= k;
        const r = c.getBoundingClientRect(), fit = Math.min(r.width / c.width, r.height / c.height);
        const ox = (r.width - c.width * fit) / 2, oy = (r.height - c.height * fit) / 2;
        return {x: r.left + ox + (x - d.view.x0) * d.view.s * fit, y: r.top + oy + (y - d.view.y0) * d.view.s * fit};
    }""", [x, y, src_w])


def tap(page, p, src_w=SRC_W):
    c = to_client(page, *p, src_w=src_w)
    page.mouse.click(c["x"], c["y"])


def drag(page, a, b, src_w=SRC_W):
    ca, cb = to_client(page, *a, src_w=src_w), to_client(page, *b, src_w=src_w)
    page.mouse.move(ca["x"], ca["y"]); page.mouse.down()
    page.mouse.move(cb["x"], cb["y"], steps=6); page.mouse.up()


def debug(page):
    return page.evaluate("Measure._debug()")


def suggestion_src(page, src_w=SRC_W):
    d = debug(page)
    k = d["size"][0] / src_w
    return [d["suggestion"]["x"] / k, d["suggestion"]["y"] / k]


def floor_src(page, src_w=SRC_W):
    d = debug(page)
    k = d["size"][0] / src_w
    return [d["floorSuggestion"]["x"] / k, d["floorSuggestion"]["y"] / k] if d["floorSuggestion"] else None


def place_and_measure(page, truth=TRUTH["points"], src_w=SRC_W, scale=1.0):
    """Head top by tap; wrist and floor dragged from body tracking's suggestions. Measuring is automatic."""
    pt = lambda k: [truth[k][0] * scale, truth[k][1] * scale]
    page.wait_for_function("Measure._debug().suggestion !== null", timeout=30000)
    tap(page, pt("head_top"), src_w)
    drag(page, suggestion_src(page, src_w), pt("wrist"), src_w)
    f = floor_src(page, src_w)
    drag(page, f, pt("floor"), src_w) if f else tap(page, pt("floor"), src_w)
    return read_measurement(page)


def read_measurement(page):
    """Wait for the automatic measurement of the markers as they now stand."""
    page.wait_for_timeout(300)
    page.wait_for_function("!Measure._debug().pending && Fitter.state().measurement_staged", timeout=15000)
    m = page.evaluate("Fitter.state().measurement_staged")
    expect(page.locator("#measure-result .metric .v").first).to_have_text(f"{m['height_cm']} cm")
    return m["height_cm"], m["wrist_to_floor_cm"]


def upload(page, path):
    page.locator("#photo-input").set_input_files(str(path))
    expect(page.locator("#tap-card")).to_be_visible()


def fixtures():
    TMP.mkdir(exist_ok=True)
    img = cv2.imread(str(SAMPLE / "sample-body.jpg"))
    cv2.imwrite(str(TMP / "large.jpg"), cv2.resize(img, None, fx=1.4, fy=1.4, interpolation=cv2.INTER_CUBIC))
    cv2.imwrite(str(TMP / "blurry.jpg"), cv2.GaussianBlur(img, (0, 0), 2))
    hidden = img.copy()
    paper = np.array(TRUTH["points"]["paper"], np.int32)
    cv2.fillPoly(hidden, [paper], (245, 245, 245))                      # markers gone, paper edge kept
    cv2.imwrite(str(TMP / "no_markers.jpg"), hidden)
    # EXIF orientation 6: pixels stored rotated, the viewer must rotate 90 degrees clockwise to show it upright
    from PIL import Image
    pil = Image.fromarray(cv2.cvtColor(cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE), cv2.COLOR_BGR2RGB))
    exif = Image.Exif(); exif[0x0112] = 6
    pil.save(TMP / "exif_rotated.jpg", exif=exif, quality=92)
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420).tobytes()
    h, w = img.shape[:2]
    with open(TMP / "camera.y4m", "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F10:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(2):
            f.write(b"FRAME\n" + yuv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--shots", default=str(ROOT / "screenshots"))
    a = ap.parse_args()
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    fixtures()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                          f"--use-file-for-fake-video-capture={TMP / 'camera.y4m'}"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True,
                                  permissions=["camera"])
        page = ctx.new_page()
        # 422s are the app's own validation answers (wrong marker order, crossed corners): expected here
        page.on("console", lambda m: m.type == "error" and "status of 422" not in m.text and errors.append(repr(m.text) + " @ " + repr(m.location)))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url)
        page.evaluate("Fitter.show(3)")

        print("method choice: height + camera is the default, no printing")
        check(page.evaluate("Measure.status().method") == "height", "the default method is entered height + camera")
        expect(page.locator('#ms-intro [data-method="height"]')).to_contain_text("We'll use your entered height to estimate wrist-to-floor")
        uploads = []
        page.on("request", lambda r: ("/api/photo/sheet" in r.url or "/api/photo/measure" in r.url)
                and uploads.append(f"{r.url.split('/api/')[1]} @ {page.evaluate('Measure.status().view')}/{page.evaluate('Measure.status().method')}"))
        page.click("#use-sample")
        expect(page.locator("#ms-intro-error")).to_contain_text("Enter your height first")
        check(True, "the height is asked for before measuring")
        page.fill("#ms-height", str(TRUTH["height_cm"]))
        page.click("#use-sample")
        expect(page.locator("#sheet-status")).to_contain_text("This photo stays on your device", timeout=15000)
        expect(page.locator("#tap-prompt")).to_contain_text("top of the head")
        h, w = place_and_measure(page)
        check(abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL and h == TRUTH["height_cm"],
              f"height-scale estimate {w} cm wrist-to-floor (truth {TRUTH['wrist_to_floor_cm']})")
        check([u for u in uploads if "measure-height" not in u] == [] and any("measure-height" in u for u in uploads),
              "no photo or frame was uploaded; only the marker positions")
        expect(page.locator("#measure-result")).to_contain_text("your entered height")
        page.click("#retake")

        print("instructions before the camera (printed sheet)")
        page.evaluate("Measure.setMethod('sheet')")
        intro = page.locator("#ms-intro")
        for text in ("at 100 %", "must measure 100 mm", "stand flat against the wall", "3–4 m away", "not stored"):
            expect(intro).to_contain_text(text)
        check(True, "setup steps, the wall rule and the privacy note are shown before the camera")
        page.screenshot(path=shots / "m1-intro.png", full_page=True)

        print("sample photo: suggested wrist, manual head and floor, zoom, confirm")
        page.click("#use-sample")
        expect(page.locator("#sheet-status")).to_have_text("Sheet found.", timeout=15000)
        page.wait_for_function("Measure._debug().suggestion !== null", timeout=40000)
        sx, sy = suggestion_src(page)
        wx, wy = TRUTH["points"]["wrist"]
        off_cm = ((sx - wx) ** 2 + (sy - wy) ** 2) ** 0.5 * 177.2 / (TRUTH["points"]["floor"][1] - TRUTH["points"]["head_top"][1])
        check(off_cm < 10, f"pose suggests a wrist {off_cm:.1f} cm from the true crease (MediaPipe's wrist is the joint, not the crease)")
        expect(page.locator("#tap-prompt")).to_contain_text("top of the head")
        check(debug(page)["points"]["head"] is None, "the head top is never pre-filled from a landmark")
        f = floor_src(page)
        cm_per_px = 177.2 / (TRUTH["points"]["floor"][1] - TRUTH["points"]["head_top"][1])
        check(f is not None and abs(f[1] - TRUTH["points"]["floor"][1]) * cm_per_px < 4,
              f"body tracking suggests the floor {abs(f[1] - TRUTH['points']['floor'][1]) * cm_per_px:.1f} cm from the true floor line")
        tap(page, TRUTH["points"]["head_top"])
        expect(page.locator("#tap-prompt")).to_contain_text("Measured below")
        page.click("#tap-undo")
        check(debug(page)["points"]["head"] is None, "undo removes the head marker")
        tap(page, TRUTH["points"]["head_top"])
        drag(page, floor_src(page), TRUTH["points"]["floor"])
        page.click("#zoom-in"); page.click("#zoom-in")              # zooms on the unconfirmed wrist
        check(debug(page)["view"]["s"] > 2, "zoom in on the wrist")
        drag(page, suggestion_src(page), TRUTH["points"]["wrist"])   # dragged while zoomed: mapping must hold
        wr = debug(page)["points"]["wrist"]
        k = debug(page)["size"][0] / SRC_W
        check(abs(wr["x"] / k - TRUTH["points"]["wrist"][0]) < 2 and abs(wr["y"] / k - TRUTH["points"]["wrist"][1]) < 2,
              "a marker moved while zoomed lands on the right pixel")
        page.click("#zoom-out"); page.click("#zoom-out"); page.click("#zoom-out")
        page.screenshot(path=shots / "m2-markers.png", full_page=True)
        h, w = read_measurement(page)
        check(True, "measured automatically once the markers are placed: no Measure button")
        expect(page.locator("#measure-confirm")).to_be_visible()
        check(abs(h - TRUTH["height_cm"]) <= TOL and abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL,
              f"measured {h} / {w} cm vs truth {TRUTH['height_cm']} / {TRUTH['wrist_to_floor_cm']}")
        expect(page.locator("#measure-result .source-label")).to_have_text("Camera estimate · printed sheet scale")
        expect(page.locator("#measure-result")).to_contain_text("Not yet checked against tape")
        check(page.evaluate("Fitter.state().measurement") is None, "not saved before confirmation")
        page.screenshot(path=shots / "m3-result.png", full_page=True)

        print("cross-check against entered answers")
        page.evaluate("Fitter.showScreen('home')")
        page.click(".demo summary")
        page.select_option("#demo-select", "DEMO-13"); page.click("#demo-load")
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        page.evaluate("Fitter.show(3)")
        expect(page.locator("#measure-result .crosscheck")).to_contain_text("wrist-to-floor")
        check(True, f"a {88.9 - w:.1f} cm wrist-to-floor difference is explained, with adjust and tape options")
        page.click("#adjust-markers")
        expect(page.locator("#tap-card")).to_be_visible()
        page.click("#measure-confirm") if page.locator("#measure-confirm").is_visible() else None
        expect(page.locator("#swing-screen")).to_be_visible(timeout=15000)           # confirming moves on to the swing
        check(page.evaluate("Fitter.state().measurement.source") == "photo" and
              page.evaluate("Fitter.state().full_recommendation.measured") is True,
              "confirming saves the photo measurement and refits")
        page.evaluate("Fitter.show(3)")

        print("resized upload")
        upload(page, TMP / "large.jpg")
        expect(page.locator("#sheet-status")).to_contain_text("Sheet found", timeout=15000)
        check(debug(page)["size"][1] <= 2400, f"a {int(2158 * 1.4)} px photo is downscaled to {debug(page)['size']}")
        h, w = place_and_measure(page, src_w=SRC_W * 1.4, scale=1.4)
        check(abs(h - TRUTH["height_cm"]) <= TOL and abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL, f"resized photo measures {h} / {w} cm")

        print("photo orientation")
        upload(page, TMP / "exif_rotated.jpg")
        expect(page.locator("#sheet-status")).to_contain_text("Sheet found", timeout=15000)
        check(debug(page)["size"] == [1848, 2158], "an EXIF-rotated photo is shown upright")
        h, w = place_and_measure(page)
        check(abs(h - TRUTH["height_cm"]) <= TOL and abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL, f"rotated photo measures {h} / {w} cm")

        print("plain A4 paper method")
        page.click("#retake")
        page.evaluate("Measure.setMethod('paper')")
        upload(page, TMP / "no_markers.jpg")
        expect(page.locator("#sheet-status")).to_contain_text("corners of your A4 paper", timeout=15000)
        for i in range(4):
            tap(page, TRUTH["points"]["paper"][i])
        h, w = place_and_measure(page)
        check(abs(h - TRUTH["height_cm"]) <= 2.0 and abs(w - TRUTH["wrist_to_floor_cm"]) <= 2.0, f"plain paper measures {h} / {w} cm")
        page.click("#retake")
        page.evaluate("Measure.setMethod('sheet')")

        print("no markers: paper-corner fallback")
        upload(page, TMP / "no_markers.jpg")
        try:
            expect(page.locator("#sheet-status")).to_contain_text("Mark the four outside corners", timeout=15000)
        except AssertionError:
            print("   status:", page.inner_text("#sheet-status"), "| debug:", debug(page), "| method:", page.evaluate("Measure.status().method"))
            raise
        c = TRUTH["points"]["paper"]
        for i in (0, 2, 1, 3):                                       # wrong order: crossed
            tap(page, c[i])
        expect(page.locator("#measure-error")).to_contain_text("cross or are out of order")
        check(debug(page)["corners"] == [], "crossed corners are rejected and cleared")
        for i in range(4):
            tap(page, c[i])
        expect(page.locator("#tap-prompt")).to_contain_text("top of the head")
        h, w = place_and_measure(page)
        check(abs(h - TRUTH["height_cm"]) <= 2.0 and abs(w - TRUTH["wrist_to_floor_cm"]) <= 2.0, f"paper-corner scale measures {h} / {w} cm")
        expect(page.locator("#measure-result")).to_contain_text("paper corners you marked")

        print("blur and wrong markers")
        upload(page, TMP / "blurry.jpg")
        expect(page.locator("#sheet-status")).to_contain_text("blurry", timeout=15000)
        place_and_measure(page)
        expect(page.locator("#measure-result")).to_contain_text("blurry")
        check(True, "a blurry photo is flagged before and after measuring")
        page.click("#use-sample") if page.locator("#use-sample").is_visible() else (page.click("#retake"), page.click("#use-sample"))
        page.wait_for_function("Measure._debug().suggestion !== null", timeout=30000)
        drag(page, floor_src(page), TRUTH["points"]["head_top"])   # the floor marker dragged up to the head...
        tap(page, TRUTH["points"]["floor"])                         # ...and the head marker tapped on the floor: swapped
        expect(page.locator("#measure-error")).to_contain_text("wrong order")
        check(True, "markers in an impossible order are refused")

        print("live camera: framing checks, auto countdown, cancel, capture")
        page.click("#retake")
        # headless tracking runs at ~2 fps here, so give the interruption below time to land inside the countdown
        page.evaluate("void (Measure.thresholds.countdown_s = 8)")
        page.click("#cam-open")
        expect(page.locator("#cam-instruction")).to_have_text("Ready to capture", timeout=60000)
        page.wait_for_function("document.querySelectorAll('#cam-checks li.ok').length === 5", timeout=15000)
        check(True, "sheet, head, wrist, feet and stillness all pass")
        expect(page.locator("#countdown")).to_be_visible(timeout=15000)
        page.evaluate("void (window.__realDetect = Pose.detect, Pose.detect = () => null)")          # golfer walks out
        expect(page.locator("#countdown")).to_be_hidden(timeout=3000)
        expect(page.locator("#cam-instruction")).to_contain_text("Step back so your whole body is visible")
        check(page.evaluate("Measure.status().countdown_seconds_left") is None, "countdown cancels when tracking is lost")
        page.evaluate("void (Pose.detect = window.__realDetect, Measure.thresholds.countdown_s = 3)")
        expect(page.locator("#cam-instruction")).to_have_text("Ready to capture", timeout=30000)
        page.screenshot(path=shots / "m4-live.png")
        # tracking back: it becomes ready again and a new countdown captures (slow headless tracking: allow time)
        try:
            expect(page.locator("#tap-card")).to_be_visible(timeout=40000)
        except AssertionError:
            for _ in range(6):
                print("   state:", page.evaluate("(() => { const s = Measure.status(); return [s.camera.instruction, s.camera.issues, s.countdown_seconds_left, s.view, s.person, s.camera.markers_found]; })()"))
                page.wait_for_timeout(1000)
            raise
        check(page.evaluate("Measure.status().camera.running") is False, "camera tracks stop after capture")
        expect(page.locator("#sheet-status")).to_have_text("Sheet found.", timeout=15000)
        h, w = place_and_measure(page)
        check(abs(h - TRUTH["height_cm"]) <= TOL and abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL, f"live capture measures {h} / {w} cm")

        print("live camera, height method: no sheet check, nothing uploaded")
        page.click("#retake")
        page.evaluate("Measure.setMethod('height')")
        n_before = len(uploads)
        page.click("#cam-open")
        expect(page.locator('#cam-checks li[data-check="sheet"]')).to_be_hidden()
        expect(page.locator("#cam-instruction")).to_have_text("Ready to capture", timeout=60000)
        expect(page.locator("#tap-card")).to_be_visible(timeout=40000)
        h, w = place_and_measure(page)
        check(abs(w - TRUTH["wrist_to_floor_cm"]) <= TOL, f"live height-mode capture estimates {w} cm")
        extra = [u for u in uploads[n_before:] if "measure-height" not in u]
        check(not extra, f"no camera frames were sent in height mode {extra}")
        page.evaluate("Measure.setMethod('sheet')")

        print("front camera: mirrored preview, unmirrored capture")
        page.click("#retake")
        page.evaluate("Measure.setFacing('user')")
        page.click("#cam-open")
        expect(page.locator("#cam-view")).to_have_class("cam-view mirrored", timeout=10000)
        check(page.evaluate("getComputedStyle(document.querySelector('#cam-overlay')).transform") ==
              page.evaluate("getComputedStyle(document.querySelector('#cam-video')).transform") != "none",
              "overlay is mirrored together with the preview")
        expect(page.locator("#tap-card")).to_be_visible(timeout=30000)
        expect(page.locator("#sheet-status")).to_have_text("Sheet found.", timeout=15000)
        check(True, "the captured photo is not mirrored (mirrored markers can't be decoded)")

        print("leaving the step stops the camera")
        page.click("#retake")
        page.evaluate("Measure.setFacing('environment')")
        page.click("#cam-open")
        page.wait_for_function("Measure.status().camera.running === true", timeout=10000)
        page.click("#step-3 .screen-foot [data-screen='swing-screen']")
        check(page.evaluate("Measure.status().camera.running") is False and
              page.evaluate("document.querySelector('#cam-video').srcObject") is None, "camera stopped on leaving")
        browser.close()
    if errors:
        raise SystemExit("console errors:\n" + "\n".join(errors))
    print("OK: body measurement verified")


if __name__ == "__main__":
    main()
