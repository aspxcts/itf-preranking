"""
ITF Authentication Module
==========================

Implements Playwright-based login to ipin.itftennis.com using Azure AD B2C.
Mirrors the pattern from itf_preseeding/auth.py exactly.

The login flow seeds Incapsula cookies on both www.itftennis.com and
ipin.itftennis.com, then performs credential-based auth via Azure AD B2C,
resulting in a verified human browser session that Incapsula allows through.

Usage (in /api/login endpoint):
    result = await login(email, password)
    # result = {
    #   "cookies": {name: value, ...},      # All cookies from context (ARRAffinity, etc.)
    #   "email": email,
    # }
"""

from __future__ import annotations

import json as _json
import os
import sys
from datetime import datetime, timezone

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)

_WWW_URL = "https://www.itftennis.com/en/tournament-calendar/world-tennis-tour-juniors-calendar/"
_IPIN_URL = "https://ipin.itftennis.com"
_LOGIN_URL = (
    "https://login.itftennis.com"
    "/iditftennis.onmicrosoft.com/b2c_1a_signin/oauth2/v2.0/authorize"
    "?client_id=1e3b8f48-f6d9-47f4-8b3f-0e066732d693"
    "&redirect_uri=https%3A%2F%2Fipin.itftennis.com"
    "&response_mode=form_post&response_type=id_token&scope=openid"
    "&clientId=itf-players-portal"
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class LoginError(Exception):
    """Raised when ITF login fails."""


async def login(email: str, password: str) -> dict | None:
    """
    Log into ipin.itftennis.com using email + password via Azure AD B2C.

    Steps:
      1. Seed www.itftennis.com cookies (Incapsula)
      2. Seed ipin.itftennis.com cookies (Incapsula)
      3. Navigate to Azure AD B2C login form
      4. Fill signInName + password, submit
      5. Wait for redirect back to ipin (networkidle, 30s)
      6. Harvest ALL cookies from context
      7. Return {cookies, email} or None on failure

    Args:
        email: ITF account email
        password: ITF account password

    Returns:
        {
            "cookies": {name: value, ...},
            "email": email,
        }
        or None if login failed
    """
    async with async_playwright() as p:
        in_container = os.environ.get("K_SERVICE") or os.environ.get("DOCKER_ENV")
        launch_args = [
            "--no-sandbox", "--disable-setuid-sandbox"
        ] if in_container else []

        browser = await p.chromium.launch(headless=True, args=launch_args)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=_USER_AGENT,
        )
        page = await context.new_page()

        try:
            # Step 1: www warm-up (seed Incapsula cookies)
            print("[auth] Step 1: Loading www.itftennis.com for Incapsula seed...", file=sys.stderr)
            try:
                await page.goto(_WWW_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[auth] www warm-up ended early ({e}) -- continuing.", file=sys.stderr)

            # Step 2: ipin warm-up (seed Incapsula cookies)
            print("[auth] Step 2: Loading ipin.itftennis.com for Incapsula seed...", file=sys.stderr)
            try:
                await page.goto(_IPIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[auth] ipin warm-up ended early ({e}) -- continuing.", file=sys.stderr)

            # Step 3: Navigate to Azure AD B2C login form
            print(f"[auth] Step 3: Navigating to ITF login form for {email}...", file=sys.stderr)
            try:
                await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(
                    'input[name="signInName"], input[type="email"], #signInName, #email',
                    timeout=15_000,
                )
            except PlaywrightTimeout as e:
                print(f"[auth] Login form not found: {e}", file=sys.stderr)
                return None

            # Step 4: Fill credentials and submit
            print("[auth] Step 4: Filling credentials and submitting...", file=sys.stderr)
            try:
                await page.fill(
                    'input[name="signInName"], input[type="email"], #signInName, #email',
                    email,
                )
                await page.fill(
                    'input[name="password"], input[type="password"], #password',
                    password,
                )
                await page.locator("#next, button[type='submit']").first.click()
            except Exception as e:
                print(f"[auth] Failed to fill/submit form: {e}", file=sys.stderr)
                return None

            # Step 5: Wait for redirect back to ipin
            print("[auth] Step 5: Waiting for login completion (redirect to ipin)...", file=sys.stderr)
            try:
                await page.wait_for_url(
                    f"{_IPIN_URL}/**",
                    wait_until="networkidle",
                    timeout=30_000,
                )
                print("[auth] Login successful!", file=sys.stderr)
            except PlaywrightTimeout:
                current_url = page.url
                if "login.itftennis.com" in current_url:
                    print(f"[auth] Login failed -- still on {current_url}", file=sys.stderr)
                    return None
                print(f"[auth] Redirect timeout but at {current_url} -- continuing...", file=sys.stderr)

            # Step 6: Harvest cookies — keep full objects so domain info is preserved
            cookies = await context.cookies()
            cookie_names = {c["name"] for c in cookies}

            # Check for key session cookies
            key_cookies = ["ARRAffinity", "ARRAffinitySameSite", ".AspNet"]
            found = [k for k in key_cookies if k in cookie_names]
            if found:
                print(f"[auth] Harvested session cookies: {', '.join(found)}", file=sys.stderr)
            else:
                print(f"[auth] WARNING: No key session cookies found", file=sys.stderr)

            domains = set(c["domain"] for c in cookies)
            print(f"[auth] Total cookies: {len(cookies)} across domains: {domains}", file=sys.stderr)

            # Serialize only the fields needed by context.add_cookies()
            cookie_list = [
                {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"]}
                for c in cookies
            ]
            return {
                "cookies": cookie_list,
                "email": email,
            }

        except Exception as e:
            print(f"[auth] Unexpected error: {e}", file=sys.stderr)
            return None

        finally:
            await browser.close()
