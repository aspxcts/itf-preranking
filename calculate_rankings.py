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
import random
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

async def run(headless: bool, week_monday: datetime.date, full_breakdown: bool = False) -> None:
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

    # ── Load or fetch breakdown data ─────────────────────────────────────────
    # breakdown_by_pid maps str(player_id) → dict with countable/non_countable
    # lists needed by simulate_player().
    # full_breakdown=True (Monday): fetch GetRankingPoints via browser for every
    #   player, save the week's cache, use accurate top-6 simulation.
    # full_breakdown=False (daily): load the cache written on Monday; players
    #   not found in cache get a simple additive estimate instead.
    breakdown_by_pid: dict[str, dict] = {}

    if full_breakdown:
        # ── Batched browser fetch with cooldown between batches ───────────────
        # Mirrors main.py's approach but scaled down for API GETs:
        #   • 30 players per context window  (vs 2 tournaments in main.py)
        #   • 15–35 s cooldown between batches (vs 60–120 s)
        #   • Semaphore(2) = 2 concurrent tabs (vs 5)
        #   • 0.5–2 s jitter inside each semaphore slot
        _BATCH_SIZE   = 15
        _COOLDOWN_MIN = 15.0
        _COOLDOWN_MAX = 35.0

        sem = asyncio.Semaphore(2)

        async def _fetch_one(sess, pid: int):
            """Acquire a slot, sleep briefly, then call the API."""
            async with sem:
                await asyncio.sleep(random.uniform(0.5, 2.0))
                return await fetch_ranking_points(sess, pid)

        total_players = len(unique_players)
        total_batches = (total_players + _BATCH_SIZE - 1) // _BATCH_SIZE
        print(
            f"[calc] Full breakdown mode — {total_players} players, "
            f"{total_batches} batch(es) of {_BATCH_SIZE}, "
            f"cooldown {_COOLDOWN_MIN:.0f}–{_COOLDOWN_MAX:.0f}s between batches…"
        )

        ranking_results: list = []

        async with BrowserSession(headless=headless) as session:
            for batch_start in range(0, total_players, _BATCH_SIZE):
                batch_pids = unique_players[batch_start:batch_start + _BATCH_SIZE]
                batch_num  = batch_start // _BATCH_SIZE + 1

                if batch_start > 0:
                    cooldown = random.uniform(_COOLDOWN_MIN, _COOLDOWN_MAX)
                    print(
                        f"[calc] ── Batch {batch_num}/{total_batches} ── "
                        f"cooldown {cooldown:.0f}s, restarting context…"
                    )
                    if session.context is not None:
                        try:
                            await asyncio.wait_for(session.context.close(), timeout=10.0)
                        except Exception:
                            pass
                        session.context = None
                    from src.browser import clear_session_cache
                    clear_session_cache()
                    await asyncio.sleep(cooldown)
                    await session._warm_up()
                else:
                    print(f"[calc] ── Batch 1/{total_batches} ──")

                batch_results = await asyncio.gather(
                    *[_fetch_one(session, pid) for pid in batch_pids],
                    return_exceptions=True,
                )
                ranking_results.extend(batch_results)
                ok  = sum(1 for r in batch_results if not isinstance(r, Exception))
                bad = len(batch_results) - ok
                print(
                    f"[calc]    {batch_start + len(batch_pids)}/{total_players} done "
                    f"({ok} ok, {bad} failed)"
                )

        # ── Retry failed players once (single pass, no cooldown) ─────────────
        failed_pids   = [pid for pid, r in zip(unique_players, ranking_results) if isinstance(r, Exception)]
        if failed_pids:
            print(f"[calc] Retrying {len(failed_pids)} failed player(s)…")
            async with BrowserSession(headless=headless) as retry_sess:
                retry_results = await asyncio.gather(
                    *[_fetch_one(retry_sess, pid) for pid in failed_pids],
                    return_exceptions=True,
                )
            retry_map = dict(zip(failed_pids, retry_results))
            ranking_results = [
                retry_map.get(pid, r) if isinstance(r, Exception) else r
                for pid, r in zip(unique_players, ranking_results)
            ]
            still_bad = sum(1 for r in ranking_results if isinstance(r, Exception))
            if still_bad:
                print(f"[calc] {still_bad} player(s) still failed after retry — will skip them.")

        # ── Build and save weekly breakdown cache ─────────────────────────────
        player_breakdowns_saved: dict[str, dict] = {}
        for pid, rdata in zip(unique_players, ranking_results):
            if isinstance(rdata, Exception):
                print(f"[warn] Skipping player {pid}: {rdata}")
                continue
            pinfo = player_new_pts[pid]
            entry: dict = {
                "name":              pinfo["name"],
                "nationality":       pinfo["nationality"],
                "gender":            pinfo["gender"],
                "current_rank":      pinfo.get("current_rank"),
                "current_points":    float(pinfo.get("current_points") or 0),
                "current_week_singles": pinfo["singles"],
                "current_week_doubles": pinfo["doubles"],
                "singles_countable":      rdata.get("singles_countable", []),
                "singles_non_countable":  rdata.get("singles_non_countable", []),
                "doubles_countable":      rdata.get("doubles_countable", []),
                "doubles_non_countable":  rdata.get("doubles_non_countable", []),
            }
            breakdown_by_pid[str(pid)] = entry
            player_breakdowns_saved[str(pid)] = entry

        merged_breakdowns = {**expiry_breakdowns, **player_breakdowns_saved}
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
        print(
            f"[calc] Breakdown cache written -> {bd_path} "
            f"({len(merged_breakdowns)} players total)"
        )

    else:
        # ── Daily fast path: no browser, load Monday's cached breakdowns ──────
        print("[calc] Fast estimation mode — no API calls.")
        if _bd_existing.exists():
            with open(_bd_existing, encoding="utf-8") as _f:
                _bd_cache = json.load(_f)
            breakdown_by_pid = _bd_cache.get("players", {})
            print(f"[calc] Loaded {len(breakdown_by_pid)} cached player breakdowns.")
        else:
            print(
                "[calc] No cached breakdowns found — additive estimate for all players. "
                "Run once with --full-breakdown to seed the cache."
            )

    # ── Simulate / estimate each player ──────────────────────────────────────
    output_players: list[dict] = []
    simulated_full = 0   # accurate top-6 simulation from cached/fresh breakdown
    simulated_fast = 0   # simple additive estimate (no breakdown in cache)

    for pid in unique_players:
        pinfo = player_new_pts[pid]
        current_pts = float(pinfo.get("current_points") or 0)
        singles_new = pinfo["singles"]
        doubles_new = pinfo["doubles"]

        bd = breakdown_by_pid.get(str(pid))
        if bd:
            sim = simulate_player(
                ranking_data={
                    "singles_countable":     bd.get("singles_countable", []),
                    "singles_non_countable": bd.get("singles_non_countable", []),
                    "doubles_countable":     bd.get("doubles_countable", []),
                    "doubles_non_countable": bd.get("doubles_non_countable", []),
                },
                new_singles_pts=singles_new,
                new_doubles_pts=doubles_new,
                week_monday=week_monday,
                current_combined=current_pts,
            )
            estimated_pts  = round(sim.new_combined, 4)
            delta          = round(sim.delta_combined, 4)
            breakdown_dict = {
                "singles_old":   round(sim.singles.old_total, 4),
                "singles_new":   round(sim.singles.new_total, 4),
                "singles_delta": round(sim.singles.delta, 4),
                "doubles_old":   round(sim.doubles.old_total, 4),
                "doubles_new":   round(sim.doubles.new_total, 4),
                "doubles_delta": round(sim.doubles.delta, 4),
                "new_singles_pts_this_week": singles_new,
                "new_doubles_pts_this_week": doubles_new,
            }
            simulated_full += 1
        else:
            # No 52-week breakdown — simple additive estimate.
            # current_points already reflects the top-6 rule from the live ITF
            # rankings, so adding new points directly overestimates for players
            # who already have 6 full countable results, but is close enough
            # for a daily pre-ranking estimate.
            delta_s        = sum(singles_new)
            delta_d        = sum(doubles_new) * 0.25
            delta          = round(delta_s + delta_d, 4)
            estimated_pts  = round(current_pts + delta, 4)
            breakdown_dict = {
                "singles_delta": round(delta_s, 4),
                "doubles_delta": round(delta_d * 4, 4),
                "new_singles_pts_this_week": singles_new,
                "new_doubles_pts_this_week": doubles_new,
                "_estimated": True,
            }
            simulated_fast += 1

        output_players.append({
            "player_id":        pid,
            "name":             pinfo["name"],
            "nationality":      pinfo["nationality"],
            "gender":           pinfo["gender"],
            "current_rank":     pinfo.get("current_rank"),
            "current_points":   current_pts,
            "estimated_points": estimated_pts,
            "delta":            delta,
            "breakdown":        breakdown_dict,
        })

    print(
        f"[calc] Simulations: {simulated_full} accurate (breakdown), "
        f"{simulated_fast} additive estimate"
    )

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
        "week_start":         week_start,
        "generated_at":       datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "players_calculated": len(output_players),
        "full_breakdown_used": full_breakdown,
        "players":       output_players,
    }
    out_path = Path("output") / f"estimated_rankings_{week_start}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(
        f"[calc] Done. {len(output_players)} players written "
        f"({'full breakdown' if full_breakdown else 'fast estimate'}) -> {out_path}"
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
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Clear Firestore session cookie cache before starting.",
    )
    parser.add_argument(
        "--full-breakdown",
        action="store_true",
        help=(
            "Fetch GetRankingPoints for every player (slow — hundreds of API calls). "
            "Run once on Monday to refresh the weekly breakdown cache. "
            "Subsequent daily runs load the cache and need no browser at all."
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
    monday = _week_monday(anchor)
    asyncio.run(run(args.headless, monday, full_breakdown=args.full_breakdown))


if __name__ == "__main__":
    main()
