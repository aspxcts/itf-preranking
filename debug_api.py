"""
debug_api.py — Local diagnostic script for ITF API connectivity.

Replicates exactly what the deployed Cloud Run service does on cold start:
  1. Launch Playwright Chromium (headless, with --no-sandbox to simulate container)
  2. Navigate to the ITF calendar warm-up page
  3. Call GetPlayerRankings via APIRequestContext (cookie-sharing HTTP client)
  4. Call GetPlayerRankings via page.evaluate(fetch()) (in-page JS fetch)
  5. Call GetCalendar for this week

Every step is timed and all network events are logged so you can see exactly
where time is being spent and what Incapsula is doing.

Run from the project root:
    python debug_api.py
    python debug_api.py --headless      # simulates container behaviour
"""
import argparse
import asyncio
import json
import time
from urllib.parse import urlencode

from playwright.async_api import async_playwright

_WARM_UP_URL = (
    "https://www.itftennis.com"
    "/en/tournament-calendar/world-tennis-tour-juniors-calendar/"
)
_RANKINGS_URL = (
    "https://www.itftennis.com/tennis/api/PlayerRankApi/GetPlayerRankings"
)
_CALENDAR_URL = (
    "https://www.itftennis.com/tennis/api/TournamentApi/GetCalendar"
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def ts():
    return f"{time.monotonic():.2f}s"


async def main(headless: bool, no_sandbox: bool):
    print(f"\n{'='*60}")
    print(f"ITF API Debug  |  headless={headless}  no_sandbox={no_sandbox}")
    print(f"{'='*60}\n")

    t0 = time.monotonic()

    async with async_playwright() as pw:
        # ── 1. Launch browser ─────────────────────────────────────────────────
        args = ["--no-sandbox", "--disable-setuid-sandbox"] if no_sandbox else []
        print(f"[{ts()}] Launching Chromium (headless={headless}, args={args})…")
        browser = await pw.chromium.launch(headless=headless, args=args)

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=_USER_AGENT,
        )
        print(f"[{ts()}] Browser context created.")

        # ── 2. Warm-up page — log all network events ──────────────────────────
        page = await context.new_page()

        def on_request(req):
            print(f"  [REQ ] {req.method} {req.url[:100]}")

        def on_response(resp):
            print(f"  [RESP] {resp.status} {resp.url[:100]}")

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"\n[{ts()}] >>> WARM-UP: navigating to ITF calendar page…")
        warmup_start = time.monotonic()
        try:
            await page.goto(_WARM_UP_URL, wait_until="domcontentloaded", timeout=60_000)
            print(f"[{ts()}] domcontentloaded fired  (+{time.monotonic()-warmup_start:.2f}s)")
        except Exception as e:
            print(f"[{ts()}] goto timed out / failed: {e}")

        cookies = await context.cookies()
        incap_cookies = [c for c in cookies if "incap" in c["name"].lower() or "visid" in c["name"].lower()]
        print(f"[{ts()}] Cookies after warm-up: {len(cookies)} total, {len(incap_cookies)} Incapsula")
        for c in incap_cookies:
            print(f"         {c['name']} = {c['value'][:40]}…")

        await page.close()
        print(f"[{ts()}] Warm-up done  (total: +{time.monotonic()-warmup_start:.2f}s)\n")

        # ── 3. APIRequestContext GET (what the original code used) ────────────
        print(f"[{ts()}] >>> TEST A: APIRequestContext.get() (cookie-sharing HTTP client)")
        req_ctx = context.request
        api_start = time.monotonic()
        params = {
            "circuitCode": "JT",
            "playerTypeCode": "B",
            "ageCategoryCode": "",
            "juniorRankingType": "itf",
            "take": 10,  # small take for speed
            "skip": 0,
            "isOrderAscending": "true",
        }
        full_url = f"{_RANKINGS_URL}?{urlencode(params)}"
        print(f"  URL: {full_url[:120]}")
        try:
            resp = await req_ctx.get(
                _RANKINGS_URL,
                headers={
                    "accept": "application/json, text/plain, */*",
                    "accept-language": "en-US,en;q=0.9",
                },
                params=params,
                timeout=60_000,
            )
            elapsed = time.monotonic() - api_start
            text = await resp.text()
            print(f"  Status: {resp.status}  Size: {len(text)} bytes  Time: {elapsed:.2f}s")
            if resp.ok and text.strip():
                data = json.loads(text)
                items = data.get("items", [])
                print(f"  Result: {len(items)} players returned")
                if items:
                    p = items[0]
                    print(f"  #1: {p.get('playerGivenName')} {p.get('playerFamilyName')} — {p.get('points')} pts")
            else:
                print(f"  Body[:300]: {text[:300]}")
        except Exception as e:
            print(f"  FAILED after {time.monotonic()-api_start:.2f}s: {e}")

        # ── 4. page.evaluate fetch() GET ──────────────────────────────────────
        print(f"\n[{ts()}] >>> TEST B: page.evaluate(fetch()) from within ITF page context")
        eval_page = await context.new_page()
        eval_page.on("request", on_request)
        eval_page.on("response", on_response)
        try:
            print(f"  Loading warm-up page into eval page…")
            eval_start = time.monotonic()
            await eval_page.goto(_WARM_UP_URL, wait_until="domcontentloaded", timeout=60_000)
            print(f"  Page loaded in {time.monotonic()-eval_start:.2f}s")
        except Exception as e:
            print(f"  Page load failed: {e}")

        eval_start = time.monotonic()
        try:
            result = await eval_page.evaluate(
                """
                async (url) => {
                    const resp = await fetch(url, {
                        method: 'GET',
                        headers: {
                            'accept': 'application/json, text/plain, */*',
                            'accept-language': 'en-US,en;q=0.9',
                        },
                        credentials: 'include',
                    });
                    return { status: resp.status, ok: resp.ok, body: await resp.text() };
                }
                """,
                full_url,
            )
            elapsed = time.monotonic() - eval_start
            print(f"  Status: {result['status']}  Size: {len(result['body'])} bytes  Time: {elapsed:.2f}s")
            if result['ok'] and result['body'].strip():
                data = json.loads(result['body'])
                items = data.get("items", [])
                print(f"  Result: {len(items)} players returned")
            else:
                print(f"  Body[:300]: {result['body'][:300]}")
        except Exception as e:
            print(f"  FAILED after {time.monotonic()-eval_start:.2f}s: {e}")

        await eval_page.close()

        # ── 5. Calendar call ──────────────────────────────────────────────────
        import datetime
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        cal_params = {
            "circuitCode": "JT",
            "searchString": "",
            "skip": 0,
            "take": 10,
            "dateFrom": monday.isoformat(),
            "dateTo": sunday.isoformat(),
            "isOrderAscending": "true",
            "orderField": "startDate",
        }
        print(f"\n[{ts()}] >>> TEST C: GetCalendar for {monday} to {sunday}")
        cal_start = time.monotonic()
        try:
            resp = await req_ctx.get(
                _CALENDAR_URL,
                headers={"accept": "application/json, text/plain, */*", "accept-language": "en-US,en;q=0.9"},
                params=cal_params,
                timeout=60_000,
            )
            elapsed = time.monotonic() - cal_start
            text = await resp.text()
            print(f"  Status: {resp.status}  Size: {len(text)} bytes  Time: {elapsed:.2f}s")
            if resp.ok and text.strip():
                data = json.loads(text)
                items = data.get("items", [])
                print(f"  Result: {len(items)} tournaments")
        except Exception as e:
            print(f"  FAILED after {time.monotonic()-cal_start:.2f}s: {e}")

        await browser.close()

    total = time.monotonic() - t0
    print(f"\n[DONE] Total time: {total:.2f}s\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="Run Chromium headless (simulates container)")
    parser.add_argument("--no-sandbox", dest="no_sandbox", action="store_true", help="Add --no-sandbox flag (simulates Cloud Run container)")
    args = parser.parse_args()
    asyncio.run(main(args.headless, args.no_sandbox))
