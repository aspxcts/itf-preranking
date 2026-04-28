"""
ITF Expiry Sweep
================

Runs once per week (Monday) to fetch ranking breakdowns for every player
whose results are expiring this week (i.e. players who competed in tournaments
that started during the same ISO week exactly 52 weeks ago).

The resulting breakdowns are merged into output/latest_player_breakdowns.json
so that calculate_rankings.py has accurate data for all affected players —
not just those who played this week.

Why this is necessary
---------------------
calculate_rankings.py only fetches breakdowns for players active THIS week.
But if a player has a result expiring (falling off the 52-week window) and
did NOT play this week, their estimated rank will be wrong unless we have
their current breakdown.

Usage
-----
    python expiry_sweep.py                   # uses today's week, headful browser
    python expiry_sweep.py --headless
    python expiry_sweep.py --week 2026-03-30 # any date in the target week
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import random
from pathlib import Path

from src.browser import BrowserSession, clear_session_cache
from src.api import (
    fetch_calendar,
    fetch_event_filters,
    fetch_drawsheet,
    fetch_ranking_points,
)
from src.parser import parse_drawsheet
from src.points import load_points_table


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _week_monday(anchor: datetime.date) -> datetime.date:
    return anchor - datetime.timedelta(days=anchor.weekday())


def _year_ago_window(week_monday: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return Monday–Sunday of the same ISO week 52 weeks ago."""
    ya_monday = week_monday - datetime.timedelta(weeks=52)
    return ya_monday, ya_monday + datetime.timedelta(days=6)


async def _limited(sem: asyncio.Semaphore, coro):
    async with sem:
        return await coro


def _load_breakdowns() -> dict:
    """Load the current latest_player_breakdowns.json, or return empty shell."""
    path = Path("output") / "latest_player_breakdowns.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"week_start": None, "generated_at": None, "players": {}}


