"""
ITF Junior Estimated Rankings Calculator
=========================================

Loads a ``points_earned`` JSON produced by main.py, fetches each player's
current ranking breakdown from the ITF API, simulates the top-6 countable
rule, and writes estimated new rankings to output/estimated_rankings_<date>.json.

Usage
-----
    python calculate_rankings.py                        # uses latest points_earned file
    python calculate_rankings.py --week 2026-03-30      # specific week
    python calculate_rankings.py --headless --week ...

Output
------
    output/estimated_rankings_<week_start>.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from pathlib import Path

from src.browser import BrowserSession
from src.api import fetch_ranking_points
from src.calculator import simulate_player


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _week_monday(anchor: datetime.date) -> datetime.date:
    return anchor - datetime.timedelta(days=anchor.weekday())


def _find_points_file(week_start: str) -> Path:
    """Locate the points_earned JSON for a given week-start date string."""
    path = Path("output") / f"points_earned_{week_start}.json"
    if path.exists():
        return path
    # Fall back to the most recent file
    candidates = sorted(Path("output").glob("points_earned_*.json"), reverse=True)
    if candidates:
        print(f"[calc] No file for {week_start}, using {candidates[0].name}")
        return candidates[0]
    raise FileNotFoundError(
        "No points_earned_*.json found in output/. Run main.py first."
    )


async def _limited(sem: asyncio.Semaphore, coro):
    async with sem:
        return await coro


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run(headless: bool, week_monday: datetime.date) -> None:
    week_start = week_monday.isoformat()
    points_path = _find_points_file(week_start)

    print(f"[calc] Loading points from {points_path}")
    with open(points_path, encoding="utf-8") as f:
        points_data = json.load(f)

    # ── Aggregate this week's new points per player per discipline ────────────
    # Structure: { player_id: {"singles": [pts, ...], "doubles": [pts, ...],
    #                          "name": str, "nationality": str, "gender": ?} }
    player_new_pts: dict[int, dict] = {}

    for tournament in points_data.get("tournaments", []):
        for result in tournament.get("results", []):
            pid    = result["player_id"]
            event  = result["event"]   # "BS", "GS", "BD", "GD"
            pts    = float(result["points"])
            mt     = event[1]          # "S" or "D"

            if pid not in player_new_pts:
                player_new_pts[pid] = {
                    "singles": [],
                    "doubles": [],
                    "name":        result.get("name", ""),
                    "nationality": result.get("nationality", ""),
                    "gender":      event[0],   # "B" or "G"
                    "current_rank":   result.get("current_rank"),
                    "current_points": result.get("current_points"),
                }

            if mt == "S":
                player_new_pts[pid]["singles"].append(pts)
            else:
                player_new_pts[pid]["doubles"].append(pts)

    unique_players = list(player_new_pts.keys())
    print(f"[calc] Unique players who played this week: {len(unique_players)}")

    # ── Load expiry-sweep breakdowns for players NOT active this week ─────────
    # expiry_sweep.py runs on Monday and pre-fetches breakdowns for every player
    # whose results are dropping off the 52-week window this week.
    expiry_breakdowns: dict[str, dict] = {}
    _bd_existing = Path("output") / "latest_player_breakdowns.json"
    if _bd_existing.exists():
        with open(_bd_existing, encoding="utf-8") as _f:
            _existing_data = json.load(_f)
        for _pid_str, _bd in _existing_data.get("players", {}).items():
            if (_bd.get("_source") == "expiry_sweep"
                    and int(_pid_str) not in player_new_pts):
                expiry_breakdowns[_pid_str] = _bd
    if expiry_breakdowns:
        print(f"[calc] Expiry-only players loaded from sweep: {len(expiry_breakdowns)}")

    # ── Fetch ranking breakdowns for all players (parallel, rate-limited) ─────
    sem = asyncio.Semaphore(5)

    async with BrowserSession(headless=headless) as session:
        print("[calc] Fetching ranking breakdowns…")
        ranking_results = await asyncio.gather(
            *[
                _limited(sem, fetch_ranking_points(session, pid))
                for pid in unique_players
            ],
            return_exceptions=True,
        )

        # Retry players that failed on the first pass (rate-limit / transient)
        failed_pids = [
            pid for pid, r in zip(unique_players, ranking_results)
            if isinstance(r, Exception)
        ]
        if failed_pids:
            print(f"[calc] Retrying {len(failed_pids)} failed breakdown(s)…")
            await asyncio.sleep(3)
            retry_results = await asyncio.gather(
                *[
                    _limited(sem, fetch_ranking_points(session, pid))
                    for pid in failed_pids
                ],
                return_exceptions=True,
            )
            # Splice retried results back into ranking_results
            retry_map = dict(zip(failed_pids, retry_results))
            ranking_results = [
                retry_map.get(pid, r)
                for pid, r in zip(unique_players, ranking_results)
            ]
            remaining = sum(1 for r in ranking_results if isinstance(r, Exception))
            if remaining:
                print(f"[calc] {remaining} player(s) still failed after retry — will use estimate.")


    # ── Save raw ranking breakdowns (used by the What-if frontend) ─────────────
    player_breakdowns: dict[str, dict] = {}
    for pid, rdata in zip(unique_players, ranking_results):
        if isinstance(rdata, Exception):
            continue
        pinfo = player_new_pts[pid]
        player_breakdowns[str(pid)] = {
            "name":              pinfo["name"],
            "nationality":       pinfo["nationality"],
            "gender":            pinfo["gender"],
            "current_rank":      pinfo.get("current_rank"),
            "current_points":    float(pinfo.get("current_points") or 0),
            # Points earned this week (from this pipeline run)
            "current_week_singles": pinfo["singles"],
            "current_week_doubles": pinfo["doubles"],
            # Raw ITF API entries for top-6 simulation
            "singles_countable":      rdata.get("singles_countable", []),
            "singles_non_countable":  rdata.get("singles_non_countable", []),
            "doubles_countable":      rdata.get("doubles_countable", []),
            "doubles_non_countable":  rdata.get("doubles_non_countable", []),
        }

    # Merge: expiry entries first (background), then fresh weekly data on top
    merged_breakdowns = {**expiry_breakdowns, **player_breakdowns}

    bd_output = {
        "week_start":   week_start,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "players":      merged_breakdowns,
    }
    bd_path = Path("output") / f"player_breakdowns_{week_start}.json"
    with open(bd_path, "w", encoding="utf-8") as f:
        json.dump(bd_output, f, indent=2, ensure_ascii=False)
    bd_latest = Path("output") / "latest_player_breakdowns.json"
    with open(bd_latest, "w", encoding="utf-8") as f:
        json.dump(bd_output, f, indent=2, ensure_ascii=False)
    print(f"[calc] Breakdown data written -> {bd_path} ({len(merged_breakdowns)} players total)")

    # ── Simulate each player ──────────────────────────────────────────────────
    output_players: list[dict] = []
    errors = 0

    for pid, rdata in zip(unique_players, ranking_results):
        pinfo = player_new_pts[pid]

        if isinstance(rdata, Exception):
            print(f"[warn] GetRankingPoints failed for player {pid}: {rdata}")
            errors += 1
            continue

        sim = simulate_player(
            ranking_data=rdata,
            new_singles_pts=pinfo["singles"],
            new_doubles_pts=pinfo["doubles"],
            week_monday=week_monday,
            current_combined=pinfo.get("current_points"),
        )

        output_players.append({
            "player_id":        pid,
            "name":             pinfo["name"],
            "nationality":      pinfo["nationality"],
            "gender":           pinfo["gender"],
            "current_rank":     pinfo.get("current_rank"),
            "current_points":   round(sim.old_combined, 4),
            "estimated_points": round(sim.new_combined, 4),
            "delta":            round(sim.delta_combined, 4),
            "breakdown": {
                "singles_old":   round(sim.singles.old_total, 4),
                "singles_new":   round(sim.singles.new_total, 4),
                "singles_delta": round(sim.singles.delta, 4),
                "doubles_old":   round(sim.doubles.old_total, 4),
                "doubles_new":   round(sim.doubles.new_total, 4),
                "doubles_delta": round(sim.doubles.delta, 4),
                "new_singles_pts_this_week": pinfo["singles"],
                "new_doubles_pts_this_week": pinfo["doubles"],
            },
        })

    # ── Simulate expiry-only players (those who lost results but didn't play) ──
    expiry_simulated = 0
    for pid_str, bd in expiry_breakdowns.items():
        rdata = {
            "singles_countable":     bd.get("singles_countable", []),
            "singles_non_countable": bd.get("singles_non_countable", []),
            "doubles_countable":     bd.get("doubles_countable", []),
            "doubles_non_countable": bd.get("doubles_non_countable", []),
        }
        sim = simulate_player(
            ranking_data=rdata,
            new_singles_pts=[],
            new_doubles_pts=[],
            week_monday=week_monday,
            current_combined=bd.get("current_points") or None,
        )
        # Only include if there's a meaningful change (expiry actually dropped something)
        if abs(sim.delta_combined) < 0.001:
            continue
        output_players.append({
            "player_id":        int(pid_str),
            "name":             bd.get("name", ""),
            "nationality":      bd.get("nationality", ""),
            "gender":           bd.get("gender", ""),
            "current_rank":     bd.get("current_rank"),
            "current_points":   round(sim.old_combined, 4),
            "estimated_points": round(sim.new_combined, 4),
            "delta":            round(sim.delta_combined, 4),
            "breakdown": {
                "singles_old":   round(sim.singles.old_total, 4),
                "singles_new":   round(sim.singles.new_total, 4),
                "singles_delta": round(sim.singles.delta, 4),
                "doubles_old":   round(sim.doubles.old_total, 4),
                "doubles_new":   round(sim.doubles.new_total, 4),
                "doubles_delta": round(sim.doubles.delta, 4),
                "new_singles_pts_this_week": [],
                "new_doubles_pts_this_week": [],
            },
        })
        expiry_simulated += 1

    if expiry_simulated:
        print(f"[calc] Expiry-only players simulated: {expiry_simulated}")

    # Sort by estimated points descending
    output_players.sort(key=lambda p: p["estimated_points"], reverse=True)

    # ── Write output ──────────────────────────────────────────────────────────
    output = {
        "week_start":    week_start,
        "generated_at":  datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "players_calculated": len(output_players),
        "errors":        errors,
        "players":       output_players,
    }
    out_path = Path("output") / f"estimated_rankings_{week_start}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"[calc] Done. {len(output_players)} players calculated "
        f"({errors} errors) -> {out_path}"
    )
    if output_players:
        print("\nTop 10 estimated movers this week:")
        print(f"  {'Name':<30} {'Cur Pts':>9}  {'Est Pts':>9}  {'Delta':>8}")
        print("  " + "-" * 62)
        # Show top 10 by estimated points
        for p in output_players[:10]:
            delta_str = f"{p['delta']:+.2f}"
            print(
                f"  {p['name']:<30} {p['current_points']:>9.2f}  "
                f"{p['estimated_points']:>9.2f}  {delta_str:>8}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ITF junior estimated rankings calculator")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--week",
        default=None,
        metavar="YYYY-MM-DD",
        help="Any date in the target week (defaults to today)",
    )
    args = parser.parse_args()

    anchor = (
        datetime.date.fromisoformat(args.week)
        if args.week
        else datetime.date.today()
    )
    monday = _week_monday(anchor)
    asyncio.run(run(args.headless, monday))


if __name__ == "__main__":
    main()
