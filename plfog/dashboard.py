"""Admin dashboard callback for Unfold."""

from __future__ import annotations

from django.db.models import Count
from django.http import HttpRequest

from membership.models import FundingSnapshot, Guild, Member, VotePreference


def dashboard_callback(request: HttpRequest, context: dict) -> dict:
    """Populate voting stats for the admin dashboard."""
    active_members = Member.objects.filter(status=Member.Status.ACTIVE).count()
    total_voters = VotePreference.objects.count()
    paying_voters = VotePreference.objects.filter(
        member__membership_plan__monthly_price__gt=0,
    ).count()
    active_guilds = Guild.objects.filter(is_active=True).count()
    projected_pool = paying_voters * 10
    participation_pct = round(total_voters / active_members * 100) if active_members else 0

    # Current vote leaders
    guilds = (
        Guild.objects.filter(is_active=True)
        .annotate(
            first=Count("first_choice_votes"),
            second=Count("second_choice_votes"),
            third=Count("third_choice_votes"),
        )
        .order_by("-first", "-second", "-third")
    )

    top_guilds = []
    for g in guilds:
        points = g.first * 5 + g.second * 3 + g.third * 2
        if points > 0:
            top_guilds.append(
                {
                    "name": g.name,
                    "first": g.first,
                    "second": g.second,
                    "third": g.third,
                    "points": points,
                }
            )
    top_guilds.sort(key=lambda x: x["points"], reverse=True)

    last_snapshot = FundingSnapshot.objects.order_by("-snapshot_at").first()

    context["stats"] = {
        "active_members": active_members,
        "total_voters": total_voters,
        "paying_voters": paying_voters,
        "active_guilds": active_guilds,
        "projected_pool": projected_pool,
        "participation_pct": participation_pct,
        "top_guilds": top_guilds,
        "last_snapshot": last_snapshot,
    }
    return context
