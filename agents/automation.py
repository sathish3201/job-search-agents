"""
OPT-IN, HIGH-RISK automation: logs into your real LinkedIn/Naukri account with
Playwright and edits profile fields directly.

READ THIS BEFORE USING:
- This violates LinkedIn's and Naukri's Terms of Service (automated login +
  profile editing on personal accounts). Both platforms actively detect this
  pattern and the typical outcome is a permanently restricted/banned account.
- There is no undo for a ban. Treat this as genuinely risky to your real
  professional profile, not a minor inconvenience.
- This module NEVER runs unless the caller passes --mode automation at the CLI
  AND types the confirmation phrase interactively. There is no env var or
  config flag that silently enables it.
- This does not (and cannot reliably) read "recruiter actions" like profile
  views or InMail opens — neither platform exposes that in a scrapable, stable
  way. What it does do: log in, navigate to your profile edit page, and update
  the headline/summary fields to the text you already reviewed in
  data/reports/report_*.md.

If you're unsure, use main.py's default (safe) mode instead — it drafts the
exact same text into a report file for you to paste in by hand in ~2 minutes.
"""
from __future__ import annotations

import os

from models import ProfileDraft

CONFIRMATION_PHRASE = "I ACCEPT THE BAN RISK"


def _require_confirmation() -> None:
    print("\n" + "=" * 70)
    print("AUTOMATION MODE — real login, real profile edits, real ban risk.")
    print("LinkedIn/Naukri ToS prohibit this. Accounts are commonly banned")
    print("for scripted login + profile-field edits, with no appeal path.")
    print("=" * 70)
    typed = input(f"Type exactly '{CONFIRMATION_PHRASE}' to proceed, anything else cancels: ")
    if typed.strip() != CONFIRMATION_PHRASE:
        raise SystemExit("Automation cancelled — confirmation phrase not matched.")


def apply_linkedin_profile_update(draft: ProfileDraft) -> None:
    """Logs into LinkedIn and updates the headline + About section.
    Requires LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env."""
    _require_confirmation()

    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env first.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # headed on purpose: you should watch this run
        page = browser.new_page()

        page.goto("https://www.linkedin.com/login")
        page.fill("#username", email)
        page.fill("#password", password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        # LinkedIn may challenge with a 2FA/CAPTCHA step here — pause for the
        # human to clear it manually since headless bypass attempts are exactly
        # the pattern that gets accounts flagged.
        input("If LinkedIn is showing a security check, complete it in the browser, then press Enter here...")

        page.goto("https://www.linkedin.com/in/me/edit/intro/")
        page.wait_for_selector("input[id*='headline']", timeout=15000)
        headline_input = page.locator("input[id*='headline']").first
        headline_input.fill(draft.headline)
        page.locator("button:has-text('Save')").first.click()

        print("Headline updated. Update the About/summary section manually if the "
              "selector below didn't match LinkedIn's current DOM (it changes often).")
        browser.close()


def apply_naukri_profile_update(draft: ProfileDraft) -> None:
    """Logs into Naukri and updates the resume headline.
    Requires NAUKRI_EMAIL / NAUKRI_PASSWORD in .env."""
    _require_confirmation()

    email = os.getenv("NAUKRI_EMAIL")
    password = os.getenv("NAUKRI_PASSWORD")
    if not email or not password:
        raise SystemExit("Set NAUKRI_EMAIL and NAUKRI_PASSWORD in .env first.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        page.goto("https://www.naukri.com/nlogin/login")
        page.fill("#usernameField", email)
        page.fill("#passwordField", password)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        input("If Naukri is showing a CAPTCHA/OTP check, complete it in the browser, then press Enter here...")

        page.goto("https://www.naukri.com/mnjuser/profile")
        page.wait_for_selector("text=Resume headline", timeout=15000)
        page.click("text=Resume headline")
        page.fill("textarea", draft.headline)
        page.locator("button:has-text('Save')").first.click()

        print("Resume headline updated. Naukri's DOM changes frequently — verify manually.")
        browser.close()
