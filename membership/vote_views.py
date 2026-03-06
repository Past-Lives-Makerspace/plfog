"""Views for guild voting system."""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.signing import BadSignature, SignatureExpired
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from . import airtable_sync, vote_calculator, vote_emails, vote_tokens
from .models import Guild, GuildVote, VotingSession
from .vote_forms import CreateSessionForm, VoteForm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public Views
# ---------------------------------------------------------------------------


def _verify_voter(request: HttpRequest, token: str) -> HttpResponse | tuple[str, dict, VotingSession]:
    """Verify token, fetch member, check session. Returns (member_record_id, member, session) or error response."""
    try:
        data = vote_tokens.verify_vote_token(token)
    except SignatureExpired:
        return render(request, "membership/voting/closed.html", {"reason": "Your voting link has expired."})
    except BadSignature:
        return HttpResponseBadRequest("Invalid voting link.")

    session = get_object_or_404(VotingSession, pk=data["session_id"])

    if not session.is_open_for_voting:
        return render(
            request,
            "membership/voting/closed.html",
            {"reason": f"Voting for {session.name} has closed.", "session": session},
        )

    member_record_id = str(data["member_record_id"])
    try:
        member = airtable_sync.get_member(member_record_id)
    except Exception:
        logger.exception("Failed to fetch member %s from Airtable", member_record_id)
        return render(
            request,
            "membership/voting/closed.html",
            {"reason": "We're having trouble verifying your membership right now. Please try again in a few minutes."},
            status=503,
        )

    if member.get("status") != "Active":
        return HttpResponseBadRequest("This member is not currently active.")

    return member_record_id, member, session


def _check_already_voted(
    request: HttpRequest, member: dict, session: VotingSession, member_record_id: str
) -> HttpResponse | None:
    """Return an already-voted response if this member has voted, else None."""
    existing_votes = GuildVote.objects.filter(session=session, member_airtable_id=member_record_id)
    if not existing_votes.exists():
        return None
    vote_names = {}
    for v in existing_votes:
        rank_label = {1: "1st", 2: "2nd", 3: "3rd"}.get(v.priority, "?")
        vote_names[rank_label] = v.guild.name
    return render(
        request,
        "membership/voting/already_voted.html",
        {"member": member, "session": session, "votes": vote_names},
    )


def _save_votes(session: VotingSession, member_record_id: str, member_name: str, guild_names: list[str]) -> None:
    """Persist votes and update session count atomically."""
    with transaction.atomic():
        for priority, guild_name in enumerate(guild_names, start=1):
            guild_obj, _created = Guild.objects.get_or_create(name=guild_name)
            GuildVote.objects.create(
                session=session,
                member_airtable_id=member_record_id,
                member_name=member_name,
                guild=guild_obj,
                priority=priority,
            )

        session.votes_cast = GuildVote.objects.filter(session=session).values("member_airtable_id").distinct().count()
        session.save(update_fields=["votes_cast"])


def vote(request: HttpRequest, token: str) -> HttpResponse:
    """Public voting page. Token encodes member + session."""
    result = _verify_voter(request, token)
    if isinstance(result, HttpResponse):
        return result
    member_record_id, member, session = result

    already = _check_already_voted(request, member, session, member_record_id)
    if already:
        return already

    at_guilds = airtable_sync.get_voteable_guilds()
    guild_choices = [(g["name"], g["name"]) for g in at_guilds]

    if request.method == "POST":
        form = VoteForm(guild_choices, request.POST)
        if form.is_valid():
            # Double-check no duplicate (race condition guard)
            if GuildVote.objects.filter(session=session, member_airtable_id=member_record_id).exists():
                return render(
                    request,
                    "membership/voting/already_voted.html",
                    {
                        "member": member,
                        "session": session,
                        "votes": {"1st": "N/A", "2nd": "N/A", "3rd": "N/A"},
                    },
                )

            guild_names = [
                form.cleaned_data["guild_1st"],
                form.cleaned_data["guild_2nd"],
                form.cleaned_data["guild_3rd"],
            ]

            _save_votes(session, member_record_id, member["name"], guild_names)

            airtable_sync.sync_vote_to_airtable(
                member_name=member["name"],
                member_airtable_id=member_record_id,
                guild_1st=guild_names[0],
                guild_2nd=guild_names[1],
                guild_3rd=guild_names[2],
                session_name=session.name,
            )

            return render(
                request,
                "membership/voting/vote_success.html",
                {
                    "member": member,
                    "session": session,
                    "votes": {"1st": guild_names[0], "2nd": guild_names[1], "3rd": guild_names[2]},
                },
            )
    else:
        form = VoteForm(guild_choices)

    return render(
        request,
        "membership/voting/vote.html",
        {
            "form": form,
            "member": member,
            "session": session,
            "guilds_json": json.dumps(guild_choices),
        },
    )


