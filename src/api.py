"""
ITF API wrapper functions.

Each function takes a BrowserSession and returns the relevant portion of the
parsed JSON response.  All endpoint URLs and query parameter shapes are derived
from the captured browser requests in the companion *.js files.
"""

from __future__ import annotations

from src.browser import BrowserSession

_BASE = "https://www.itftennis.com/tennis/api"


# ─────────────────────────────────────────────────────────────────────────────
# Rankings
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_rankings(session: BrowserSession, player_type_code: str) -> list[dict]:
    """
    Fetch the top-1000 ITF junior rankings for one gender.

    Args:
        player_type_code: "B" for Boys, "G" for Girls.

    Returns:
        List of player dicts (playerId, rank, points, …).
    """
    data = await session.get(
        f"{_BASE}/PlayerRankApi/GetPlayerRankings",
        params={
            "circuitCode": "JT",
            "playerTypeCode": player_type_code,
            "ageCategoryCode": "",
            "juniorRankingType": "itf",
            "take": 1000,
            "skip": 0,
            "isOrderAscending": "true",
        },
    )
    return data.get("items", [])


# ─────────────────────────────────────────────────────────────────────────────
# Calendar
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_calendar(
    session: BrowserSession,
    date_from: str,
    date_to: str,
) -> list[dict]:
    """
    Fetch the tournament calendar for a date range.

    Args:
        date_from / date_to: ISO date strings (YYYY-MM-DD). The API returns
            tournaments whose schedule overlaps with this range.

    Returns:
        List of tournament dicts (tournamentKey, category, name, …).
    """
    data = await session.get(
        f"{_BASE}/TournamentApi/GetCalendar",
        params={
            "circuitCode": "JT",
            "searchString": "",
            "skip": 0,
            "take": 100,
            "nationCodes": "",
            "zoneCodes": "",
            "dateFrom": date_from,
            "dateTo": date_to,
            "indoorOutdoor": "",
            "categories": "",
            "isOrderAscending": "true",
            "orderField": "startDate",
            "surfaceCodes": "",
            "singlesDrawFormat": "",
        },
    )
    return data.get("items", [])


# ─────────────────────────────────────────────────────────────────────────────
# Event filters
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_event_filters(
    session: BrowserSession, tournament_key: str
) -> dict:
    """
    Fetch the event filter tree for a tournament and extract all playable
    event combinations.

    Returns a dict::
        {
            "tournamentId": int,
            "tourType": str,                              # "N" for normal
            "events": [
                (playerTypeCode, matchTypeCode, eventClassCode, drawStructCode),
                ...
            ]
        }

    For a standard junior tournament this will be:
        ("B", "S", "M", "KO"), ("B", "D", "M", "KO"),
        ("G", "S", "M", "KO"), ("G", "D", "M", "KO")
    """
    data = await session.get(
        f"{_BASE}/TournamentApi/GetEventFilters",
        params={"tournamentKey": tournament_key},
    )
    events = _walk_filter_tree(data.get("filters") or [])
    return {
        "tournamentId": data["tournamentId"],
        "tourType": data.get("tourType") or "N",
        "events": events,
    }


def _walk_filter_tree(
    nodes: list[dict],
    path: tuple = (),
) -> list[tuple[str, str, str, str]]:
    """
    Recursively walk the nested ITF filter tree and collect all leaf-node
    combinations as 4-tuples:
        (playerTypeCode, matchTypeCode, eventClassificationCode, drawsheetStructureCode)

    Tree shape (from getEvent_response.json):
        filters[]           → playerTypeCode (B/G)
          subFilter[]       → matchTypeCode (S/D)
            subFilter[]     → eventClassificationCode (M=Main/Q=Qualifying)
              subFilter[]   → drawsheetStructureCode (KO/RR)  ← leaf
    """
    results: list[tuple[str, str, str, str]] = []
    for node in nodes:
        data_name = node.get("dataName", "")
        value_code = node.get("valueCode", "")
        sub_filters = node.get("subFilter") or []
        new_path = (*path, (data_name, value_code))
        if not sub_filters:
            # Leaf node — build the combo from accumulated path entries
            combo = dict(new_path)
            results.append((
                combo.get("playerTypeCode", ""),
                combo.get("matchTypeCode", ""),
                combo.get("eventClassificationCode", ""),
                combo.get("drawsheetStructureCode", ""),
            ))
        else:
            results.extend(_walk_filter_tree(sub_filters, new_path))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Drawsheet
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_drawsheet(
    session: BrowserSession,
    tournament_id: int,
    tour_type: str,
    player_type_code: str,
    match_type_code: str,
    event_class_code: str = "M",
    draw_structure_code: str = "KO",
) -> dict:
    """
    POST to GetDrawsheet and return the raw response dict.

    Args:
        tournament_id:     Numeric ID from GetEventFilters response.
        tour_type:         "N" for standard junior tournaments.
        player_type_code:  "B" or "G".
        match_type_code:   "S" (singles) or "D" (doubles).
        event_class_code:  "M" = Main Draw (default).
        draw_structure_code: "KO" = Knockout (default).
    """
    return await session.post(
        f"{_BASE}/TournamentApi/GetDrawsheet",
        body={
            "tournamentId": tournament_id,
            "tourType": tour_type,
            "weekNumber": 0,
            "playerTypeCode": player_type_code,
            "matchTypeCode": match_type_code,
            "eventClassificationCode": event_class_code,
            "drawsheetStructureCode": draw_structure_code,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ranking points breakdown
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_ranking_points(session: BrowserSession, player_id: int) -> dict:
    """
    Fetch a player's current ranking-points breakdown (countable + non-countable
    for both Singles and Doubles) in a single API call.

    The matchTypeCode="S" parameter returns a response that contains both
    disciplines in the ``countable`` array (index 0 = Singles, index 1 = Doubles).

    Returns a normalised dict::
        {
            "current_combined_total": float,
            "singles_countable":      [...],
            "singles_non_countable":  [...],
            "doubles_countable":      [...],
            "doubles_non_countable":  [...],
        }

    Each entry in the lists is a dict with at least:
        {"tournamentName": str, "startDate": str, "points": float, ...}
    """
    data = await session.get(
        f"{_BASE}/PlayerRankApi/GetRankingPoints",
        params={
            "circuitCode": "JT",
            "matchTypeCode": "S",
            "playerId": player_id,
        },
    )

    def _breakdown(discipline_index: int, section: str) -> list[dict]:
        try:
            entries = (
                data["countable"][discipline_index]
                [f"{section}Points"]["pointsBreakdown"]
            )
            return entries or []
        except (KeyError, IndexError, TypeError):
            return []

    try:
        combined_total = float(data["Value"]["Value"])
    except (KeyError, TypeError, ValueError):
        combined_total = 0.0

    return {
        "current_combined_total": combined_total,
        "singles_countable":      _breakdown(0, "countable"),
        "singles_non_countable":  _breakdown(0, "nonCountable"),
        "doubles_countable":      _breakdown(1, "countable"),
        "doubles_non_countable":  _breakdown(1, "nonCountable"),
    }
