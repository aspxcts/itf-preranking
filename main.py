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
from pathlib import Path

from src.browser import BrowserSession
from src.api import (
    fetch_calendar,
    fetch_drawsheet,
    fetch_event_filters,
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


async def _limited(sem: asyncio.Semaphore, coro):
    """Acquire *sem* before awaiting *coro* to cap concurrent requests."""
    async with sem:
        return await coro


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

    # Semaphore: cap concurrent API requests to avoid rate-limiting
    sem = asyncio.Semaphore(5)

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
        for t in tournaments:
            print(f"       {t['category']:5s}  {t['name']}")

        # ── 3. Event filters for all tournaments (parallel, rate-limited) ─────
        print("[main] Fetching event filters…")
        ef_results = await asyncio.gather(
            *[
                _limited(sem, fetch_event_filters(session, t["tournamentKey"]))
                for t in tournaments
            ],
            return_exceptions=True,
        )

        # ── 4. Build drawsheet fetch tasks ───────────────────────────────────
        drawsheet_coros: list = []
        drawsheet_meta: list[tuple[dict, str, str]] = []  # (tournament, pt, mt)

        for tournament, ef in zip(tournaments, ef_results):
            tkey = tournament["tournamentKey"]
            if isinstance(ef, Exception):
                print(f"[warn] Event filters failed for {tkey}: {ef}")
                continue

            t_id      = ef["tournamentId"]
            tour_type = ef["tourType"]

            for pt_code, mt_code, ec_code, ds_code in ef["events"]:
                # Only process Main Draw Knockout events
                if ec_code != "M" or ds_code != "KO":
                    continue
                drawsheet_coros.append(
                    _limited(
                        sem,
                        fetch_drawsheet(
                            session, t_id, tour_type,
                            pt_code, mt_code, ec_code, ds_code,
                        ),
                    )
                )
                drawsheet_meta.append((tournament, pt_code, mt_code))

        # ── 5. Fetch all drawsheets (parallel, rate-limited) ────────────────
        print(f"[main] Fetching {len(drawsheet_coros)} drawsheets…")
        drawsheet_results = await asyncio.gather(
            *drawsheet_coros, return_exceptions=True
        )

        # ── 6. Parse results, map to points ──────────────────────────────────
        tournament_output: dict[str, dict] = {}

        for (tournament, pt_code, mt_code), drawsheet in zip(
            drawsheet_meta, drawsheet_results
        ):
            tkey     = tournament["tournamentKey"]
            category = tournament["category"]

            if isinstance(drawsheet, Exception):
                print(f"[warn] Drawsheet error {tkey} {pt_code}{mt_code}: {drawsheet}")
                continue

            player_results: list[PlayerResult] = parse_drawsheet(
                drawsheet, category, pt_code, mt_code, points_table
            )

            if tkey not in tournament_output:
                tournament_output[tkey] = {
                    "tournament_key": tkey,
                    "name":           tournament["name"],
                    "category":       category,
                    "surface":        tournament.get("surfaceDesc"),
                    "location":       tournament.get("location"),
                    "host_nation":    tournament.get("hostNation"),
                    "results":        [],
                }

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
    args = parser.parse_args()

    anchor = (
        datetime.date.fromisoformat(args.week)
        if args.week
        else datetime.date.today()
    )

    asyncio.run(run(args.headless, anchor))


if __name__ == "__main__":
    main()
