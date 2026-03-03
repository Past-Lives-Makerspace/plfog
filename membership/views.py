from __future__ import annotations

from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import Guild


def guild_list(request: HttpRequest) -> HttpResponse:
    guilds = Guild.objects.filter(is_active=True).annotate(vote_count=Count("votes")).order_by("name")
    return render(request, "membership/guild_list.html", {"guilds": guilds})


def guild_detail(request: HttpRequest, slug: str) -> HttpResponse:
    guild = get_object_or_404(Guild, slug=slug, is_active=True)

    context: dict = {
        "guild": guild,
        "vote_count": guild.votes.count(),
        "guild_lead": guild.guild_lead,
    }

    # Voter list — names visible only to authenticated users
    if request.user.is_authenticated:
        context["voters"] = guild.votes.select_related("member__user").order_by("priority")
        context["has_voted"] = guild.votes.filter(member__user=request.user).exists()
    else:
        context["voters"] = None
        context["has_voted"] = False

    # Active spaces leased by this guild
    active_leases = guild.active_leases.select_related("space")
    spaces = [lease.space for lease in active_leases]
    if spaces:
        context["spaces"] = spaces

    return render(request, "membership/guild_detail.html", context)
