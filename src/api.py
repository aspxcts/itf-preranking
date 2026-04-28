"""
ITF API wrapper functions.

Each function takes a BrowserSession and returns the relevant portion of the
parsed JSON response.  All endpoint URLs and query parameter shapes are derived
from the captured browser requests in the companion *.js files.
"""

from __future__ import annotations

import asyncio
import datetime
import json as _json

from src.browser import BrowserSession, SessionError

_BASE = "https://www.itftennis.com/tennis/api"
_WWW = "https://www.itftennis.com"


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
# Drawsheet  (navigate to the draws page and intercept the API response)
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_drawsheets_via_page(
    session: BrowserSession,
    tournament_link: str,
) -> dict[tuple[str, str], dict]:
    """
    Fetch all drawsheet data for a tournament autonomously:

      1. Navigate to the tournament's draw-results page.
      2. Intercept the GetEventFilters GET the page fires natively —
         extracts tournamentId/tourType without any external API call.
      3. Intercept GetDrawsheet POSTs the page fires (auto-fires Boys Singles).
      4. Fire in-page fetch() for any remaining events using the extracted ID,
         using the same same-origin context so Incapsula allows the requests.

    Returns:
        dict keyed by (playerTypeCode, matchTypeCode) → raw API response.
    """
    _STANDARD_EVENTS = [("B", "S"), ("G", "S"), ("B", "D"), ("G", "D")]
    draws_url = f"{_WWW}{tournament_link}draw-results/"
    captured: dict[tuple[str, str], dict] = {}
    ef_data: dict = {}   # populated from the page's own GetEventFilters GET

    # Wait out any in-progress rewarm before opening the page
    if session.context is None:
        async with session._rewarm_lock:
            pass  # release immediately — just waits for rewarm to finish
    if session.context is None:
        raise SessionError(
            f"fetch_drawsheets_via_page: no browser context for {tournament_link}"
        )

    try:
        page = await session.context.new_page()
    except Exception as _np_err:
        # Context may have been closed by a concurrent rewarm between the
        # is-None check and new_page().  Wait for the rewarm and retry once.
        async with session._rewarm_lock:
            pass
        if session.context is None:
            raise SessionError(
                f"fetch_drawsheets_via_page: no browser context for {tournament_link}"
            )
        page = await session.context.new_page()
    title = "unknown"
    try:
        # ── Intercept: capture GetEventFilters + GetDrawsheet responses ───────
        async def _on_response(response):
            url = response.url
            if "GetEventFilters" in url:
                try:
                    data = await response.json()
                    ef_data["tournamentId"] = data.get("tournamentId")
                    ef_data["tourType"]     = data.get("tourType", "N")
                except Exception:
                    pass
            elif "GetDrawsheet" in url:
                try:
                    req_data = _json.loads(response.request.post_data or "{}")
                    key = (
                        req_data.get("playerTypeCode", "?"),
                        req_data.get("matchTypeCode", "?"),
                    )
                    data = await response.json()
                    captured[key] = data
                except Exception:
                    pass

        page.on("response", _on_response)

        # ── Navigate: React fires GetEventFilters then GetDrawsheet(BS) ──────
        await page.goto(draws_url, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(5_000)

        # ── In-page fetch: fire remaining events using the page's own ID ─────
        tid = ef_data.get("tournamentId")
        if tid is not None:
            _JS = """
                async ({tournamentId, tourType, pt, mt}) => {
                    try {
                        await fetch('/tennis/api/TournamentApi/GetDrawsheet', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': '*/*',
                            },
                            body: JSON.stringify({
                                tournamentId, tourType, weekNumber: 0,
                                playerTypeCode: pt, matchTypeCode: mt,
                                eventClassificationCode: 'M',
                                drawsheetStructureCode: 'KO',
                            }),
                        });
                    } catch (e) {}
                    return null;
                }
            """
            ttyp = ef_data.get("tourType", "N")
            for pt, mt in _STANDARD_EVENTS:
                if (pt, mt) in captured:
                    continue
                await page.evaluate(
                    _JS,
                    {"tournamentId": tid, "tourType": ttyp, "pt": pt, "mt": mt},
                )
                # Random wait between each of the 4 event fetch() calls.
                # A flat 4s was still too fast — Incapsula flags the burst.
                # 6–14s mimics a human clicking through tabs.
                import random as _random
                await page.wait_for_timeout(int(_random.uniform(6_000, 14_000)))

        title = await page.title()
    except Exception as e:
        raise SessionError(f"fetch_drawsheets_via_page {tournament_link}: {e}") from e
    finally:
        # ── Always close the draw page before any external API calls ─────────
        # This prevents context rewarns (triggered by session.get/post below)
        # from destroying a still-open page and cascading failures.
        try:
            await asyncio.wait_for(page.close(), timeout=5.0)
        except Exception:
            pass

    # ── Fallback: if GetEventFilters wasn't intercepted (SSR / Incapsula-blocked
    #    XHR), call it directly.  The draw page is already closed so any rewarm
    #    triggered here won't destroy in-flight pages. ─────────────────────────
    tid = ef_data.get("tournamentId")
    if tid is None:
        tournament_key = tournament_link.rstrip("/").split("/")[-1]
        print(
            f"[draws] {tournament_key}: GetEventFilters not intercepted"
            f" — falling back to direct GET"
        )
        try:
            ef_resp = await session.get(
                f"{_BASE}/TournamentApi/GetEventFilters",
                params={"tournamentKey": tournament_key},
            )
            tid = ef_resp.get("tournamentId")
            if tid:
                ef_data["tournamentId"] = tid
                ef_data["tourType"] = ef_resp.get("tourType", "N")
                print(f"[draws] {tournament_key}: fallback tid={tid}")
            else:
                print(
                    f"[draws] {tournament_key}: fallback returned no"
                    f" tournamentId (draw not published yet?)"
                )
        except Exception as _ef_err:
            print(
                f"[draws] {tournament_key}: GetEventFilters fallback"
                f" failed: {_ef_err}"
            )

    # ── Secondary fallback: issue draw POSTs directly for any events not
    #    yet captured (covers in-page fetch being blocked by Incapsula). ───────
    if tid is not None:
        ttyp = ef_data.get("tourType", "N")
        for pt, mt in _STANDARD_EVENTS:
            if (pt, mt) in captured:
                continue
            try:
                data = await session.post(
                    f"{_BASE}/TournamentApi/GetDrawsheet",
                    body={
                        "tournamentId": tid,
                        "tourType": ttyp,
                        "weekNumber": 0,
                        "playerTypeCode": pt,
                        "matchTypeCode": mt,
                        "eventClassificationCode": "M",
                        "drawsheetStructureCode": "KO",
                    },
                )
                captured[(pt, mt)] = data
            except Exception:
                pass  # event may not exist for this tournament

    print(
        f"[draws] {tournament_link.split('/')[-2]}: "
        f"tid={tid}  title={title!r}  captured={list(captured.keys())}"
    )
    return captured


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
    Low-level drawsheet POST kept for compatibility.
    Prefer fetch_drawsheets_via_page() for new callers.
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
