"""Views for the member hub."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from membership.models import Guild, GuildVote, Member, VotingSession
from membership.vote_forms import VoteForm


def _get_hub_context(request: HttpRequest) -> dict:  # type: ignore[type-arg]
    """Build common sidebar context for all hub pages."""
    guilds = Guild.objects.filter(is_active=True).order_by("name")
    user = request.user
    initials = ""
    if user.is_authenticated:
        email = getattr(user, "email", "") or ""
        name = getattr(user, "get_full_name", lambda: "")() or email
        parts = name.strip().split()
        if parts:
            initials = "".join(p[0].upper() for p in parts[:2])
        if not initials and email:
            initials = email[0].upper()
    return {
        "guilds": guilds,
        "user_initials": initials,
    }


def _get_member(request: HttpRequest) -> Member | None:
    """Get the Member for the logged-in user, or None."""
    try:
        return request.user.member  # type: ignore[union-attr]
    except Member.DoesNotExist:
        return None


@login_required
def guild_voting(request: HttpRequest) -> HttpResponse:
    """Guild voting page within the hub."""
    member = _get_member(request)
    ctx = _get_hub_context(request)

    if member is None:
        return render(request, "hub/guild_voting.html", {**ctx, "state": "no_member"})

    if member.status != Member.Status.ACTIVE:
        return render(request, "hub/guild_voting.html", {**ctx, "state": "inactive"})

    session = VotingSession.objects.filter(status=VotingSession.Status.OPEN).first()
    if session is None or not session.is_open_for_voting:
        return render(
            request,
            "hub/guild_voting.html",
            {**ctx, "state": "closed", "reason": "There is no voting session open right now."},
        )

    existing_votes = GuildVote.objects.filter(session=session, member=member)
    if existing_votes.exists():
        vote_names = {}
        for v in existing_votes:
            rank_label = {1: "1st", 2: "2nd", 3: "3rd"}.get(v.priority, "?")
            vote_names[rank_label] = v.guild.name
        return render(
            request,
            "hub/guild_voting.html",
            {**ctx, "state": "already_voted", "member": member, "session": session, "votes": vote_names},
        )

    guilds_active = Guild.objects.filter(is_active=True).order_by("name")
    guild_choices = [(g.name, g.name) for g in guilds_active]

    if request.method == "POST":
        form = VoteForm(guild_choices, request.POST)
        if form.is_valid():
            if GuildVote.objects.filter(session=session, member=member).exists():
                return render(
                    request,
                    "hub/guild_voting.html",
                    {**ctx, "state": "already_voted", "member": member, "session": session, "votes": {}},
                )

            guild_names = [
                form.cleaned_data["guild_1st"],
                form.cleaned_data["guild_2nd"],
                form.cleaned_data["guild_3rd"],
            ]
            from membership.vote_views import _save_votes
            from membership import airtable_sync

            _save_votes(session, member, guild_names)
            airtable_sync.sync_vote_to_airtable(
                member_name=member.display_name,
                guild_1st=guild_names[0],
                guild_2nd=guild_names[1],
                guild_3rd=guild_names[2],
                session_name=session.name,
            )
            return render(
                request,
                "hub/guild_voting.html",
                {
                    **ctx,
                    "state": "success",
                    "member": member,
                    "session": session,
                    "votes": {"1st": guild_names[0], "2nd": guild_names[1], "3rd": guild_names[2]},
                },
            )
    else:
        form = VoteForm(guild_choices)

    return render(
        request,
        "hub/guild_voting.html",
        {
            **ctx,
            "state": "voting",
            "form": form,
            "member": member,
            "session": session,
            "guilds_json": json.dumps(guild_choices),
        },
    )


@login_required
def guild_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Guild detail page."""
    guild = get_object_or_404(Guild, pk=pk, is_active=True)
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
