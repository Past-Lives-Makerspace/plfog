"""Member polls (#708 part 2): vote once from any page, and the Past polls page at /polls/.

The vote action answers an HTMX post with the poll's card (results, the member's answer
marked) so it swaps in place wherever the card sits, /polls/ today and the Spotlight in #709;
a plain post redirects back to ``next``. Guests (#691) and former members cannot vote or open
/polls/. Nothing here shows who voted for what, and nothing notifies anyone.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from hub.toast import trigger_toast
from hub.views import _get_hub_context, _get_member
from polls.models import Poll, PollCard, PollChoice, VoteRefusedError, can_vote

#: Polls per page on /polls/.
PER_PAGE = 10


def _back(request: HttpRequest) -> str:
    """Where a plain (non-HTMX) vote lands: ``next`` when it is ours, else /polls/."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return reverse("polls:index")


@login_required
def polls_index(request: HttpRequest) -> HttpResponse:
    """Every poll, newest first, each with its results; the open one offers its answers until voted."""
    member = _get_member(request)
    if not can_vote(member):
        raise PermissionDenied("Polls are for members.")
    page = Paginator(Poll.objects.all(), PER_PAGE).get_page(request.GET.get("page"))
    cards = PollCard.for_polls(list(page.object_list), member, timezone.now())
    return render(
        request,
        "polls/index.html",
        {**_get_hub_context(request), "page": page, "cards": cards, "next_url": request.get_full_path()},
    )


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
    response = render(
        request,
        "polls/partials/_poll_card.html",
        {"card": PollCard.for_poll(poll, member, now), "next_url": _back(request)},
    )
    trigger_toast(response, refusal or "Thanks, your vote is in.", "error" if refusal else "success")
    return response
