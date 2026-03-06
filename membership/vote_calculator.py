"""Vote weighting and result calculation.

Implements the spreadsheet formula from '2026 Ranking Calculator':
1. Weighted votes: 1st=$5, 2nd=$3, 3rd=$2
2. Total pool = eligible_members * $10
3. Non-vote $ = total_pool - sum(weighted_votes)
4. Each guild gets: weighted_votes + (guild_share% * non_vote_$)
   where guild_share% = guild_weighted / total_weighted
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
    eligible_member_count: int,
) -> dict[str, Any]:
    """Calculate weighted vote results with non-vote redistribution.

    Args:
        votes: list of dicts with guild_1st, guild_2nd, guild_3rd (guild names)
        eligible_member_count: total paying members

    Returns:
        dict with total_pool, results list, votes_cast, etc.
    """
    guild_scores: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"votes_1st": 0, "votes_2nd": 0, "votes_3rd": 0, "weighted_amount": 0}
    )

    for vote in votes:
        for rank_key, weight in [
            ("guild_1st", WEIGHTS["1st"]),
            ("guild_2nd", WEIGHTS["2nd"]),
            ("guild_3rd", WEIGHTS["3rd"]),
        ]:
            guild_name = vote.get(rank_key, "")
            if guild_name:
                guild_scores[guild_name]["weighted_amount"] += weight
                vote_count_key = rank_key.replace("guild_", "votes_")
                guild_scores[guild_name][vote_count_key] += 1

    total_pool = DOLLARS_PER_MEMBER * eligible_member_count
    total_weighted = sum(s["weighted_amount"] for s in guild_scores.values())

    # Non-vote redistribution: money from non-voters distributed proportionally
    non_vote_dollars = total_pool - total_weighted

    results = []
    for guild_name, scores in guild_scores.items():
        weighted = scores["weighted_amount"]
        if total_weighted > 0:
            guild_share_pct = weighted / total_weighted
            redistributed = guild_share_pct * non_vote_dollars
        else:
            redistributed = 0  # pragma: no cover — defensive; guild_scores always has positive weights

        disbursement = round(weighted + redistributed, 2)
        results.append(
            {
                "guild_name": guild_name,
                "votes_1st": scores["votes_1st"],
                "votes_2nd": scores["votes_2nd"],
                "votes_3rd": scores["votes_3rd"],
                "weighted_amount": weighted,
                "disbursement": disbursement,
            }
        )

    results.sort(key=lambda x: x["disbursement"], reverse=True)

    return {
        "total_pool": total_pool,
        "total_weighted": total_weighted,
        "non_vote_dollars": non_vote_dollars,
        "votes_cast": len(votes),
        "eligible_member_count": eligible_member_count,
        "results": results,
    }


def results_to_json(results_data: dict[str, Any]) -> str:
    """Serialize results for storage."""
    return json.dumps(results_data)
