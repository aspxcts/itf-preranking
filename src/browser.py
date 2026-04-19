"""
Playwright-based browser session for the ITF website (www.itftennis.com).

Auth strategy — mirrors itf_preseeding/auth.py exactly:

  Warm-up (first run / stale cookies):
    1. Visit www.itftennis.com calendar page (domcontentloaded + 2s) so
       Incapsula issues its session cookies for the www domain.
    2. Visit ipin.itftennis.com (domcontentloaded + 2s) — seeds Incapsula
       cookies for the ipin domain AND establishes a real-browser fingerprint.
    3. Log in with ITF credentials (ITF_EMAIL / ITF_PASSWORD env vars) via the
       Azure AD B2C form — exactly as auth.py does it. This elevates the
       browser session from "possibly bot" to "verified human with ITF account",
       which resolves GCP-IP Incapsula blocks.
    4. Navigate back to www.itftennis.com in the same authenticated context so
       www Incapsula cookies are refreshed under the now-verified session.
    5. Persist ALL cookies (both domains) to Firestore.

  Subsequent runs (warm cookies in Firestore):
    context.add_cookies(saved) -- skip the full login flow.

  API calls:
    page.goto(api_url, domcontentloaded, 20s) + page.on("response", _capture)
    -- same as auth.py fetch_json(). Real Chromium presents valid cookies, no
    challenge.

  POST calls:
    context.request.post() -- shares cookies, no navigation.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)

_WWW_WARM_UP_URL = (
    "https://www.itftennis.com"
    "/en/tournament-calendar/world-tennis-tour-juniors-calendar/"
)
_IPIN_URL = "https://ipin.itftennis.com"
_LOGIN_URL = (
    "https://login.itftennis.com"
    "/iditftennis.onmicrosoft.com/b2c_1a_signin/oauth2/v2.0/authorize"
    "?client_id=1e3b8f48-f6d9-47f4-8b3f-0e066732d693"
    "&redirect_uri=https%3A%2F%2Fipin.itftennis.com"
    "&response_mode=form_post&response_type=id_token&scope=openid"
    "&clientId=itf-players-portal"
)
# Real ITF page used as the navigation host for POST fetch() calls.
# Landing on a real page seeds Incapsula incap_ses_* cookies for the
# draw-results sub-application and provides a legitimate Referer header,
# so Chromium's fetch() is accepted by Incapsula on GCP IPs.
_POST_FROM_URL = "https://www.itftennis.com/en/draw-results/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_FIRESTORE_COLLECTION = "itf_sessions"
_FIRESTORE_DOC = "incapsula_cookies"
_PIPELINE_RELAY_DOC = "pipeline_relay_cookies"  # Separate doc for authenticated pipeline relay


class SessionError(Exception):
    """Raised when an ITF API call fails after all retries."""


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _load_relay_cookies() -> Optional[list[dict]]:
    """Load pipeline relay cookies saved by a prior step (e.g. main.py)."""
    try:
        from google.cloud import firestore
        db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
        )
        doc = db.collection(_FIRESTORE_COLLECTION).document(_PIPELINE_RELAY_DOC).get()
        if doc.exists:
            data = doc.to_dict()
            saved_at_str = data.get("saved_at", "")
            if saved_at_str:
                from datetime import timedelta
                saved_at = datetime.fromisoformat(saved_at_str)
                age = datetime.now(timezone.utc) - saved_at
                if age.total_seconds() > 25 * 60:
                    print(f"[browser] Relay cookies are {int(age.total_seconds() // 60)} min old -- ignoring.")
                    return None
            cookies = data.get("cookies", [])
            print(f"[browser] Loaded {len(cookies)} relay cookies from Firestore "
                  f"(saved {data.get('saved_at', '?')})")
            return cookies
    except Exception as e:
        print(f"[browser] Could not load relay cookies: {e}")
    return None


def _save_relay_cookies(cookies: list[dict]) -> None:
    """Save pipeline relay cookies so the next step can reuse the live session."""
    try:
        from google.cloud import firestore
        db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
        )
        db.collection(_FIRESTORE_COLLECTION).document(_PIPELINE_RELAY_DOC).set({
            "cookies": cookies,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[browser] Saved {len(cookies)} relay cookies to Firestore.")
    except Exception as e:
        print(f"[browser] Could not save relay cookies: {e}")


def _load_firestore_cookies(max_age_hours: float = 20.0, min_cookies: int = 6) -> Optional[list[dict]]:
    """Load warm-up cookies saved by a previous login or pipeline run.

    Ignores cookies older than *max_age_hours* so a stale entry from a
    prior run can't block a fresh login from taking effect.

    Ignores entries with fewer than *min_cookies* cookies — a bare-minimum
    Incapsula entry (2 cookies) from a failed anonymous warm-up is not usable
    on GCP IPs and should not be trusted.
    """
    try:
        from google.cloud import firestore
        db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
        )
        doc = db.collection(_FIRESTORE_COLLECTION).document(_FIRESTORE_DOC).get()
        if doc.exists:
            data = doc.to_dict()
            saved_at_str = data.get("saved_at", "")
            if saved_at_str:
                saved_at = datetime.fromisoformat(saved_at_str)
                age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600
                if age_hours > max_age_hours:
                    print(f"[browser] Firestore cookies are {age_hours:.1f}h old — ignoring.")
                    return None
            cookies = data.get("cookies", [])
            print(f"[browser] Loaded {len(cookies)} cookies from Firestore "
                  f"(saved {data.get('saved_at', '?')})")
            if len(cookies) < min_cookies:
                print(f"[browser] Too few cached cookies ({len(cookies)} < {min_cookies}) — ignoring.")
                return None
            return cookies
    except Exception as e:
        print(f"[browser] Could not load Firestore cookies: {e}")
    return None


def _save_firestore_cookies(cookies: list[dict]) -> None:
    try:
        from google.cloud import firestore
        db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
        )
        db.collection(_FIRESTORE_COLLECTION).document(_FIRESTORE_DOC).set({
            "cookies": cookies,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[browser] Saved {len(cookies)} cookies to Firestore.")
    except Exception as e:
        print(f"[browser] Could not save Firestore cookies: {e}")


def _delete_firestore_cookies() -> None:
    try:
        from google.cloud import firestore
        db = firestore.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
        )
        db.collection(_FIRESTORE_COLLECTION).document(_FIRESTORE_DOC).delete()
        print("[browser] Firestore cookie cache invalidated.")
    except Exception as e:
        print(f"[browser] Could not delete Firestore cookies: {e}")


# ---------------------------------------------------------------------------
# BrowserSession
# ---------------------------------------------------------------------------

class BrowserSession:
    """
    Async context manager providing authenticated access to the ITF public API.

    Two usage modes:

    1. **Anonymous warm-up** (no credentials):
       Visits www.itftennis.com for a basic warm-up. Only succeeds on non-GCP
       IPs; on GCP the ranking API will be Incapsula-blocked.

    2. **Authenticated warm-up** (ITF_EMAIL + ITF_PASSWORD env vars set):
       Full login flow (same as itf_preseeding/auth.py) so Incapsula sees a
       verified human session. Each pipeline step runs its own warm-up in its
       own browser context so cookies are fresh and fingerprint-matched.
       After the first successful GET, cookies are saved to Firestore as relay
       so subsequent pipeline steps can reuse them (skipping re-login).
    """

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._launch_args: list[str] = []
        self._saved_relay = False
        # Serialise concurrent rewarms: only the first failing coroutine for a
        # given context generation performs the actual invalidate+rewarm; all
        # others wait, then retry on the new (already-warmed) context.
        self._rewarm_lock = asyncio.Lock()
        self._context_generation = 0

    async def __aenter__(self) -> "BrowserSession":
        self._pw = await async_playwright().start()
        in_container = os.environ.get("K_SERVICE") or os.environ.get("DOCKER_ENV")
        self._launch_args = (
            ["--no-sandbox", "--disable-setuid-sandbox"] if in_container else []
        )
        self._browser = await self._pw.chromium.launch(
            headless=self.headless, args=self._launch_args
        )

        # 1. Prefer relay cookies saved by an earlier step in this pipeline run
        #    (main.py saves them after its first successful GET; subsequent steps
        #    reuse them without re-logging in).
        relay_cookies = _load_relay_cookies()
        if relay_cookies:
            print(f"[browser] Using relay cookies ({len(relay_cookies)} cookies) -- skipping warm-up.")
            self.context = await self._browser.new_context(user_agent=_USER_AGENT)
            await self.context.add_cookies(relay_cookies)
            return self

        # 2. Use the ITF session cookies seeded on login (< 20 h TTL).
        #    app.py calls _save_firestore_cookies() on every /api/login and on
        #    cold-start so this is normally populated before the pipeline runs.
        saved_cookies = _load_firestore_cookies()
        if saved_cookies:
            print("[browser] Reusing saved cookies -- skipping warm-up.")
            self.context = await self._browser.new_context(user_agent=_USER_AGENT)
            await self.context.add_cookies(saved_cookies)
            return self

        # 3. Fall back to a fresh warm-up.  Uses ITF_EMAIL / ITF_PASSWORD env
        #    vars when set (handy for local dev); otherwise anonymous (will fail
        #    Incapsula on GCP IPs if no valid session exists).
        await self._warm_up()
        return self

    async def _warm_up(self) -> None:
        """
        Full warm-up: seed www Incapsula cookies, log in to ipin so Incapsula
        sees a verified human, then refresh www cookies in the authenticated
        context. Mirrors auth.py step-by-step.
        """
        email = os.environ.get("ITF_EMAIL", "")
        password = os.environ.get("ITF_PASSWORD", "")

        self.context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=_USER_AGENT,
        )
        page = await self.context.new_page()

        # Step 1: www.itftennis.com warm-up (seed Incapsula cookies for www)
        print("[browser] Step 1: Loading www.itftennis.com for Incapsula seed...")
        try:
            await page.goto(_WWW_WARM_UP_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"[browser] www warm-up ended early ({e}) -- continuing.")

        if email and password:
            # Step 2: Visit ipin.itftennis.com to seed its Incapsula cookies
            print("[browser] Step 2: Loading ipin.itftennis.com for Incapsula seed...")
            try:
                await page.goto(_IPIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[browser] ipin warm-up ended early ({e}) -- continuing.")

            # Step 3: Navigate to Azure AD B2C login form
            print("[browser] Step 3: Navigating to ITF login page...")
            try:
                await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_selector(
                    'input[name="signInName"], input[type="email"], #signInName, #email',
                    timeout=15_000,
                )
                await page.fill(
                    'input[name="signInName"], input[type="email"], #signInName, #email',
                    email,
                )
                await page.fill(
                    'input[name="password"], input[type="password"], #password',
                    password,
                )
                print("[browser] Step 3: Submitting credentials...")
                await page.locator("#next, button[type='submit']").first.click()
                try:
                    await page.wait_for_url(
                        f"{_IPIN_URL}/**",
                        wait_until="networkidle",
                        timeout=30_000,
                    )
                    print("[browser] Step 3: Login successful.")
                except PlaywrightTimeout:
                    current = page.url
                    if "login.itftennis.com" in current:
                        print(f"[browser] WARNING: Login may have failed -- still on {current}")
                    else:
                        print(f"[browser] Step 3: Redirected to {current} -- continuing.")
            except Exception as e:
                print(f"[browser] Login flow error ({e}) -- continuing with basic cookies.")

            # Step 4: Navigate back to www in the now-authenticated context so
            # www Incapsula cookies are refreshed under the verified session
            print("[browser] Step 4: Re-visiting www.itftennis.com post-login...")
            try:
                await page.goto(_WWW_WARM_UP_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"[browser] Post-login www visit ended early ({e}) -- continuing.")
        else:
            print("[browser] No ITF_EMAIL/ITF_PASSWORD set -- using basic www warm-up only.")

        try:
            await asyncio.wait_for(page.close(), timeout=10.0)
        except Exception:
            pass

        cookies = await self.context.cookies()
        incap = [c for c in cookies if "incap" in c["name"].lower() or "visid" in c["name"].lower()]
        print(f"[browser] Warm-up done -- {len(cookies)} total cookies, {len(incap)} Incapsula.")
        # Only persist if the warm-up produced a meaningful session.
        # 2 bare Incapsula cookies (timed-out GCP warm-up) are not sufficient
        # for GetPlayerRankings; caching them just causes the next pipeline run
        # to trust and reuse garbage cookies.
        if len(cookies) > 5:
            _save_firestore_cookies(cookies)
        else:
            print(f"[browser] Warm-up yielded only {len(cookies)} cookies -- not caching to Firestore.")

    async def _do_rewarm(self) -> None:
        """Perform invalidate+rewarm. Must be called while holding _rewarm_lock."""
        _delete_firestore_cookies()
        try:
            from google.cloud import firestore
            db = firestore.Client(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT", "itf-live-rankings")
            )
            db.collection(_FIRESTORE_COLLECTION).document(_PIPELINE_RELAY_DOC).delete()
            print("[browser] Pipeline relay cleared.")
        except Exception as e:
            print(f"[browser] Could not clear relay: {e}")
        if self.context:
            try:
                await asyncio.wait_for(self.context.close(), timeout=10.0)
            except Exception:
                pass
            self.context = None
        self._context_generation += 1
        await self._warm_up()

    async def _invalidate_and_rewarm(self) -> None:
        async with self._rewarm_lock:
            await self._do_rewarm()

    async def __aexit__(self, *_) -> None:
        if self.context:
            try:
                await asyncio.wait_for(self.context.close(), timeout=10.0)
            except Exception:
                pass
        if self._browser:
            try:
                await asyncio.wait_for(self._browser.close(), timeout=10.0)
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()

    async def get(self, url: str, params: dict | None = None) -> dict:
        """
        Authenticated GET using page.expect_response() + page.goto().
        expect_response() captures the full response body before page.close()
        is called, avoiding the race condition where page.close() cancels an
        in-flight response.json() coroutine inside an on("response") handler.
        """
        full_url = f"{url}?{urlencode(params)}" if params else url

        for attempt in range(2):
            # If a concurrent rewarm is in progress (context=None), wait for it
            # to finish before attempting the GET rather than failing immediately.
            if self.context is None:
                async with self._rewarm_lock:
                    pass  # release immediately; just used to wait out the rewarm
            if self.context is None:
                raise SessionError(f"GET {url} -> no browser context (concurrent rewarm?)")
            gen = self._context_generation
            data = None
            page = None
            try:
                page = await self.context.new_page()
                async with page.expect_response(
                    lambda r: url in r.url,
                    timeout=30_000,
                ) as resp_info:
                    await page.goto(full_url, wait_until="commit", timeout=30_000)
                response = await resp_info.value
                try:
                    data = await response.json()
                except Exception:
                    pass  # Incapsula challenge page (HTML) — data stays None
                # A 4xx/5xx HTTP status is a real API error, not an Incapsula
                # challenge — don't rewarm for it (rewarm won't help).
                if data is None and response.status >= 400:
                    raise SessionError(f"GET {url} -> HTTP {response.status}")
            except Exception as e:
                print(f"[browser] page.goto error (attempt {attempt + 1}): {e}")
            finally:
                if page is not None:
                    try:
                        await asyncio.wait_for(page.close(), timeout=5.0)
                    except Exception:
                        pass  # page may belong to a context already closed by a concurrent rewarm
            if data is not None:
                # After first successful GET, persist the current context cookies to
                # Firestore relay so subsequent pipeline steps (e.g. merge_rankings.py)
                # can reuse this warm authenticated session without re-logging in.
                if not self._saved_relay and self.context is not None:
                    try:
                        current = await self.context.cookies()
                        _save_relay_cookies(current)
                        self._saved_relay = True
                    except Exception:
                        pass  # context may have been replaced by concurrent rewarm
                return data

            if attempt == 0:
                # Serialised rewarm: only the coroutine that first observed
                # a failure for this context generation does the work; all
                # others wait for it to finish, then retry on the new context.
                async with self._rewarm_lock:
                    if self._context_generation == gen:
                        print(f"[browser] No JSON captured from {url} -- invalidating and re-warming...")
                        await self._do_rewarm()
                    # else: already rewarmed by a concurrent coroutine; just retry
                continue

            raise SessionError(f"GET {url} -> no JSON captured after re-warm-up")

        raise SessionError(f"GET {url} -> exhausted retries")

    async def post(self, url: str, body: dict) -> dict:
        """
        POST via fetch() from a real Chromium page that has first navigated to
        the exact same API URL (as a no-op GET seed).

        The GET navigation to the API URL seeds the Incapsula incap_ses_* session
        cookie for that exact path, so the subsequent fetch() POST from that page
        is accepted by Incapsula on GCP IPs.
        """
        from urllib.parse import urlencode

        _FETCH_JS = """
            async ({url, body}) => {
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'accept': 'application/json, text/plain, */*',
                        'content-type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify(body),
                });
                const text = await resp.text();
                if (!resp.ok) throw new Error('HTTP ' + resp.status + ': ' + text.slice(0, 200));
                try { return JSON.parse(text); }
                catch (e) { throw new Error('non-JSON: ' + text.slice(0, 200)); }
            }
        """

        for attempt in range(2):
            # Wait out any concurrent rewarm rather than failing immediately.
            if self.context is None:
                async with self._rewarm_lock:
                    pass  # release immediately; just waits for in-progress rewarm
            if self.context is None:
                raise SessionError(f"POST {url} -> no browser context")
            gen = self._context_generation
            page = None
            try:
                page = await self.context.new_page()
                # Navigate to the exact API URL as GET first.
                # This seeds Incapsula incap_ses_* cookies for the endpoint path
                # even though the GET response itself may not be valid JSON.
                seed_url = f"{url}?{urlencode(body)}" if body else url
                try:
                    await page.goto(seed_url, wait_until="commit", timeout=20_000)
                except Exception:
                    pass  # seed navigation may fail (405/HTML) — that's OK
                # Now fire the POST from this page.  The browser has the right
                # incap_ses_* cookies for this endpoint and the Referer will
                # point at the same API path.
                result = await page.evaluate(_FETCH_JS, {"url": url, "body": body})
                return result
            except Exception as e:
                print(f"[browser] POST error (attempt {attempt + 1}): {e}")
            finally:
                if page is not None:
                    try:
                        await asyncio.wait_for(page.close(), timeout=5.0)
                    except Exception:
                        pass

            if attempt == 0:
                async with self._rewarm_lock:
                    if self._context_generation == gen:
                        email = os.environ.get("ITF_EMAIL", "")
                        if email:
                            print(f"[browser] POST {url} failed — invalidating and re-warming...")
                            await self._do_rewarm()
                        else:
                            raise SessionError(
                                f"POST {url} -> Incapsula challenge; "
                                "set ITF_EMAIL/ITF_PASSWORD in Cloud Run env vars to authenticate."
                            )
                continue

            raise SessionError(f"POST {url} -> failed after re-warm")