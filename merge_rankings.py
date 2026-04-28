"""
Merge Estimated Rankings
=========================

Fetches the full current ITF junior rankings (top-1000 Boys + Girls),
overlays this week's estimated point changes from calculate_rankings.py,
re-sorts each gender by estimated points, and writes a structured JSON:

    {
      "boys": [
        {
          "rank": 1,
          "name": "Ivan Ivanov",
          "rank_change": 0,          # positive = moved up
          "tournaments": "J300 Cairo",
          "points_change": 25.0,     # combined delta (singles + doubles/4)
          "total_points": 3507.25
        },
        ...
      ],
      "girls": [ ... ]
    }

Players who did not play this week keep their current points and show
tournaments = "-" and points_change = 0.

Usage
-----
    python merge_rankings.py                     # uses today's week
    python merge_rankings.py --week 2026-03-30
    python merge_rankings.py --headless --week ...
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from pathlib import Path

from src.browser import BrowserSession
from src.api import fetch_rankings


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _week_monday(anchor: datetime.date) -> datetime.date:
    return anchor - datetime.timedelta(days=anchor.weekday())


def _find_file(pattern: str, week_start: str) -> Path:
    path = Path("output") / pattern.format(week_start)
    if path.exists():
        return path
    stem = pattern.split("{")[0].rstrip("_")
    candidates = sorted(Path("output").glob(f"{stem}_*.json"), reverse=True)
    if candidates:
        print(f"[merge] No {stem} file for {week_start}, using {candidates[0].name}")
        return candidates[0]
    raise FileNotFoundError(f"No {stem}_*.json found in output/.")


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run(headless: bool, week_monday: datetime.date) -> None:
    week_start = week_monday.isoformat()

    # ── Load estimated rankings ───────────────────────────────────────────────
    est_path = _find_file("estimated_rankings_{}.json", week_start)
    print(f"[merge] Loading estimated rankings from {est_path}")
    with open(est_path, encoding="utf-8") as f:
        estimated_data = json.load(f)

    # player_id → { estimated_points, delta, current_rank }
    estimated: dict[int, dict] = {
        p["player_id"]: {
            "estimated_points": p["estimated_points"],
            "delta":            p["delta"],
            "current_rank":     p.get("current_rank"),
        }
        for p in estimated_data["players"]
    }

    # ── Load points_earned to get tournament names per player ─────────────────
    pts_path = _find_file("points_earned_{}.json", week_start)
    print(f"[merge] Loading points earned from {pts_path}")
    with open(pts_path, encoding="utf-8") as f:
        points_data = json.load(f)

    # player_id → set of tournament names played this week
    player_tournaments: dict[int, set[str]] = {}
    for tournament in points_data.get("tournaments", []):
        tname = tournament["name"]
        for result in tournament.get("results", []):
            pid = result["player_id"]
            player_tournaments.setdefault(pid, set()).add(tname)

    # ── Fetch full rankings (Boys + Girls) ────────────────────────────────────
    async with BrowserSession(headless=headless) as session:
        print("[merge] Fetching current rankings…")
        boys, girls = await asyncio.gather(
            fetch_rankings(session, "B"),
            fetch_rankings(session, "G"),
        )
    print(f"[merge] Rankings: {len(boys)} boys  /  {len(girls)} girls")

    # ── Merge + rank ──────────────────────────────────────────────────────────
    def _merge_and_rank(players: list[dict]) -> list[dict]:
        merged = []
        for p in players:
            pid  = p["playerId"]
            est  = estimated.get(pid)
            pts  = est["estimated_points"] if est else float(p.get("points") or 0)
            name = f"{p.get('playerGivenName', '')} {p.get('playerFamilyName', '')}".strip()
            old_rank = int(p.get("rank") or 0)
            merged.append({
                "player_id":      pid,
                "name":           name,
                "old_rank":       old_rank,
                "total_points":   pts,
                "delta":          est["delta"] if est else 0.0,
                "tournaments":    " / ".join(sorted(player_tournaments.get(pid, set()))) or "-",
                "rank_movement":  int(p.get("rankMovement") or 0),
                "birth_year":     p.get("birthYear"),
                "itf_points":     float(p.get("points") or 0),
            })

        merged.sort(key=lambda x: x["total_points"], reverse=True)

        result = []
        for new_rank, row in enumerate(merged, start=1):
            old_rank = row["old_rank"]
            rank_change = (old_rank - new_rank) if old_rank else 0
            result.append({
                "rank":           new_rank,
                "player_id":      str(row["player_id"]),
                "name":           row["name"],
                "itf_rank":       row["old_rank"],
                "rank_change":    rank_change,
                "rank_movement":  row.get("rank_movement", 0),
                "birth_year":     row.get("birth_year"),
                "tournaments":    row["tournaments"],
                "points_change":  round(row["delta"], 2),
                "total_points":   round(row["total_points"], 2),
                "itf_points":     round(row.get("itf_points", 0), 2),
            })
        return result

    boys_ranked  = _merge_and_rank(boys)
    girls_ranked = _merge_and_rank(girls)

    # ── Write output ──────────────────────────────────────────────────────────
    output = {
        "week_start":   week_start,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "boys":  boys_ranked,
        "girls": girls_ranked,
    }
    out_path = Path("output") / f"merged_rankings_{week_start}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[merge] Written -> {out_path}")

    latest_path = Path("output") / "latest_merged_rankings.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"[merge] Written -> {latest_path}")

    header = f"  {'Rank':>4}  {'Player':<30}  {'±Rank':>6}  {'Tournament':<25}  {'±Pts':>7}  {'Total':>9}"
    sep    = "  " + "-" * (len(header) - 2)

    print("\nBoys  top 10:")
    print(header); print(sep)
    for row in boys_ranked[:10]:
        rc = f"{row['rank_change']:+d}" if row['rank_change'] != 0 else "="
        pc = f"{row['points_change']:+.2f}" if row['points_change'] != 0 else "-"
        print(f"  {row['rank']:>4}  {row['name']:<30}  {rc:>6}  {row['tournaments']:<25}  {pc:>7}  {row['total_points']:>9.2f}")

    print("\nGirls top 10:")
    print(header); print(sep)
    for row in girls_ranked[:10]:
        rc = f"{row['rank_change']:+d}" if row['rank_change'] != 0 else "="
        pc = f"{row['points_change']:+.2f}" if row['points_change'] != 0 else "-"
        print(f"  {row['rank']:>4}  {row['name']:<30}  {rc:>6}  {row['tournaments']:<25}  {pc:>7}  {row['total_points']:>9.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Merge estimated rankings with current rankings")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--week", default=None, metavar="YYYY-MM-DD")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Clear Firestore session cookie cache before starting (use for local runs).",
    )
    args = parser.parse_args()

    if args.fresh:
        from src.browser import clear_session_cache
        clear_session_cache()

    anchor = datetime.date.fromisoformat(args.week) if args.week else datetime.date.today()
    monday = _week_monday(anchor)
    asyncio.run(run(args.headless, monday))


if __name__ == "__main__":
    main()
