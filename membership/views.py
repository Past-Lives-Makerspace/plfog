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
    return render(request, "membership/guild_detail.html", {"guild": guild})
