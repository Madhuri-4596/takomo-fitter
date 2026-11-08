"""Browser test of swing-video tempo: upload a video with known timing, track the hands on
device, run the kit's tempo analysis, show the effect on shaft and ball height, and keep the
tempo answer unchanged until the golfer chooses it.

  .venv/Scripts/python tools/e2e_swing.py [--url http://127.0.0.1:8765]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIDEO = Path(tempfile.gettempdir()) / "takomo_swing.webm"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--shots", default=str(ROOT / "screenshots"))
    a = ap.parse_args()
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_swing_video.py"), str(VIDEO)], check=True, capture_output=True)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        uploads = []
        page.on("request", lambda r: r.method == "POST" and uploads.append((r.url.split("/api/")[-1], len(r.post_data_buffer or b""))))
        page.goto(a.url)

        print("swing video -> tempo (known: backswing 0.9 s, downswing 0.3 s)")
        page.click(".demo summary")
        page.select_option("#demo-select", "DEMO-13"); page.click("#demo-load")
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        check(page.evaluate("Fitter.state().profile.tempo") == "smooth", "Anders answered 'smooth'")
        page.evaluate("Fitter.showScreen('swing-screen')")
        expect(page.locator("#swing")).to_be_visible()
        page.locator("#swing-input").set_input_files(str(VIDEO))
        expect(page.locator("#swing-result")).to_be_visible(timeout=240000)
        r = page.evaluate("Swing._debug().result")
        check(abs(r["backswing_s"] - 0.9) <= 0.12 and abs(r["downswing_s"] - 0.3) <= 0.07,
              f"measured backswing {r['backswing_s']} s and downswing {r['downswing_s']} s at {r['fps']} fps")
        check(r["tempo"] == "moderate", f"total {r['total_s']} s -> {r['tempo']} by the kit's rule")
        check(not any(path == "swing/tempo" and size > 200000 for path, size in uploads) and
              all("photo" not in path for path, _ in uploads), "only the hand track was sent, never the video")
        eff = r["tempo_effect"]
        check(eff and eff["changes_shaft"] and eff["current_shaft_model"] == "KBS Tour Lite",
              f"tempo effect: {eff['current_shaft_model']} -> {eff['shaft_model']}, flight {eff['height_change']}")
        expect(page.locator("#swing-result .effect")).to_contain_text("ball flight")
        expect(page.locator("#swing-result .swing-chart polyline")).to_have_count(1)
        expect(page.locator("#swing-progress")).to_contain_text("Swing 1 of 3")
        expect(page.locator("#swing-play")).to_have_count(1)                      # playback of the golfer's own clip
        page.screenshot(path=shots / "s1-swing.png", full_page=True)

        print("experimental club speed from hand speed: shown, never used")
        e = r["speed_experimental"]
        expect(page.locator("#swing-result")).to_contain_text("Experimental club speed" if not e["available"] else "Experimental")
        check(e.get("used_by_fit") is not True, f"experimental speed is labelled and not used by the fit ({e.get('text') or e.get('reason')})")

        print("timing markers can be corrected on the video")
        before = r["backswing_s"]
        top = page.locator('[data-ev="top"]')
        top.evaluate("(el) => { el.value = (Number(el.value) + 0.1).toFixed(3); el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); }")
        page.wait_for_function(f"Swing._debug().result && Math.abs(Swing._debug().result.backswing_s - {before}) > 0.05", timeout=10000)
        adj = page.evaluate("Swing._debug().result")
        check(adj["adjusted"] and abs(adj["backswing_s"] - before - 0.1) < 0.02,
              f"moving the top marker 0.1 s re-times the backswing: {before} -> {adj['backswing_s']} s")
        top.evaluate("(el) => { el.value = (Number(el.value) - 0.1).toFixed(3); el.dispatchEvent(new Event('change')); }")
        page.wait_for_timeout(800)
        page.click("#swing-accept")
        expect(page.locator("#swing-summary")).to_be_visible()
        expect(page.locator("#swing-progress")).to_contain_text("Swing 2 of 3")
        check(page.evaluate("Swing._debug().combined.swings") == 1, "an accepted swing goes into the summary")

        print("the measurement is evidence; the golfer chooses")
        page.click("#swing-save")
        page.wait_for_function("Fitter.state().evidence.swing_test !== null", timeout=10000)
        check(page.evaluate("Fitter.state().profile.tempo") == "smooth", "saving the evidence does not change the tempo answer")
        page.click("#swing-use")
        page.wait_for_function("Fitter.state().profile.tempo === 'moderate'", timeout=10000)
        expect(page.locator('input[name="tempo"][value="moderate"]')).to_be_checked()
        check(True, "'Use moderate as my tempo' updates the shared profile and the form")
        print("clubhead speed is separate from tempo")
        expect(page.locator("#speed-now")).to_contain_text("mph club speed")
        page.fill("#sim-speed", "76.5"); page.select_option("#sim-source", "launch_monitor"); page.click("#sim-save")
        page.wait_for_function("Fitter.state().profile.speed_source === 'launch_monitor'", timeout=10000)
        check(page.evaluate("Fitter.state().profile.speed_mph") == 76.5, "typed launch-monitor speed is saved with its source")
        check(page.evaluate("Fitter.state().profile.speed_mph") == 76.5, "the experimental range never replaced the entered speed")
        page.click("#swing-done")
        expect(page.locator("#verdict")).to_be_visible()
        expect(page.locator("#verdict .evidence .effect")).to_contain_text("Your swing tempo")
        page.locator("#verdict .evidence-map summary").click()
        expect(page.locator("#verdict .ev-row").nth(2)).to_contain_text("swing video")
        expect(page.locator("#verdict .ev-row").nth(2)).to_contain_text("measured")
        check(True, "the shaft-model evidence shows the measured tempo")
        page.screenshot(path=shots / "s2-evidence.png", full_page=True)
        b.close()
    if errors:
        raise SystemExit("console errors:\n" + "\n".join(errors))
    print("OK: swing tempo verified")


if __name__ == "__main__":
    main()
