"""
Knockout drawsheet parser.

Converts a raw GetDrawsheet API response into a list of PlayerResult objects,
each representing a player's exit round and the ITF points they earned.

Round → tier mapping
─────────────────────
We use "depth from the Final" to map round numbers to tier labels, which
automatically handles any draw size (32, 64, 128, etc.):

    depth = total_rounds − round_number

    depth 0  →  Final      loser = "F",  winner = "W"
    depth 1  →  SF         loser = "SF"
    depth 2  →  QF         loser = "QF"
    depth 3  →  R16        loser = "R16"
    depth 4  →  R32        loser = "R32"
    depth ≥ 5 → no tracked round → 0 pts (e.g., R1 of a 64-draw)

Example — J300, 64-draw (6 rounds):
    R1 losers  (depth 5): 0 pts
    R32 losers (depth 4): 30 pts
    R16 losers (depth 3): 60 pts
    QF  losers (depth 2): 100 pts
    SF  losers (depth 1): 140 pts
    Finalist   (depth 0): 210 pts
    Winner     (depth 0): 300 pts
"""

from __future__ import annotations

from dataclasses import dataclass

# Maps depth-from-final to the tier key used in points_table.json
_DEPTH_TO_LOSER_TIER: dict[int, str] = {
    0: "F",
    1: "SF",
    2: "QF",
    3: "R16",
    4: "R32",
}

# playStatusCode values that represent a completed match (winner determined)
_COMPLETED_STATUS = {"PC", "WO", "RET", "DEF"}


@dataclass
class PlayerResult:
    """Points earned by one player in one event of one tournament."""
    player_id: int
    given_name: str
    family_name: str
    nationality: str
    event: str          # "BS", "GS", "BD", "GD"
    round_reached: str  # "W", "F", "SF", "QF", "R16", "R32"
    points: float
    draw_position: int  # 1-based position in the full draw (from R1 match order)


