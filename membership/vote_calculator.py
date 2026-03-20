"""Guild funding calculation per the guild voting spec.

Each voter distributes 10 points: 1st=5, 2nd=3, 3rd=2.
Funding pool = number of paying voters × $10.
Guild funding = (guild_points / total_points) × pool.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

WEIGHTS = {
    "1st": 5,
    "2nd": 3,
    "3rd": 2,
}
DOLLARS_PER_MEMBER = sum(WEIGHTS.values())  # $10


def calculate_results(
    votes: list[dict[str, Any]],
    paying_voter_count: int | None = None,
) -> dict[str, Any]:
    """Calculate proportional guild funding from ranked votes.

    Args:
        votes: list of dicts with guild_1st, guild_2nd, guild_3rd (guild names)
        paying_voter_count: number of voters who contribute to the funding pool.
            Defaults to len(votes) if not provided (all voters are paying).

    Returns:
        dict with total_pool, results list, votes_cast.
    """
    guild_scores: dict[str, dict[str, float]] = defaultdict(
        lambda: {"votes_1st": 0, "votes_2nd": 0, "votes_3rd": 0, "total_points": 0}
    )

    for vote in votes:
        for rank_key, weight in [
            ("guild_1st", WEIGHTS["1st"]),
            ("guild_2nd", WEIGHTS["2nd"]),
            ("guild_3rd", WEIGHTS["3rd"]),
        ]:
            guild_name = vote.get(rank_key, "")
            if guild_name:
                guild_scores[guild_name]["total_points"] += weight
                vote_count_key = rank_key.replace("guild_", "votes_")
                guild_scores[guild_name][vote_count_key] += 1

    votes_cast = len(votes)
    pool_contributors = paying_voter_count if paying_voter_count is not None else votes_cast
    total_pool = DOLLARS_PER_MEMBER * pool_contributors
    total_points = sum(s["total_points"] for s in guild_scores.values())

    results: list[dict[str, Any]] = []
    for guild_name, scores in guild_scores.items():
        points = scores["total_points"]
        share = points / total_points
        funding = round(share * total_pool, 2)
        results.append(
            {
                "guild_name": guild_name,
                "votes_1st": scores["votes_1st"],
                "votes_2nd": scores["votes_2nd"],
                "votes_3rd": scores["votes_3rd"],
                "total_points": points,
                "share_pct": round(share * 100, 1),
                "funding": funding,
            }
        )

    results.sort(key=lambda x: x["funding"], reverse=True)

    return {
        "total_pool": total_pool,
        "total_points": total_points,
        "votes_cast": votes_cast,
        "results": results,
    }


def results_to_json(results_data: dict[str, Any]) -> str:
    """Serialize results for storage."""
    return json.dumps(results_data)
