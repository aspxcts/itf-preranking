"""
ITF Junior ranking points calculator.

Simulates the ITF "top-6 countable results" rule for a single player after
one week of tournament play, and computes their estimated new combined total.

Rules implemented
─────────────────
1. Top-6 countable results count toward the ranking total per discipline.
2. Any result from the same ISO calendar week exactly one year prior is
   removed from the pool *and* excluded as a non-countable replacement.
3. If fewer than 6 countable results remain (after year-ago removal plus any
   new additions), all results in the pool count — no worst-result subtraction.
4. New results this week are added to the pool.  The pool is then sorted
   descending; the top min(6, len(pool)) values form the new total.
5. Doubles points contribute as 25% of their total toward the combined ranking:
       combined = singles_total + doubles_total * 0.25
6. Zero-point non-countable entries (lost in R32/Q1/etc.) are excluded from
   the candidate pool to avoid wasting a "promotion" slot on 0 pts.

"Same ISO week last year" definition
──────────────────────────────────────
Given the week's Monday ``week_monday``:
    last_year_monday = week_monday - 52 weeks  (364 days)
    last_year_sunday = last_year_monday + 6 days
A result whose ``startDate`` falls in [last_year_monday, last_year_sunday]
is considered a "year-ago" result.

The startDate format in the API response is "DD MMM YYYY" (e.g. "07 Jul 2025").
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass
class DisciplineResult:
    """Represents one tournament result in a discipline pool."""
    tournament_name: str
    start_date: datetime.date
    points: float
    is_new: bool = False          # True if earned this week
    is_year_ago: bool = False     # True if from the same ISO week last year


@dataclass
class DisciplineSimulation:
    old_total: float
    new_total: float
    delta: float
    old_countable_count: int
    new_pool: list[DisciplineResult]   # sorted desc, up to 6 entries that count


@dataclass
class PlayerSimulation:
    singles: DisciplineSimulation
    doubles: DisciplineSimulation
    old_combined: float
    new_combined: float
    delta_combined: float


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date(date_str: str) -> datetime.date | None:
    """Parse ITF startDate strings: 'DD MMM YYYY' or ISO 'YYYY-MM-DD'."""
    if not date_str:
        return None
    date_str = date_str.strip()
    parts = date_str.split()
    if len(parts) == 3:
        try:
            day   = int(parts[0])
            month = _MONTHS.get(parts[1].lower())
            year  = int(parts[2])
            if month:
                return datetime.date(year, month, day)
        except (ValueError, AttributeError):
            pass
    # Fallback: ISO format
    try:
        return datetime.date.fromisoformat(date_str[:10])
    except ValueError:
        return None


def _year_ago_window(week_monday: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return (monday, sunday) of the same ISO week one year ago (−52 weeks)."""
    last_monday = week_monday - datetime.timedelta(weeks=52)
    return last_monday, last_monday + datetime.timedelta(days=6)


# ─────────────────────────────────────────────────────────────────────────────
# Core simulation
# ─────────────────────────────────────────────────────────────────────────────

def simulate_discipline(
    countable: list[dict],
    non_countable: list[dict],
    new_pts_this_week: list[float],
    week_monday: datetime.date,
) -> DisciplineSimulation:
    """
    Simulate one discipline (singles or doubles) for one player.

    Args:
        countable:          List of countable result dicts from GetRankingPoints.
        non_countable:      List of non-countable result dicts.
        new_pts_this_week:  Point values earned this week (from points_earned JSON).
                            May be empty if the player didn't play this discipline.
        week_monday:        Monday of the week being simulated.

    Returns:
        DisciplineSimulation with old/new totals and delta.
    """
    ya_start, ya_end = _year_ago_window(week_monday)

    def _to_result(entry: dict, is_new: bool = False) -> DisciplineResult:
        d = _parse_date(entry.get("startDate", ""))
        is_ya = (d is not None) and (ya_start <= d <= ya_end)
        return DisciplineResult(
            tournament_name=entry.get("tournamentName", ""),
            start_date=d or datetime.date.min,
            points=float(entry.get("points") or 0),
            is_new=is_new,
            is_year_ago=is_ya,
        )

    # Current totals from the live API response
    old_total = sum(float(e.get("points") or 0) for e in countable)
    old_countable_count = len(countable)

    # Build the candidate pool:
    #   - existing countable results that are NOT year-ago
    #   - non-countable results that are NOT year-ago AND have points > 0
    #   - new results from this week
    pool: list[DisciplineResult] = []

    for entry in countable:
        r = _to_result(entry)
        if not r.is_year_ago:
            pool.append(r)

    for entry in non_countable:
        r = _to_result(entry)
        if not r.is_year_ago and r.points > 0:
            pool.append(r)

    for pts in new_pts_this_week:
        pool.append(DisciplineResult(
            tournament_name="(this week)",
            start_date=week_monday,
            points=float(pts),
            is_new=True,
            is_year_ago=False,
        ))

    # Sort descending, take top 6
    pool.sort(key=lambda r: r.points, reverse=True)
    top6 = pool[:6]
    new_total = sum(r.points for r in top6)

    return DisciplineSimulation(
        old_total=old_total,
        new_total=new_total,
        delta=new_total - old_total,
        old_countable_count=old_countable_count,
        new_pool=top6,
    )


def simulate_player(
    ranking_data: dict,
    new_singles_pts: list[float],
    new_doubles_pts: list[float],
    week_monday: datetime.date,
    current_combined: float | None = None,
) -> PlayerSimulation:
    """
    Simulate both disciplines for one player and compute the new combined total.

    Args:
        ranking_data:      Normalised dict from fetch_ranking_points().
        new_singles_pts:   List of singles point values earned this week.
        new_doubles_pts:   List of doubles point values earned this week.
        week_monday:       Monday of the week being simulated.
        current_combined:  Override for the current combined total (uses
                           ranking_data["current_combined_total"] if None).

    Returns:
        PlayerSimulation with full breakdown.
    """
    singles = simulate_discipline(
        countable=ranking_data.get("singles_countable") or [],
        non_countable=ranking_data.get("singles_non_countable") or [],
        new_pts_this_week=new_singles_pts,
        week_monday=week_monday,
    )
    doubles = simulate_discipline(
        countable=ranking_data.get("doubles_countable") or [],
        non_countable=ranking_data.get("doubles_non_countable") or [],
        new_pts_this_week=new_doubles_pts,
        week_monday=week_monday,
    )

    old_combined = (
        current_combined
        if current_combined is not None
        else ranking_data.get("current_combined_total", 0.0)
    )
    new_combined = singles.new_total + doubles.new_total * 0.25

    return PlayerSimulation(
        singles=singles,
        doubles=doubles,
        old_combined=old_combined,
        new_combined=new_combined,
        delta_combined=new_combined - old_combined,
    )
