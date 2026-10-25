"""Polls (#708, back in #925): vote from Home, browse Past Polls, and Admin Tools > Polls.

The vote action answers an HTMX post with the poll's card (results, the member's answer
marked) so it swaps in place on Home; a plain post redirects back to ``next``. Guests (#691)
and former members cannot vote or open Past Polls. The admin views post a poll, close the
open one and list past polls with their totals. Nothing here shows who voted for what, and
nothing notifies anyone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from classes.table import prepare_table
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context, _get_member
from polls.forms import NewPollForm
from polls.models import (
    ANSWER_MAX_LENGTH,
    MAX_CHOICES,
    Poll,
    PollAlreadyOpenError,
    PollCard,
    PollChoice,
    VoteRefusedError,
    can_vote,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

#: Polls per page on Past Polls.
PER_PAGE = 10
#: The card copies a vote can come from: Past Polls ("") and Home's Poll card ("home").
VARIANTS = frozenset({"", "home"})
#: The admin Past Polls table's sortable columns.
_SORTABLE = frozenset({"question", "opens_at", "closes_at", "total_votes", "top_answer"})


def _back(request: HttpRequest) -> str:
    """Where a plain (non-HTMX) vote lands: ``next`` when it is ours, else Home."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return reverse("hub_home")


@login_required
def polls_index(request: HttpRequest) -> HttpResponse:
    """Past Polls: every closed poll, newest first, each with its counts and the member's own answer."""
    member = _get_member(request)
    if not can_vote(member):
        raise PermissionDenied("Polls are for members.")
    now = timezone.now()
    page = Paginator(Poll.objects.closed_at(now), PER_PAGE).get_page(request.GET.get("page"))
    cards = PollCard.for_polls(list(page.object_list), member, now)
    return render(request, "polls/index.html", {**_get_hub_context(request), "page": page, "cards": cards})


@login_required
@require_POST
def poll_vote(request: HttpRequest, pk: int) -> HttpResponse:
    """Cast this member's one vote, then show the results in place (HTMX) or go back (plain post)."""
    poll = get_object_or_404(Poll, pk=pk)
    member = _get_member(request)
    choice = request.POST.get("choice", "")
    if not choice.isdigit():
        return HttpResponseBadRequest("Pick an answer.")
    now = timezone.now()
    refusal = ""
    try:
        poll.vote(member=member, choice_pk=int(choice), now=now)
    except PollChoice.DoesNotExist:
        return HttpResponseBadRequest("That answer is not part of this poll.")
    except VoteRefusedError as error:
        refusal = str(error)
    if not request.headers.get("HX-Request"):
        if refusal:
            messages.error(request, refusal)
        else:
            messages.success(request, "Thanks, your vote is in.")
        return HttpResponseRedirect(_back(request))
    variant = request.POST.get("variant", "")
    response = render(
        request,
        "polls/partials/_poll_card.html",
        {
            "card": PollCard.for_poll(poll, member, now),
            "next_url": _back(request),
            "variant": variant if variant in VARIANTS else "",
        },
    )
    trigger_toast(response, refusal or "Thanks, your vote is in.", "error" if refusal else "success")
    return response


def _render_admin(request: HttpRequest, *, form: NewPollForm | None = None) -> HttpResponse:
    """Admin Tools > Polls: the open poll, the New Poll form and the Past Polls table.

    An invalid or refused post renders here at 200 with its errors: a boosted 4xx becomes a
    fresh GET (``hub_boot.js``) and would drop what was typed.
    """
    now = timezone.now()
    open_poll = Poll.objects.open_poll(now)
    table = prepare_table(
        request,
        Poll.objects.closed_at(now).with_totals(),
        search_fields=[],
        default_sort="opens_at",
        default_dir="desc",
        sortable=_SORTABLE,
    )
    context: dict[str, Any] = {
        **_get_hub_context(request),
        "open_poll": open_poll,
        "open_results": open_poll.results() if open_poll is not None else [],
        "poll_form": form or NewPollForm(),
        "max_choices": MAX_CHOICES,
        "answer_max_length": ANSWER_MAX_LENGTH,
        "page": table["page"],
        "sort": table["sort"],
        "sort_dir": table["sort_dir"],
        "base_params": table["base_params"],
    }
    return render(request, "polls/admin.html", context)


@fog_admin_required
def polls_admin(request: HttpRequest) -> HttpResponse:
    """Admin Tools > Polls (admin only)."""
    return _render_admin(request)


@fog_admin_required
@require_POST
def polls_admin_new(request: HttpRequest) -> HttpResponse:
    """Open a new poll now; refused with a message while one is open."""
    form = NewPollForm(request.POST)
    if not form.is_valid():
        return _render_admin(request, form=form)
    user: User = request.user  # type: ignore[assignment]  # @fog_admin_required guarantees a User
    try:
        form.post(by=user, now=timezone.now())
    except PollAlreadyOpenError:
        form.add_error(None, "A poll is already open. Close it first, then post this one.")
        return _render_admin(request, form=form)
    messages.success(request, "Poll posted. It is open now.")
    return redirect("polls:admin")


@fog_admin_required
@require_POST
def polls_admin_close(request: HttpRequest, pk: int) -> HttpResponse:
    """Close now: end voting on this poll at once."""
    poll = get_object_or_404(Poll, pk=pk)
    poll.close(timezone.now())
    messages.success(request, "Poll closed.")
    return redirect("polls:admin")


@fog_admin_required
def polls_admin_poll(request: HttpRequest, pk: int) -> HttpResponse:
    """One poll's results: counts and percents only, never who voted."""
    poll = get_object_or_404(Poll.objects.with_totals(), pk=pk)
    return render(
        request,
        "polls/admin_poll.html",
        {**_get_hub_context(request), "poll": poll, "results": poll.results(), "is_open": poll.is_open(timezone.now())},
    )