def _save_breakdowns(data: dict) -> None:
    path = Path("output") / "latest_player_breakdowns.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[sweep] Breakdowns written -> {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run(headless: bool, week_anchor: datetime.date) -> None:
    monday = _week_monday(week_anchor)
    ya_monday, ya_sunday = _year_ago_window(monday)
    date_from = ya_monday.isoformat()
    date_to   = ya_sunday.isoformat()

    print(f"[sweep] Current week Monday : {monday.isoformat()}")
    print(f"[sweep] Expiry window       : {date_from} – {date_to}  (52 weeks ago)")

    points_table = load_points_table()
    sem = asyncio.Semaphore(3)  # calendar / event-filter / drawsheet fetches

    async with BrowserSession(headless=headless) as session:

        # ── 1. Calendar for the expiry week ───────────────────────────────────
        print("[sweep] Fetching year-ago calendar…")
        tournaments = await fetch_calendar(session, date_from, date_to)
        if not tournaments:
            print("[sweep] No tournaments found in the expiry window — nothing to do.")
            return
        print(f"[sweep] Found {len(tournaments)} expiring tournament(s):")
        for t in tournaments:
            print(f"         {t['category']:5s}  {t['name']}")

        # ── 2. Event filters (parallel) ───────────────────────────────────────
        print("[sweep] Fetching event filters…")
        ef_results = await asyncio.gather(
            *[_limited(sem, fetch_event_filters(session, t["tournamentKey"]))
              for t in tournaments],
            return_exceptions=True,
        )

        # ── 3. Build drawsheet tasks ──────────────────────────────────────────
        drawsheet_coros = []
        drawsheet_meta: list[tuple[dict, str, str]] = []  # (tournament, pt, mt)

        for tournament, ef in zip(tournaments, ef_results):
            tkey = tournament["tournamentKey"]
            if isinstance(ef, Exception):
                print(f"[warn] Event filters failed for {tkey}: {ef}")
                continue
            t_id      = ef["tournamentId"]
            tour_type = ef["tourType"]
            for pt_code, mt_code, ec_code, ds_code in ef["events"]:
                if ec_code != "M" or ds_code != "KO":
                    continue
                drawsheet_coros.append(
                    _limited(
                        sem,
                        fetch_drawsheet(session, t_id, tour_type,
                                        pt_code, mt_code, ec_code, ds_code),
                    )
                )
                drawsheet_meta.append((tournament, pt_code, mt_code))

        # ── 4. Fetch all drawsheets ───────────────────────────────────────────
        print(f"[sweep] Fetching {len(drawsheet_coros)} drawsheet(s)…")
        drawsheet_results = await asyncio.gather(
            *drawsheet_coros, return_exceptions=True
        )

        # ── 5. Collect unique player IDs from expiring drawsheets ─────────────
        expiring_player_ids: set[int] = set()

        for (tournament, pt_code, mt_code), drawsheet in zip(
            drawsheet_meta, drawsheet_results
        ):
            if isinstance(drawsheet, Exception):
                print(f"[warn] Drawsheet error {tournament['tournamentKey']} "
                      f"{pt_code}{mt_code}: {drawsheet}")
                continue

            category = tournament["category"]
            player_results = parse_drawsheet(
                drawsheet, category, pt_code, mt_code, points_table
            )
            for pr in player_results:
                if pr.player_id:
                    expiring_player_ids.add(pr.player_id)

        print(f"[sweep] Players with expiring results: {len(expiring_player_ids)}")
        if not expiring_player_ids:
            print("[sweep] No player IDs found — historical drawsheets may be unavailable.")
            return

        # ── 6. Filter out players whose breakdowns are already fresh ──────────
        existing = _load_breakdowns()
        already_have = set(int(k) for k in existing.get("players", {}).keys())
        to_fetch = expiring_player_ids - already_have

        print(f"[sweep] Already in latest_player_breakdowns.json : {len(already_have & expiring_player_ids)}")
        print(f"[sweep] Need to fetch                            : {len(to_fetch)}")

        if not to_fetch:
            print("[sweep] All expiring players already covered — nothing to fetch.")
            return

        # ── 7. Fetch ranking breakdowns for missing players (batched) ───────────
        # Mirrors the full_breakdown path in calculate_rankings.py:
        #   - Batches of 15 players, Semaphore(2) = 2 concurrent tabs
        #   - 0.5-2 s per-slot jitter to avoid burst patterns
        #   - 15-35 s cooldown + context restart + re-warm between batches
        _BATCH_SIZE  = 15
        _COOLDOWN_MIN = 15.0
        _COOLDOWN_MAX = 35.0
        sem_bp = asyncio.Semaphore(2)

        async def _fetch_one(pid: int):
            async with sem_bp:
                await asyncio.sleep(random.uniform(0.5, 2.0))
                return await fetch_ranking_points(session, pid)

        to_fetch_list = sorted(to_fetch)
        breakdown_results: list = []
        total_bp = len(to_fetch_list)
        print(
            f"[sweep] Fetching {total_bp} ranking breakdown(s) "
            f"in batches of {_BATCH_SIZE}, "
            f"cooldown {_COOLDOWN_MIN:.0f}-{_COOLDOWN_MAX:.0f}s between batches..."
        )

        for batch_start in range(0, total_bp, _BATCH_SIZE):
            batch = to_fetch_list[batch_start:batch_start + _BATCH_SIZE]

            if batch_start > 0:
                cooldown = random.uniform(_COOLDOWN_MIN, _COOLDOWN_MAX)
                print(
                    f"[sweep] Batch {batch_start // _BATCH_SIZE + 1} of "
                    f"{(total_bp + _BATCH_SIZE - 1) // _BATCH_SIZE} -- "
                    f"cooldown {cooldown:.0f}s, restarting context..."
                )
                if session.context is not None:
                    try:
                        await asyncio.wait_for(session.context.close(), timeout=10.0)
                    except Exception:
                        pass
                    session.context = None
                clear_session_cache()
                await asyncio.sleep(cooldown)
                print("[sweep] Re-warming session for next batch...")
                await session._warm_up()

            batch_results = await asyncio.gather(
                *[_fetch_one(pid) for pid in batch],
                return_exceptions=True,
            )
            breakdown_results.extend(batch_results)

    # ── 8. Merge into latest_player_breakdowns.json ───────────────────────────
    merged = existing
    added = 0
    errors = 0

    for pid, rdata in zip(to_fetch_list, breakdown_results):
        if isinstance(rdata, Exception):
            print(f"[warn] GetRankingPoints failed for player {pid}: {rdata}")
            errors += 1
            continue

        # We only have the player ID here — no name/nationality from expiry
        # drawsheets without re-parsing; store minimal entry that the
        # calculator can use (it only needs the countable/non-countable lists).
        merged["players"][str(pid)] = {
            "name":              "",
            "nationality":       "",
            "gender":            "",
            "current_rank":      None,
            "current_points":    0.0,
            # No current-week activity — this player didn't play this week
            "current_week_singles": [],
            "current_week_doubles": [],
            "singles_countable":      rdata.get("singles_countable", []),
            "singles_non_countable":  rdata.get("singles_non_countable", []),
            "doubles_countable":      rdata.get("doubles_countable", []),
            "doubles_non_countable":  rdata.get("doubles_non_countable", []),
            "_source": "expiry_sweep",
        }
        added += 1

    merged["generated_at"] = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    _save_breakdowns(merged)
    print(f"[sweep] Done. Added {added} expiry-player breakdowns ({errors} errors).")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch ranking breakdowns for players with expiring results."
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--week",
        default=None,
        metavar="YYYY-MM-DD",
        help="Any date in the target week (defaults to today).",
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