def voting_results(request: HttpRequest, session_id: int) -> HttpResponse:
    """Public results page for a completed session."""
    session = get_object_or_404(VotingSession, pk=session_id)
    if session.status != VotingSession.Status.CALCULATED or not session.results_summary:
        return render(
            request,
            "membership/voting/closed.html",
            {"reason": "Results are not yet available for this session."},
        )
    return render(
        request,
        "membership/voting/results.html",
        {"session": session, "results": session.results_summary},
    )


# ---------------------------------------------------------------------------
# Admin Views
# ---------------------------------------------------------------------------


@staff_member_required
def voting_dashboard(request: HttpRequest) -> HttpResponse:
    """Admin dashboard showing sessions and current status."""
    sessions = VotingSession.objects.all()
    active = sessions.filter(status=VotingSession.Status.OPEN).first()

    member_count = 0
    vote_count = 0
    airtable_error = None

    if active:
        try:
            member_count = len(airtable_sync.get_eligible_members())
        except Exception as e:
            airtable_error = str(e)
        vote_count = active.votes_cast

    return render(
        request,
        "membership/voting/admin/dashboard.html",
        {
            "sessions": sessions,
            "active_session": active,
            "member_count": member_count,
            "vote_count": vote_count,
            "airtable_error": airtable_error,
        },
    )


@staff_member_required
def voting_create_session(request: HttpRequest) -> HttpResponse:
    """Create a new voting session."""
    if request.method == "POST":
        form = CreateSessionForm(request.POST)
        if form.is_valid():
            members = airtable_sync.get_eligible_members()
            session = VotingSession.objects.create(
                name=form.cleaned_data["name"],
                open_date=form.cleaned_data["open_date"],
                close_date=form.cleaned_data["close_date"],
                eligible_member_count=len(members),
            )
            # Sync to Airtable
            at_id = airtable_sync.sync_session_to_airtable(
                session_id=session.pk,
                name=session.name,
                open_date=session.open_date,
                close_date=session.close_date,
                status=session.status,
                eligible_member_count=session.eligible_member_count,
            )
            if at_id:
                session.airtable_record_id = at_id
                session.save(update_fields=["airtable_record_id"])

            messages.success(request, f"Session '{session.name}' created with {len(members)} eligible members.")
            return redirect("voting_dashboard")
    else:
        form = CreateSessionForm()

    return render(request, "membership/voting/admin/create_session.html", {"form": form})


@staff_member_required
def voting_send_emails(request: HttpRequest, session_id: int) -> HttpResponse:
    """Preview and send voting emails."""
    session = get_object_or_404(VotingSession, pk=session_id)
    members = airtable_sync.get_eligible_members()
    members_with_email = [m for m in members if m.get("email")]
    members_without_email = [m for m in members if not m.get("email")]

    if request.method == "POST":
        base_url = request.build_absolute_uri("/").rstrip("/")
        # Open the session if still draft
        if session.status == VotingSession.Status.DRAFT:
            session.status = VotingSession.Status.OPEN
            session.eligible_member_count = len(members)
            session.save(update_fields=["status", "eligible_member_count"])
            airtable_sync.sync_session_to_airtable(
                session_id=session.pk,
                name=session.name,
                open_date=session.open_date,
                close_date=session.close_date,
                status=session.status,
                eligible_member_count=session.eligible_member_count,
                airtable_record_id=session.airtable_record_id,
            )

        result = vote_emails.send_voting_emails(
            members=members_with_email,
            session_id=session.pk,
            session_name=session.name,
            close_date=session.close_date,
            base_url=base_url,
        )
        messages.success(request, f"Sent {result['sent_count']} emails.")
        for err in result.get("errors", []):
            messages.warning(request, err)
        return redirect("voting_dashboard")

    return render(
        request,
        "membership/voting/admin/email_preview.html",
        {
            "session": session,
            "members_with_email": members_with_email,
            "members_without_email": members_without_email,
        },
    )


