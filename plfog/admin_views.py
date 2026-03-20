"""Custom admin views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from membership.models import FundingSnapshot, VotePreference
from membership.vote_calculator import calculate_results


@require_POST
@staff_member_required
def take_snapshot(request: HttpRequest) -> HttpResponse:
    """Create a funding snapshot from current vote preferences."""
    preferences = VotePreference.objects.select_related(
        "member__membership_plan",
        "guild_1st",
        "guild_2nd",
        "guild_3rd",
    ).all()

    paying_prefs = preferences.filter(member__membership_plan__monthly_price__gt=0)
    paying_count = paying_prefs.count()

    votes = [
        {
            "guild_1st": pref.guild_1st.name,
            "guild_2nd": pref.guild_2nd.name,
            "guild_3rd": pref.guild_3rd.name,
        }
        for pref in preferences
    ]

    if not votes:
        messages.warning(request, "No votes to snapshot.")
        return redirect("admin:index")

    calc = calculate_results(votes, paying_voter_count=paying_count)
    pool = calc["total_pool"]

    now = timezone.now()
    cycle_label = now.strftime("%B %Y")

    FundingSnapshot.objects.create(
        cycle_label=cycle_label,
        contributor_count=paying_count,
        funding_pool=pool,
        results=calc,
    )

    messages.success(request, f"Snapshot for {cycle_label} created successfully.")
    return redirect("admin:index")
