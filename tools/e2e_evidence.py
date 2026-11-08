"""Browser test of the evidence features: game questions, flight diagram, head comparison,
evidence map and the strike test (synthetic clubface photo with known marks).

  .venv/Scripts/python tools/e2e_evidence.py [--url http://127.0.0.1:8765]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import cv2
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_evidence import face  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "takomo_e2e_evidence"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ok -", msg)


def tap(page, x, y):
    page.evaluate("document.querySelector('#strike-canvas').scrollIntoView({block: 'center', behavior: 'instant'})")
    page.wait_for_timeout(200)
    c = page.evaluate("""([x, y]) => { const c = document.querySelector('#strike-canvas'), r = c.getBoundingClientRect();
        const fit = Math.min(r.width / c.width, r.height / c.height);
        return {x: r.left + (r.width - c.width * fit) / 2 + x * fit, y: r.top + (r.height - c.height * fit) / 2 + y * fit}; }""", [x, y])
    page.mouse.click(c["x"], c["y"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--shots", default=str(ROOT / "screenshots"))
    a = ap.parse_args()
    shots = Path(a.shots); shots.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(exist_ok=True)
    img, truth, heel, toe = face([-18, -6, 2, 12, 22])
    cv2.imwrite(str(TMP / "face.jpg"), img)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url)

        print("your game: representative score, where shots are lost, ball flight now")
        page.click(".demo summary")
        page.select_option("#demo-select", "DEMO-13"); page.click("#demo-load")
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        page.evaluate("Fitter.goQ(2)")
        page.click('.q-step[data-q="2"] input[value="carry"] + span')   # carry only: the weaker evidence for flex
        page.fill('input[name="carry_yd"]', "145")
        page.evaluate("Fitter.goQ(1)")
        page.select_option('select[name="score_representative"]', "several")
        page.evaluate("Fitter.goQ(5)")
        page.click('input[name="lose_shots"][value="irons"] + span')
        page.evaluate("Fitter.goQ(3)")
        d0 = page.get_attribute("#flight-path", "d")
        page.click('input[name="ball_start"][value="left"] + span')
        page.click('input[name="ball_curve"][value="right"] + span')
        page.click('input[name="ball_height"][value="lower"] + span')
        check(page.get_attribute("#flight-path", "d") != d0, "the flight diagram follows the answers")
        expect(page.locator("#flight-words")).to_contain_text("fades or slices for a right-handed golfer", timeout=5000)
        page.evaluate("Fitter.goQ(8)")
        page.click('input[name="handedness"][value="left"] + span')
        page.evaluate("Fitter.goQ(3)")
        expect(page.locator("#flight-words")).to_contain_text("draws or hooks for a left-handed golfer", timeout=5000)
        page.screenshot(path=shots / "e1-game.png", full_page=True)
        page.evaluate("Fitter.goQ(8)")
        page.click('input[name="handedness"][value="right"] + span')
        page.click("#quiz-submit")
        expect(page.locator("#verdict")).to_be_visible()

        print("evidence behind the fit")
        ev = page.locator("#verdict .evidence")
        expect(ev.locator(".player")).to_contain_text("you lose most shots with your irons")
        expect(ev.locator(".player")).to_contain_text("over several recent rounds")
        expect(ev.locator(".compare-heads")).to_contain_text("Your two head options")
        expect(ev.locator(".compare-heads")).to_contain_text("contact consistency is the part we still need to check")
        head = page.evaluate("Fitter.state().full_recommendation.result.best.head_name")
        alt = page.evaluate("Fitter.state().full_recommendation.result.runner_up.head_name")
        expect(ev.locator(".compare-heads")).to_contain_text(head)
        expect(ev.locator(".compare-heads")).to_contain_text(alt)
        check(True, f"the comparison names the kit's best ({head}) and runner-up ({alt}) heads")
        ev.locator(".evidence-map summary").click()
        expect(ev.locator(".ev-row")).to_have_count(5)
        expect(ev.locator(".ev-row").nth(1)).to_contain_text("fallback: carry varies with loft and brand")
        check(True, "five decisions with their evidence; carry is marked as the weaker fallback")
        page.screenshot(path=shots / "e2-evidence.png", full_page=True)

        print("strike test")
        ev.locator("[data-strike]").click()
        expect(page.locator("#strike")).to_be_visible()
        page.locator("#strike-input").set_input_files(str(TMP / "face.jpg"))
        expect(page.locator("#strike-prompt")).to_contain_text("heel end")
        tap(page, *heel); tap(page, *toe)
        page.wait_for_function("Strike._debug().marks.length === 5", timeout=15000)
        check(True, "five marks found between heel and toe")
        m = page.evaluate("Strike._debug().marks[0]")
        tap(page, m["x"], m["y"])
        check(page.evaluate("Strike._debug().marks.length") == 4, "tapping a mark removes it")
        tap(page, m["x"], m["y"])
        page.wait_for_function("Strike._debug().marks.length === 5 && Strike._debug().analysis", timeout=10000)
        expect(page.locator("#strike-result")).to_contain_text("spread widely")
        page.screenshot(path=shots / "e3-strike.png", full_page=True)
        page.click("#strike-save")
        expect(page.locator("#verdict .evidence .player")).to_contain_text("inconsistent iron contact")
        expect(page.locator("#verdict .compare-heads")).to_contain_text("spread widely")
        check(page.evaluate("Fitter.state().recommendation.stale") is False, "the strike test explains the fit without changing it")
        b.close()
    if errors:
        raise SystemExit("console errors:\n" + "\n".join(errors))
    print("OK: evidence features verified")


if __name__ == "__main__":
    main()
