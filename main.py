"""
ITF Junior Pre-Ranking Pipeline
================================

Fetches the current week's tournament results for all WTT Juniors events,
assigns ITF points per player based on the round they reached, and writes a
JSON summary to output/points_earned_<week-start>.json.

Usage
-----
    python main.py                  # uses today's week, headful browser
    python main.py --headless       # headless Chromium (once stable)
    python main.py --week 2026-03-23  # process the week containing that date

First-time setup
----------------
    pip install -r requirements.txt
    playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import random
from pathlib import Path

from src.browser import BrowserSession
from src.api import (
    fetch_calendar,
    fetch_drawsheets_via_page,
    fetch_rankings,
)
from src.parser import parse_drawsheet, PlayerResult
from src.points import load_points_table


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def week_range(anchor: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return the Monday–Sunday dates of the week containing *anchor*."""
    monday = anchor - datetime.timedelta(days=anchor.weekday())
    return monday, monday + datetime.timedelta(days=6)


# Category → sort priority (lower = fetched first = highest points value)
_CATEGORY_PRIORITY: dict[str, int] = {
    "J500": 0,
    "J300": 1,
    "J200": 2,
    "J100": 3,
    "J60":  4,
    "J30":  5,
}


def _category_priority(tournament: dict) -> int:
    """Return sort key for a tournament dict — lower = higher priority."""
    cat = (tournament.get("category") or "").upper()
    # Exact match first; fall back to prefix match for e.g. "J500 Regional"
    if cat in _CATEGORY_PRIORITY:
        return _CATEGORY_PRIORITY[cat]
    for key, pri in _CATEGORY_PRIORITY.items():
        if cat.startswith(key):
            return pri
    return 99  # unknown category → deprioritised to end


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run(headless: bool, week_anchor: datetime.date) -> None:
    monday, sunday = week_range(week_anchor)
    date_from = monday.isoformat()
    date_to   = sunday.isoformat()
    print(f"[main] Target week: {date_from} – {date_to}")

    points_table = load_points_table()
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    async with BrowserSession(headless=headless) as session:

        # ── 1. Rankings (Boys + Girls in parallel) ────────────────────────────
        print("[main] Fetching rankings…")
        boys, girls = await asyncio.gather(
            fetch_rankings(session, "B"),
            fetch_rankings(session, "G"),
        )
        rankings_by_id: dict[int, dict] = {
            p["playerId"]: p for p in (*boys, *girls)
        }
        print(f"[main] Rankings: {len(boys)} boys  /  {len(girls)} girls")

        # ── 2. Calendar ───────────────────────────────────────────────────────
        print("[main] Fetching calendar…")
        tournaments = await fetch_calendar(session, date_from, date_to)
        print(f"[main] Tournaments this week: {len(tournaments)}")

        # Sort highest-value categories first so J500 → J300 → … → J30.
        # If the session degrades mid-run the most important tournaments will
        # already be in the cache when problems begin.
        tournaments = sorted(tournaments, key=_category_priority)
        for t in tournaments:
            print(f"       {t['category']:5s}  {t['name']}")

        # ── 3. Fetch draw pages in batches of 2 tournaments ───────────────
        # Incapsula pattern: first few requests are fine, then the session
        # gets flagged when we hammer it continuously.  Strategy:
        #   • Fetch 2 full tournaments (all 4 events each) back-to-back.
        #   • Close the browser context, wait 60–120 s (true cooldown).
        #   • Re-warm a brand-new context with fresh fingerprint + cookies.
        #   • Repeat until all tournaments are done.
        # Within each tournament the 4 event fetches are also spaced out
        # (see fetch_drawsheets_via_page in api.py).

        _STANDARD_EVENTS = [("B", "S"), ("G", "S"), ("B", "D"), ("G", "D")]
        _BATCH_SIZE = 2           # tournaments per session window
        _COOLDOWN_MIN = 60.0      # seconds to wait between batches
        _COOLDOWN_MAX = 120.0

        draw_page_meta: list = []    # (tournament_dict, [(pt, mt)])
        draw_page_results: list = [] # parallel with draw_page_meta

        linkable = [t for t in tournaments if t.get("tournamentLink")]
        total = len(linkable)
        print(f"[main] Fetching {total} tournament draw pages "
              f"(batches of {_BATCH_SIZE}, cooldown {_COOLDOWN_MIN:.0f}–{_COOLDOWN_MAX:.0f}s)…")

        for batch_start in range(0, total, _BATCH_SIZE):
            batch = linkable[batch_start:batch_start + _BATCH_SIZE]

            # ── Cooldown + session restart between batches ─────────────────
            if batch_start > 0:
                cooldown = random.uniform(_COOLDOWN_MIN, _COOLDOWN_MAX)
                print(
                    f"[main] ── Batch {batch_start // _BATCH_SIZE + 1} of "
                    f"{(total + _BATCH_SIZE - 1) // _BATCH_SIZE} ──"
                )
                print(
                    f"[main] Cooldown {cooldown:.0f}s — closing context, "
                    f"clearing cookies, waiting…"
                )
                # Close the current context and wipe Firestore cookies so the
                # next warm-up starts with a completely fresh fingerprint.
                if session.context is not None:
                    try:
                        await asyncio.wait_for(session.context.close(), timeout=10.0)
                    except Exception:
                        pass
                    session.context = None
                from src.browser import clear_session_cache
                clear_session_cache()
                await asyncio.sleep(cooldown)

                # Re-warm: perform a full login and seed new Incapsula cookies.
                print("[main] Re-warming session for next batch…")
                await session._warm_up()

            for idx, tournament in enumerate(batch):
                global_idx = batch_start + idx + 1

                # Small jitter between tournaments within the same batch.
                if idx > 0:
                    jitter = random.uniform(5.0, 12.0)
                    print(f"[main] Intra-batch pause {jitter:.1f}s…")
                    await asyncio.sleep(jitter)

                print(
                    f"[main] [{global_idx}/{total}] "
                    f"{tournament.get('category', '?'):5s}  "
                    f"{tournament.get('name', '?')}"
                )
                try:
                    result = await fetch_drawsheets_via_page(
                        session, tournament["tournamentLink"]
                    )
                except Exception as exc:
                    result = exc

                draw_page_meta.append((tournament, _STANDARD_EVENTS))
                draw_page_results.append(result)

        # ── 5. Collect resolved draws from fresh captures ───────────────────
        resolved_draws: dict[tuple[str, str, str], dict] = {}

        for (tournament, events), fresh_draws in zip(
            draw_page_meta, draw_page_results
        ):
            tkey = tournament["tournamentKey"]

            if isinstance(fresh_draws, Exception):
                print(f"[warn] Drawsheet error {tkey}: {fresh_draws}")
                continue

            for pt_code, mt_code in events:
                draw = fresh_draws.get((pt_code, mt_code))
                if draw is not None:
                    resolved_draws[(tkey, pt_code, mt_code)] = draw
                else:
                    print(f"[warn] No drawsheet captured for {tkey} {pt_code}{mt_code}")

        # ── 6. Parse results, map to points ──────────────────────────────────
        # Pre-populate ALL calendar tournaments so they appear in the output
        # even when their drawsheets haven't been published yet or returned
        # no results (the UI renders them as "pending" cards).
        tournament_output: dict[str, dict] = {}
        for tournament in tournaments:
            tkey = tournament["tournamentKey"]
            tournament_output[tkey] = {
                "tournament_key": tkey,
                "name":           tournament["name"],
                "category":       tournament["category"],
                "surface":        tournament.get("surfaceDesc"),
                "location":       tournament.get("location"),
                "host_nation":    tournament.get("hostNation"),
                "results":        [],
            }

        # Build a lookup from tournament key → category for the resolved loop
        category_by_key: dict[str, str] = {
            t["tournamentKey"]: t["category"] for t in tournaments
        }

        for (tkey, pt_code, mt_code), drawsheet in resolved_draws.items():
            category = category_by_key.get(tkey)
            if category is None:
                continue

            player_results: list[PlayerResult] = parse_drawsheet(
                drawsheet, category, pt_code, mt_code, points_table
            )

            for pr in player_results:
                ranked = rankings_by_id.get(pr.player_id, {})
                tournament_output[tkey]["results"].append({
                    "player_id":      pr.player_id,
                    "name":           f"{pr.given_name} {pr.family_name}".strip(),
                    "nationality":    pr.nationality,
                    "event":          pr.event,
                    "round_reached":  pr.round_reached,
                    "points":         pr.points,
                    "draw_position":  pr.draw_position,
                    "current_rank":   ranked.get("rank"),
                    "current_points": ranked.get("points"),
                })

        # ── 7. Write output ───────────────────────────────────────────────────
        output = {
            "week_start":    date_from,
            "week_end":      date_to,
            "generated_at":  datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "tournaments":   list(tournament_output.values()),
        }
        out_path = output_dir / f"points_earned_{date_from}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        latest_path = output_dir / "latest_points_earned.json"
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        total_player_results = sum(
            len(t["results"]) for t in tournament_output.values()
        )
        print(
            f"[main] Done. {len(tournament_output)} tournaments, "
            f"{total_player_results} player-results → {out_path}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ITF junior pre-ranking pipeline")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium in headless mode (default: headful for debugging)",
    )
    parser.add_argument(
        "--week",
        default=None,
        metavar="YYYY-MM-DD",
        help="Any date in the target week. Defaults to today.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Clear all Firestore session cookie caches before starting. "
            "Use this for local runs to prevent burned GCP session cookies "
            "from poisoning the local browser warm-up."
        ),
    )
    args = parser.parse_args()

    if args.fresh:
        from src.browser import clear_session_cache
        clear_session_cache()

    anchor = (
        datetime.date.fromisoformat(args.week)
        if args.week
        else datetime.date.today()
    )

    asyncio.run(run(args.headless, anchor))


if __name__ == "__main__":
    main()
