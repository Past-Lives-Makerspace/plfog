"""Views for the member hub."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from membership.models import Guild, Member


def _get_hub_context(request: HttpRequest) -> dict[str, Any]:
    """Build common sidebar context for all hub pages."""
    guilds = Guild.objects.order_by("name")
    initials = ""
    if request.user.is_authenticated:
        member: Member | None = getattr(request.user, "member", None)
        if member is not None:
            initials = member.initials
    return {
        "guilds": guilds,
        "user_initials": initials,
    }


def _get_member(request: HttpRequest) -> Member | None:
    """Get the Member for the logged-in user, or None."""
    user = request.user
    if not user.is_authenticated:
        return None
    member: Member | None = getattr(user, "member", None)
    return member


@login_required
def guild_voting(request: HttpRequest) -> HttpResponse:
    """Guild voting page within the hub."""
    ctx = _get_hub_context(request)
    return render(request, "hub/guild_voting.html", {**ctx, "state": "closed"})


@login_required
def guild_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Guild detail page."""
    guild = get_object_or_404(Guild, pk=pk)
    ctx = _get_hub_context(request)
    return render(request, "hub/guild_detail.html", {**ctx, "guild": guild})


@login_required
def profile_settings(request: HttpRequest) -> HttpResponse:
    """Profile settings page."""
    member = _get_member(request)
    ctx = _get_hub_context(request)

    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return render(request, "hub/profile_settings.html", {**ctx, "member": None})

    if request.method == "POST":
        preferred_name = request.POST.get("preferred_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        member.preferred_name = preferred_name
        member.phone = phone
        member.save(update_fields=["preferred_name", "phone"])
        messages.success(request, "Profile updated.")
        return redirect("hub_profile_settings")

    return render(request, "hub/profile_settings.html", {**ctx, "member": member})


@login_required
def email_preferences(request: HttpRequest) -> HttpResponse:
    """Email preferences page."""
    ctx = _get_hub_context(request)

    if request.method == "POST":
        messages.success(request, "Email preferences updated.")
        return redirect("hub_email_preferences")

    return render(request, "hub/email_preferences.html", ctx)
