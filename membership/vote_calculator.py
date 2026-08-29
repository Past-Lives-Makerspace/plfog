"""Guild funding calculation per the guild voting spec.

Each voter distributes 10 points: 1st=5, 2nd=3, 3rd=2.
Contributed pool = number of paying voters × $10.
Total pool = max(contributed_pool, minimum_pool) — the floor covers cycles
where paying-voter count is below the target (~100 voters).
Guild funding = (guild_points / total_points) × total_pool.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from datetime import datetime

WEIGHTS = {
    "1st": 5,
    "2nd": 3,
    "3rd": 2,
}
DOLLARS_PER_MEMBER = sum(WEIGHTS.values())  # $10


class VoteStanding(TypedDict, total=False):
    guild_name: str
    total_points: int
    bar_pct: float


def compute_live_standings() -> list[VoteStanding]:
    """Tally live vote points from current VotePreference records.

    Only counts votes from members with a linked User — members imported from
    Airtable who never signed up to the app are excluded. See
    ``VotePreferenceQuerySet.from_signed_up_members``.

    Returns a list of dicts sorted by total points descending:
        [{"guild_name": str, "total_points": int, "bar_pct": float}, ...]
    """
    from django.db.models import Count, Q

    from membership.models import Guild

    signed_up_1st = Q(first_choice_votes__member__user__isnull=False)
    signed_up_2nd = Q(second_choice_votes__member__user__isnull=False)
    signed_up_3rd = Q(third_choice_votes__member__user__isnull=False)
    # distinct=True is essential: annotating three reverse-FK Counts on the same
    # queryset cross-joins first/second/third_choice_votes, so without distinct
    # each Count is multiplied by the other two. A guild with 1/2/3 first/second/
    # third-place votes would show 6/6/6 and score 60 points instead of 17.
    guilds = Guild.objects.filter(is_active=True).annotate(
        first=Count("first_choice_votes", filter=signed_up_1st, distinct=True),
        second=Count("second_choice_votes", filter=signed_up_2nd, distinct=True),
        third=Count("third_choice_votes", filter=signed_up_3rd, distinct=True),
    )

    results: list[VoteStanding] = []
    for g in guilds:
        points = g.first * WEIGHTS["1st"] + g.second * WEIGHTS["2nd"] + g.third * WEIGHTS["3rd"]
        if points > 0:
            results.append(VoteStanding(guild_name=g.name, total_points=points))

    if not results:
        return []

    results.sort(key=lambda x: x["total_points"], reverse=True)
    max_points = results[0]["total_points"]
    for r in results:
        r["bar_pct"] = round(r["total_points"] / max_points * 100, 1)
    return results


def compute_new_votes_since(since: datetime | None) -> list[VoteStanding]:
    """Tally points from VotePreferences updated after ``since``.

    Represents the "new votes this month" view — votes cast or changed since
    the last snapshot was taken. If ``since`` is None (no prior snapshot),
    every signed-up vote is considered new.
    """
    from django.db.models import Count, Q

    from membership.models import Guild

    first_q = Q(first_choice_votes__member__user__isnull=False)
    second_q = Q(second_choice_votes__member__user__isnull=False)
    third_q = Q(third_choice_votes__member__user__isnull=False)
    if since is not None:
        first_q &= Q(first_choice_votes__updated_at__gt=since)
        second_q &= Q(second_choice_votes__updated_at__gt=since)
        third_q &= Q(third_choice_votes__updated_at__gt=since)

    # See note on distinct=True in compute_live_standings — same cross-join
    # multiplication applies here.
    guilds = Guild.objects.filter(is_active=True).annotate(
        first=Count("first_choice_votes", filter=first_q, distinct=True),
        second=Count("second_choice_votes", filter=second_q, distinct=True),
        third=Count("third_choice_votes", filter=third_q, distinct=True),
    )

    results: list[VoteStanding] = []
    for g in guilds:
        points = g.first * WEIGHTS["1st"] + g.second * WEIGHTS["2nd"] + g.third * WEIGHTS["3rd"]
        if points > 0:
            results.append(VoteStanding(guild_name=g.name, total_points=points))

    if not results:
        return []

    results.sort(key=lambda x: x["total_points"], reverse=True)
    max_points = results[0]["total_points"]
    for r in results:
        r["bar_pct"] = round(r["total_points"] / max_points * 100, 1)
    return results


def calculate_results(
    votes: list[dict[str, Any]],
    *,
    paying_voter_count: int | None = None,
    minimum_pool: Decimal | int = 0,
) -> dict[str, Any]:
    """Calculate proportional guild funding from ranked votes.

    Args:
        votes: list of dicts with guild_1st, guild_2nd, guild_3rd (guild names).
        paying_voter_count: number of voters who contribute to the funding pool.
            Defaults to len(votes) if not provided (all voters are paying).
        minimum_pool: dollar floor applied to the contributed pool. The total
            pool is ``max(contributed_pool, minimum_pool)``. Defaults to 0.

    Returns:
        dict with total_pool, contributed_pool, minimum_pool, total_points,
        votes_cast, and a per-guild results list sorted by funding descending.
    """
    guild_scores: dict[str, dict[str, int]] = defaultdict(
        lambda: {"votes_1st": 0, "votes_2nd": 0, "votes_3rd": 0, "total_points": 0}
    )

    for vote in votes:
        for rank_key, weight in [
            ("guild_1st", WEIGHTS["1st"]),
            ("guild_2nd", WEIGHTS["2nd"]),
            ("guild_3rd", WEIGHTS["3rd"]),
        ]:
            guild_name = vote[rank_key]
            if not guild_name:
                # 2nd/3rd are optional; a blank one contributes nothing. A blank 1st
                # choice is never valid (required at the form + DB level), so still flag it.
                if rank_key == "guild_1st":
                    raise ValueError(f"Empty guild name in vote for rank '{rank_key}'")
                continue
            guild_scores[guild_name]["total_points"] += weight
            vote_count_key = rank_key.replace("guild_", "votes_")
            guild_scores[guild_name][vote_count_key] += 1

    votes_cast = len(votes)
    pool_contributors = paying_voter_count if paying_voter_count is not None else votes_cast
    contributed_pool = Decimal(DOLLARS_PER_MEMBER * pool_contributors)
    floor = Decimal(minimum_pool)
    total_pool = max(contributed_pool, floor)
    total_points = sum(s["total_points"] for s in guild_scores.values())

    results: list[dict[str, Any]] = []
    for guild_name, scores in guild_scores.items():
        points = scores["total_points"]
        share = Decimal(points) / Decimal(total_points)
        funding = (share * total_pool).quantize(Decimal("0.01"))
        share_pct = float((share * Decimal(100)).quantize(Decimal("0.1")))
        results.append(
            {
                "guild_name": guild_name,
                "votes_1st": scores["votes_1st"],
                "votes_2nd": scores["votes_2nd"],
                "votes_3rd": scores["votes_3rd"],
                "total_points": points,
                "share_pct": share_pct,
                "funding": funding,
            }
        )

    results.sort(key=lambda x: x["funding"], reverse=True)

    return {
        "total_pool": total_pool,
        "contributed_pool": contributed_pool,
        "minimum_pool": floor,
        "total_points": total_points,
        "votes_cast": votes_cast,
        "results": results,
    }


def results_to_json(results_data: dict[str, Any]) -> str:
    """Serialize results for storage. Decimals are rendered as strings."""
    return json.dumps(results_data, default=str)