def parse_drawsheet(
    drawsheet: dict,
    category: str,
    player_type_code: str,
    match_type_code: str,
    points_table: dict,
) -> list[PlayerResult]:
    """
    Parse a knockout drawsheet and return one PlayerResult per player per
    tracked round (i.e., the round in which that player exited — or won).

    Args:
        drawsheet:         Raw dict from GetDrawsheet API.
        category:          Tournament category key, e.g. "J300", "J200".
        player_type_code:  "B" or "G".
        match_type_code:   "S" or "D".
        points_table:      Loaded points_table.json dict.

    Returns:
        List of PlayerResult; may be empty if no matches are complete yet.
    """
    ko_groups = drawsheet.get("koGroups") or []
    if not ko_groups:
        return []
    rounds = ko_groups[0].get("rounds") or []
    if not rounds:
        return []

    total_rounds = max(r["roundNumber"] for r in rounds)
    event_code = f"{player_type_code}{match_type_code}"   # e.g. "BS"
    mt_key = "singles" if match_type_code == "S" else "doubles"

    # ── Build draw-position map ─────────────────────────────────────────────────
    # Round 1 is the first round (roundNumber == 1). In a 32-draw that is R32
    # (32 players, 16 matches). Each match has 2 teams: team[0] = top half of the
    # slot (odd position), team[1] = bottom half (even position).
    # We assign:
    #   draw_position = (match_index_in_round * 2) + team_index   (0-based)
    # then convert to 1-based. Players who enter in later rounds (byes / late
    # arrivals due to missing R1 data) get draw_position = None and are placed at
    # the end.
    draw_pos_by_player: dict[int, int] = {}  # player_id → 1-based R1 position

    first_round = next((r for r in rounds if r["roundNumber"] == 1), None)
    if first_round:
        for mi, match in enumerate(first_round.get("matches") or []):
            teams = match.get("teams") or []
            for ti, team in enumerate(teams):
                for player in team.get("players") or []:
                    if player is None:
                        continue
                    pid = player["playerId"]
                    if pid not in draw_pos_by_player:
                        draw_pos_by_player[pid] = mi * 2 + ti + 1  # 1-based

    results: list[PlayerResult] = []

    # Track winner data so we can later find still-active players.
    # winner_data[pid] = (min_won_depth, player_dict)
    # "min_won_depth" is the smallest depth at which the player won a match
    # (smaller depth = further into tournament).
    winner_data: dict[int, tuple[int, dict]] = {}

    for rnd in rounds:
        round_number = rnd["roundNumber"]
        depth = total_rounds - round_number          # 0 = Final, 1 = SF, …
        loser_tier = _DEPTH_TO_LOSER_TIER.get(depth) # None → depth ≥ 5

        for match in rnd.get("matches") or []:
            # Skip BYEs and matches not yet played
            if match.get("resultStatusCode") == "BYE":
                continue
            if match.get("playStatusCode") not in _COMPLETED_STATUS:
                continue

            teams = match.get("teams") or []
            if len(teams) != 2:
                continue

            # ── Record winner data for active-player tracking ──────────────
            # Use == True (not `is True`) so that integer 1 is also accepted,
            # since some ITF API responses return isWinner as 0/1 rather than
            # false/true.
            winner_team = next(
                (t for t in teams if t.get("isWinner") == True), None  # noqa: E712
            )
            if winner_team:
                for player in winner_team.get("players") or []:
                    if player is None:
                        continue
                    pid = player["playerId"]
                    existing_depth = winner_data.get(pid, (total_rounds + 1, None))[0]
                    if depth < existing_depth:
                        winner_data[pid] = (depth, player)

            # ── Process losing side + winner of the Final ──────────────────
            for tier, is_winner_side in (
                (loser_tier, False),  # always attempt loser
                ("W",        True),   # winner only in the Final
            ):
                if tier is None:
                    continue  # depth ≥ 5, no points for this round
                if is_winner_side and depth != 0:
                    continue  # Only award "W" in the Final

                team = next(
                    (t for t in teams if t.get("isWinner") == is_winner_side),
                    None,
                )
                if team is None:
                    continue

                pts = _lookup_points(points_table, category, mt_key, tier) or 0

                for player in team.get("players") or []:
                    if player is None:
                        continue  # BYE slot / unpaired doubles slot
                    pid = player["playerId"]
                    results.append(PlayerResult(
                        player_id=pid,
                        given_name=player.get("givenName", ""),
                        family_name=player.get("familyName", ""),
                        nationality=player.get("nationality", ""),
                        event=event_code,
                        round_reached=tier,
                        points=pts,
                        draw_position=draw_pos_by_player.get(pid, 9999),
                    ))

    # ── Add still-active players ────────────────────────────────────────────
    # Any player who won a match but never appeared as a loser is still in the
    # tournament.  We add them with points=0 so the bracket can display them.
    loser_pids: set[int] = {r.player_id for r in results}
    for pid, (won_depth, player) in winner_data.items():
        if pid in loser_pids:
            continue  # already recorded as exiting in some round
        # They won at won_depth; the next round they're "in" is at won_depth-1.
        active_depth = won_depth - 1
        if active_depth < 0:
            # Won the Final — should already be in results as "W".
            active_tier: str | None = "W"
        else:
            active_tier = _DEPTH_TO_LOSER_TIER.get(active_depth)
        if active_tier is None:
            continue  # no tracked tier for this depth
        pts = _lookup_points(points_table, category, mt_key, active_tier) or 0
        results.append(PlayerResult(
            player_id=pid,
            given_name=player.get("givenName", ""),
            family_name=player.get("familyName", ""),
            nationality=player.get("nationality", ""),
            event=event_code,
            round_reached=active_tier,
            points=pts,  # guaranteed minimum earned by reaching this round
            draw_position=draw_pos_by_player.get(pid, 9999),
        ))

    return results


def _lookup_points(
    table: dict, category: str, match_type: str, tier: str
) -> float | None:
    """
    Look up the point value for a given category / match type / tier.

    Returns None if the category doesn't exist in the table, or if the
    specific tier is explicitly null (e.g. J60 has no R32 points).
    """
    tier_map = table.get(match_type, {}).get(category)
    if tier_map is None:
        return None
    val = tier_map.get(tier)
    if isinstance(val, list):
        # ITF_JUNIOR_FINALS uses arrays like [550, 490]; take the maximum.
        return val[0]
    return val  # may be None (JSON null) → caller treats as 0
