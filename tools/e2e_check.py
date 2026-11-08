"""End-to-end browser check of the four-step form flow at phone size.

Start the server first, then:
  .venv/Scripts/python tools/e2e_check.py [--url http://127.0.0.1:8765] [--shots DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TRUTH = json.loads((ROOT / "app" / "static" / "sample" / "sample-body.json").read_text())


def tap_image_point(page, x, y, image_width):
    """Tap the canvas where pixel (x, y) of the original image is drawn (the app may downscale it)."""
    page.evaluate("document.querySelector('#photo-canvas').scrollIntoView({block: 'center', behavior: 'instant'})")
    # wait until any smooth scroll started by the app has settled
    page.wait_for_function("""() => new Promise(ok => { const y = scrollY;
        setTimeout(() => ok(scrollY === y), 150); })""")
    pos = page.evaluate("""([x, y, iw]) => {
        const c = document.querySelector('#photo-canvas');
        x *= c.width / iw; y *= c.width / iw;
        const r = c.getBoundingClientRect();
        const s = Math.min(r.width / c.width, r.height / c.height);
        const ox = (r.width - c.width * s) / 2, oy = (r.height - c.height * s) / 2;
        return {x: r.left + ox + x * s, y: r.top + oy + y * s};
    }""", [x, y, image_width])
    page.mouse.click(pos["x"], pos["y"])


def tap_truth_points(page):
    for k in ("head_top", "wrist", "floor"):
        tap_image_point(page, *TRUTH["points"][k], TRUTH["image_size"][0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    ap.add_argument("--shots", default=str(ROOT / "screenshots"))
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, has_touch=True)
        page.on("console", lambda m: m.type == "error" and "cdn.jsdelivr" not in m.text and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(a.url)
        no_hscroll = "document.documentElement.scrollWidth <= window.innerWidth"

        # ---- home -> demo golfer -> quiz (prefilled) -> verdict
        expect(page.locator("#home")).to_be_visible()
        expect(page.locator("#go-fitter")).to_contain_text("Talk to the fitter")
        expect(page.locator("#go-quiz")).to_contain_text("Quick quiz")
        page.screenshot(path=shots / "1-home.png", full_page=True)
        page.click(".demo summary")
        expect(page.locator("#demo-select option")).to_have_count(17)
        page.select_option("#demo-select", "DEMO-13")
        page.click("#demo-load")
        expect(page.locator('.q-step[data-q="8"]')).to_be_visible()
        expect(page.locator('input[name="wtf"]')).to_have_value("88.9")
        expect(page.locator("#rail .settled")).to_have_count(5)
        assert page.evaluate(no_hscroll), "horizontal scroll on the quiz"
        page.click("#quiz-submit")

        expect(page.locator("#verdict")).to_be_visible()
        fit = page.locator("#verdict .fit")
        expect(fit.locator(".rec-card h3")).to_have_text("Iron 201T MKII")
        expect(fit.locator(".rec-card .build")).to_have_text("Custom build")
        for row in ("Head", "Shaft", "Length", "Lie", "Grip"):
            expect(fit.locator(f'details[data-row="{row}"] .conf')).to_have_count(1)
        expect(fit.locator('details[data-row="Lie"] .v')).to_contain_text("+1° upright")
        fit.locator('details[data-row="Lie"] summary').click()
        expect(fit.locator('details[data-row="Lie"] .why-long')).to_contain_text("wrist-to-floor")
        page.screenshot(path=shots / "2-verdict.png", full_page=True)
        page.click("#verdict [data-go-measure]")

        # ---- measure: the demo's simulated photo result, then confirm
        expect(page.locator("#step-3")).to_be_visible()
        page.click("#use-simulated")
        expect(page.locator("#measure-confirm")).to_be_visible()
        expect(page.locator("#measure-result")).to_contain_text("84.9 cm")
        page.screenshot(path=shots / "3-measure.png", full_page=True)
        # near a band edge that would flip stock/custom: the check screen asks for a tape before confirming
        expect(page.locator("#measure-result .boundary")).to_have_text("A small measurement change could affect your build. Please check with a tape.")
        page.click("#measure-confirm")
        expect(page.locator("#swing-screen")).to_be_visible()                      # the journey moves on to the swing
        page.click('#swing-screen [data-journey="verdict"]')
        expect(page.locator("#verdict")).to_be_visible()
        expect(fit.locator(".rec-card .build")).to_have_text("Stock set")
        expect(fit.locator("details.changed")).to_have_count(1)                     # the lie
        expect(page.locator("#changes")).to_contain_text("stock set")
        expect(page.locator("#decision")).to_contain_text("Please check with a tape before ordering custom")   # 1.8 cm from a band edge that would flip stock/custom
        page.screenshot(path=shots / "4-verdict-measured.png", full_page=True)
        assert page.evaluate(no_hscroll), "horizontal scroll on the verdict"
        page.click("#review-order")
        expect(page.locator("#summary")).to_contain_text("Iron 201T MKII")

        # ---- second run: the quiz, one question at a time, tape-measure route, oversize grip -> custom
        page.click("#restart")
        expect(page.locator("#home")).to_be_visible()
        page.click("#go-quiz")
        q = lambda n: page.locator(f'.q-step[data-q="{n}"]')
        page.fill('input[name="score_value"]', "88"); page.click("#quiz-next")
        q(2).locator("text=I know my swing speed").click()
        page.fill('input[name="speed_mph"]', "84"); page.click("#quiz-next")
        q(3).locator("label:has-text('Medium')").click(); page.click("#quiz-next")
        q(4).locator("text=Moderate").click(); page.click("#quiz-next")
        q(5).locator('input[value="forgiveness"] + span').click(); page.click("#quiz-next")
        q(6).locator("text=inches").click()
        page.fill('input[name="height"]', "72"); page.fill('input[name="wtf"]', "35"); page.click("#quiz-next")
        page.select_option('select[name="glove"]', "XL"); page.click("#quiz-next")
        q(8).locator("text=Left-handed").click()
        page.fill('input[name="age"]', "52")
        q(8).locator('input[value="male"] + span').click()
        page.click("#quiz-submit")
        expect(page.locator("#verdict")).to_be_visible()
        expect(fit.locator('details[data-row="Grip"] .v')).to_contain_text("Oversize")
        expect(fit.locator('details[data-row="Hand"] .v')).to_contain_text("Left-handed")
        expect(fit.locator('details[data-row="Shaft"] .v')).to_contain_text("Stiff")
        page.click("#verdict [data-go-measure]")
        expect(page.locator("#use-simulated")).to_be_hidden()
        page.click("#tape summary")
        page.fill("#manual-height", "183")
        page.fill("#manual-wtf", "86")
        page.click("#manual-use")
        page.click("#measure-confirm")
        expect(page.locator("#swing-screen")).to_be_visible()
        page.evaluate("Fitter.showScreen('verdict')")
        expect(fit.locator(".rec-card .build")).to_have_text("Custom build")
        expect(page.locator("#decision")).to_contain_text("oversize grip")
        page.screenshot(path=shots / "5-second-run.png", full_page=True)
        browser.close()
    if errors:
        raise SystemExit("console errors:\n" + "\n".join(errors))
    print("OK: form flow verified in the browser; screenshots in", shots)


if __name__ == "__main__":
    main()
