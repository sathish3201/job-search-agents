"""OPT-IN, HIGH-RISK automation: submits real job applications via a real
LinkedIn login and a headed Playwright browser.

READ THIS BEFORE USING:
- Submitting automated applications violates LinkedIn's and most job
  boards' Terms of Service. Accounts used for this can be flagged or
  banned, with no appeal path. This is real risk to your real account,
  not a minor inconvenience.
- LOCAL-ONLY BY DESIGN, same pattern as agents/automation.py: this must
  never run on Render (or any headless/unattended server). A human needs
  to be physically present to solve CAPTCHAs/2FA and to watch the actual
  submission happen — there is no reliable headless bypass, and attempting
  one is exactly the pattern that gets accounts flagged. The API layer
  that calls this (api/routers/applications.py) is meant to be hit from
  your own machine's locally-running backend, not the deployed one.
- Never runs without the caller passing the confirmation phrase — no env
  var or config flag silently enables it.
- Only handles LinkedIn's "Easy Apply" flow with real automation (the
  most standardized apply UI, worth the investment). Every other site
  (Naukri, company ATS pages like Greenhouse/Lever) gets a generic
  fallback: the job posting opens in a headed browser for the human to
  fill out and submit manually. Reverse-engineering dozens of bespoke
  apply-form layouts isn't worth it for a first version — opening the
  right page and getting out of the way is still real value.
- Human approval happens BEFORE this module is ever called (see the
  dashboard's Apply button) — this module only executes an apply the user
  already reviewed and explicitly approved for this specific job.
"""
from __future__ import annotations

import os

CONFIRMATION_PHRASE = "I ACCEPT THE BAN RISK"


class ApplyResult:
    def __init__(self, success: bool, message: str, applied_url: str = ""):
        self.success = success
        self.message = message
        self.applied_url = applied_url


def _require_confirmation(confirmation_phrase: str) -> None:
    if confirmation_phrase != CONFIRMATION_PHRASE:
        raise ValueError(
            f"Confirmation phrase mismatch — must be exactly {CONFIRMATION_PHRASE!r}. "
            "Application not submitted."
        )


def _is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in url


def apply_to_job(job_url: str, confirmation_phrase: str) -> ApplyResult:
    """Entry point: routes to the LinkedIn Easy Apply handler for LinkedIn
    URLs, generic manual-assist for everything else. Always headed (never
    headless) — this is a submission action, not a read-only scrape, so a
    human watching is the safety mechanism, not optional."""
    _require_confirmation(confirmation_phrase)

    if _is_linkedin_url(job_url):
        return _apply_linkedin_easy_apply(job_url)
    return _apply_generic_manual_assist(job_url)


def _apply_linkedin_easy_apply(job_url: str) -> ApplyResult:
    """Logs into LinkedIn and attempts the "Easy Apply" flow: click Easy
    Apply, click through any additional-info steps without answering
    unknown questions (those get left for the human to fill), and pause
    before the final submit so the human confirms it's correct.

    Requires LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env — same credentials
    agents/automation.py already uses for profile edits."""
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")
    if not email or not password:
        return ApplyResult(False, "Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env first.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # headed on purpose: you should watch this run
        page = browser.new_page()

        try:
            page.goto("https://www.linkedin.com/login")
            page.fill("#username", email)
            page.fill("#password", password)
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")

            # Same reasoning as agents/automation.py: pause for the human to
            # clear any 2FA/CAPTCHA challenge rather than attempting a
            # headless bypass, which is exactly the pattern that gets
            # accounts flagged.
            input(
                "If LinkedIn is showing a security check, complete it in the browser, "
                "then press Enter here..."
            )

            page.goto(job_url)
            page.wait_for_load_state("networkidle")

            easy_apply_button = page.locator("button:has-text('Easy Apply')").first
            if not easy_apply_button.is_visible(timeout=5000):
                print(
                    "No 'Easy Apply' button found on this posting — it likely applies "
                    "through an external site instead. Leaving the browser open for you "
                    "to apply manually."
                )
                input("Press Enter once you've finished (or decided not to apply)...")
                browser.close()
                return ApplyResult(
                    False,
                    "No Easy Apply button found — this posting requires an external "
                    "application, opened for manual completion.",
                    applied_url=job_url,
                )

            easy_apply_button.click()
            page.wait_for_timeout(1000)

            # Easy Apply is a multi-step wizard (contact info -> resume ->
            # optional screening questions -> review -> submit). Screening
            # questions vary per posting and this doesn't attempt to guess
            # answers — that's exactly the kind of thing a human should
            # confirm, not something worth risking a wrong/dishonest answer
            # over. Click "Next"/"Review" while visible, pausing for the
            # human whenever neither is available (a question needs input).
            for _ in range(10):  # cap iterations — never loop forever on a stuck step
                next_btn = page.locator("button:has-text('Next')").first
                review_btn = page.locator("button:has-text('Review')").first
                if next_btn.is_visible(timeout=2000):
                    next_btn.click()
                    page.wait_for_timeout(800)
                    continue
                if review_btn.is_visible(timeout=2000):
                    review_btn.click()
                    page.wait_for_timeout(800)
                    continue
                break

            submit_btn = page.locator("button:has-text('Submit application')").first
            if submit_btn.is_visible(timeout=3000):
                print(
                    "Reached the final 'Submit application' step. The browser is paused "
                    "here — review everything LinkedIn is about to submit before continuing."
                )
                confirm = input("Type 'submit' to actually submit this application, anything else cancels: ")
                if confirm.strip().lower() == "submit":
                    submit_btn.click()
                    page.wait_for_timeout(1500)
                    browser.close()
                    return ApplyResult(True, "Application submitted via LinkedIn Easy Apply.", applied_url=job_url)
                browser.close()
                return ApplyResult(False, "Submission cancelled by user at the final review step.")

            print(
                "Could not reach the final submit step automatically (a screening "
                "question likely needs an answer). Finish it manually in the browser."
            )
            input("Press Enter once you've finished (or decided not to apply)...")
            browser.close()
            return ApplyResult(
                False,
                "Easy Apply flow needed manual input to complete — left open for you.",
                applied_url=job_url,
            )
        except Exception as e:
            browser.close()
            return ApplyResult(False, f"Easy Apply automation failed: {e}")


def _apply_generic_manual_assist(job_url: str) -> ApplyResult:
    """For any non-LinkedIn URL: just open the posting in a headed browser
    and let the human fill out and submit whatever apply form the site
    uses. Not real automation, but still saves the step of finding and
    opening the link, and keeps every apply action going through the same
    reviewed-and-approved flow regardless of source."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        try:
            page.goto(job_url)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            browser.close()
            return ApplyResult(False, f"Failed to open job posting: {e}")

        print(f"Opened {job_url} for manual application — this site isn't automated yet.")
        input("Press Enter once you've finished (or decided not to apply)...")
        browser.close()
        return ApplyResult(
            True,
            "Job posting opened for manual application — confirm you actually applied.",
            applied_url=job_url,
        )