@staff_member_required
def voting_calculate(request: HttpRequest, session_id: int) -> HttpResponse:
    """Calculate and save results for a session."""
    session = get_object_or_404(VotingSession, pk=session_id)

    # Build vote data from Django DB
    votes_qs = GuildVote.objects.filter(session=session).select_related("guild")
    # Group by member into vote dicts
    member_votes: dict[str, dict[str, str]] = {}
    for v in votes_qs:
        mid = v.member_airtable_id
        if mid not in member_votes:
            member_votes[mid] = {}
        rank_key = {1: "guild_1st", 2: "guild_2nd", 3: "guild_3rd"}.get(v.priority)
        if rank_key:
            member_votes[mid][rank_key] = v.guild.name

    vote_list = list(member_votes.values())

    results_data = vote_calculator.calculate_results(
        votes=vote_list,
        eligible_member_count=session.eligible_member_count,
    )

    if request.method == "POST":
        session.status = VotingSession.Status.CALCULATED
        session.votes_cast = results_data["votes_cast"]
        session.results_summary = results_data
        session.save(update_fields=["status", "votes_cast", "results_summary"])

        # Sync to Airtable
        airtable_sync.sync_session_to_airtable(
            session_id=session.pk,
            name=session.name,
            open_date=session.open_date,
            close_date=session.close_date,
            status=session.status,
            eligible_member_count=session.eligible_member_count,
            votes_cast=session.votes_cast,
            results_summary=vote_calculator.results_to_json(results_data),
            airtable_record_id=session.airtable_record_id,
        )

        messages.success(request, "Results calculated and saved.")
        return redirect("voting_dashboard")

    return render(
        request,
        "membership/voting/admin/calculate.html",
        {"session": session, "results": results_data},
    )


@staff_member_required
def voting_email_results(request: HttpRequest, session_id: int) -> HttpResponse:
    """Email results to leadership."""
    session = get_object_or_404(VotingSession, pk=session_id)

    if not session.results_summary:
        messages.error(request, "Calculate results before emailing them.")
        return redirect("voting_dashboard")

    results_data = session.results_summary

    if request.method == "POST":
        recipient_list = [r.strip() for r in request.POST.get("recipients", "").split(",") if r.strip()]
        if not recipient_list:
            messages.error(request, "Enter at least one recipient email.")
        else:
            try:
                vote_emails.send_results_email(recipient_list, session.name, results_data)
                messages.success(request, f"Results emailed to {', '.join(recipient_list)}.")
            except Exception as e:
                messages.error(request, f"Failed to send: {e}")
        return redirect("voting_dashboard")

    return render(
        request,
        "membership/voting/admin/email_results.html",
        {"session": session, "results": results_data},
    )


@staff_member_required
def voting_set_status(request: HttpRequest, session_id: int) -> HttpResponse:
    """Change a voting session's status."""
    if request.method != "POST":
        return redirect("voting_dashboard")

    new_status = request.POST.get("status", "")
    session = get_object_or_404(VotingSession, pk=session_id)

    if not session.can_transition_to(new_status):
        messages.error(request, f"Cannot change status from {session.get_status_display()} to {new_status}.")
        return redirect("voting_dashboard")

    session.status = new_status
    if new_status == VotingSession.Status.OPEN and session.eligible_member_count == 0:
        try:
            members = airtable_sync.get_eligible_members()
            session.eligible_member_count = len(members)
        except Exception:
            pass
    session.save(update_fields=["status", "eligible_member_count"])

    # Sync to Airtable
    airtable_sync.sync_session_to_airtable(
        session_id=session.pk,
        name=session.name,
        open_date=session.open_date,
        close_date=session.close_date,
        status=session.status,
        eligible_member_count=session.eligible_member_count,
        votes_cast=session.votes_cast,
        airtable_record_id=session.airtable_record_id,
    )

    messages.success(request, f"Session '{session.name}' is now {session.get_status_display()}.")
    return redirect("voting_dashboard")
