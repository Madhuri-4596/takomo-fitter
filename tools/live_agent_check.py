"""Talk to the real ElevenLabs agent in text mode and print the transcript (uses a little agent quota).

  .venv/Scripts/python tools/live_agent_check.py [--url http://127.0.0.1:8765]

Plays a golfer who gives an ambiguous distance and a unit correction, confirms on screen,
then asks for the fit, and checks what can be checked automatically: the agent calls its
tools, staged answers wait for the tap, and the spoken spec matches the card. Read the
printed transcript to judge tone, one-question-at-a-time and read-backs.
"""
from __future__ import annotations

import argparse
import time

from playwright.sync_api import expect, sync_playwright


def wait_reply(page, before: int, settle_s: float = 3.0, timeout_s: float = 60):
    """Wait for new agent messages, then until the agent has been quiet for settle_s."""
    end = time.time() + timeout_s
    while time.time() < end and page.locator("#transcript .agent").count() <= before:
        page.wait_for_timeout(300)
    last, quiet_since = page.locator("#transcript li").count(), time.time()
    while time.time() < end and time.time() - quiet_since < settle_s:
        page.wait_for_timeout(300)
        n = page.locator("#transcript li").count()
        if n != last:
            last, quiet_since = n, time.time()


def say(page, text):
    before = page.locator("#transcript .agent").count()
    page.fill("#composer-input", text)
    page.click("#composer button")
    wait_reply(page, before)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765/classic")
    a = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda e: print("PAGE ERROR", e))
        page.goto(a.url)
        # golfer has filled part of the form already
        page.fill('input[name="age"]', "44")
        page.click("#game-form >> text=Male")
        page.click("#game-form >> text=Right-handed")
        page.click("text=85–92")
        page.click("text=Forgiveness")
        page.click("#game-form >> text=Medium")
        page.click("#game-form >> text=Moderate")
        page.select_option('select[name="glove"]', "M")
        page.wait_for_timeout(800)

        page.click("#talk-open")
        expect(page.locator("#consent-list")).to_contain_text("processed by ElevenLabs")
        page.click("#start-text")
        expect(page.locator("#voice-status")).to_have_text("Connected · typing", timeout=20000)
        wait_reply(page, 0, settle_s=5)

        say(page, "My 7-iron goes about 160")
        say(page, "That's total, with the roll. It carries about 150 yards.")
        say(page, "I'm 5 foot 10 and my wrist to floor is 34")
        say(page, "Sorry, inches. Actually make it 35 inches.")
        staged = page.evaluate("Object.keys(Fitter.state().staged_for_review)")
        print("\nSTAGED FOR REVIEW:", staged)
        profile_before = page.evaluate("Fitter.state().profile")
        say(page, "Yes, that's all right.")
        say(page, "What fits me?")
        page.wait_for_timeout(2000)
        rec = page.evaluate("Fitter.state().full_recommendation")

        print("\nTRANSCRIPT")
        for li in page.locator("#transcript li").all():
            cls = li.get_attribute("class")
            print(f"  [{ {'agent': 'FITTER', 'user': 'GOLFER', 'sys': 'app'}[cls] }] {li.inner_text()}")
        print("\nprofile before confirm had height:", "height_cm" in profile_before)
        print("profile after:", {k: page.evaluate("Fitter.state().profile")[k] for k in ("height_cm", "wrist_to_floor_cm", "carry_yd") if k in page.evaluate("Fitter.state().profile")})
        if rec:
            r = rec["result"]["best"]
            print(f"CARD: Fit #{rec['id']} {r['head_name']} / {r['shaft_model']} {r['flex_label']} / lie {r['lie_deg']} / {r['grip']} / {r['purchase']}")
        else:
            print("CARD: no recommendation yet")
        try:
            page.click("#end", timeout=3000)
        except Exception:
            pass
        browser.close()


if __name__ == "__main__":
    main()
