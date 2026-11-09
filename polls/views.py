"""Polls (#708, back in #925): vote, browse Past Polls, Admin Tools > Polls, and a guild's polls.

The vote action answers an HTMX post with the poll's card (results, the member's answer
marked) so it swaps in place on Home or a guild's Polls tab; a plain post redirects back to
``next``. Guests (#691) and former members cannot vote or open Past Polls. The admin views post
a site wide poll, close the open one and list past site wide polls with their totals. The guild
views post and close a guild's poll, for whoever ``membership.permissions.can_edit_guild``
allows. Nothing here shows who voted for what, and nothing notifies anyone.

Email polls (#1142): the answer page by its plain link (signed in) or by a member's personal
email link (no sign in; the token names who answers), and the Polls page of every one sent to
the member. Results show only to those ``EmailPoll.may_see_results`` allows.
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
from membership.models import Guild
from membership.permissions import can_edit_guild
from polls.forms import EmailPollAnswerForm, NewPollForm, WriteInForm
from polls.models import (
    ANSWER_MAX_LENGTH,
    BAD_LINK,
    MAX_CHOICES,
    AnswerRefusedError,
    BadPollLinkError,
    EmailPoll,
    EmailPollPage,
    Poll,
    PollAlreadyOpenError,
    PollCard,
    PollChoice,
    VoteRefusedError,
    can_vote,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from membership.models import Member

#: Polls per page on Past Polls.
PER_PAGE = 10
#: The card copies a vote can come from: Past Polls (""), Home's Poll card ("home") and a guild's Polls tab ("guild").
VARIANTS = frozenset({"", "home", "guild"})
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
    page = Paginator(Poll.objects.closed_at(now).select_related("guild"), PER_PAGE).get_page(request.GET.get("page"))
    cards = PollCard.for_polls(list(page.object_list), member, now)
    return render(request, "polls/index.html", {**_get_hub_context(request), "page": page, "cards": cards})


@login_required
@require_POST
def poll_vote(request: HttpRequest, pk: int) -> HttpResponse:
    """Cast this member's one vote, then show the results in place (HTMX) or go back (plain post).

    A post carrying ``choice`` picks an answer; one without it is a written answer from the card's
    ``WriteInForm`` (#925 part 3), refused with its first error when the form is not valid.
    """
    poll = get_object_or_404(Poll, pk=pk)
    member = _get_member(request)
    now = timezone.now()
    refusal = ""
    answer: dict[str, Any] = {}
    if "choice" in request.POST:
        choice = request.POST["choice"]
        if not choice.isdigit():
            return HttpResponseBadRequest("Pick an answer.")
        answer = {"choice_pk": int(choice)}
    else:
        form = WriteInForm.for_poll(poll, request.POST)
        if form.is_valid():
            answer = {"write_in": form.cleaned_data["write_in"], "anonymous": form.is_anonymous}
        else:
            refusal = next(str(error) for errors in form.errors.values() for error in errors)
    if answer:
        try:
            poll.vote(member=member, now=now, **answer)
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
    variant = variant if variant in VARIANTS else ""
    response = render(
        request,
        "polls/partials/_poll_card.html",
        {
            "card": PollCard.for_poll(poll, member, now),
            "next_url": _back(request),
            "variant": variant,
            "hide_guild": variant == "guild",
        },
    )
    trigger_toast(response, refusal or "Thanks, your vote is in.", "error" if refusal else "success")
    return response


def _render_admin(request: HttpRequest, *, form: NewPollForm | None = None) -> HttpResponse:
    """Admin Tools > Polls: the open site wide poll, the New Poll form and the past site wide polls.

    An invalid or refused post renders here at 200 with its errors: a boosted 4xx becomes a
    fresh GET (``hub_boot.js``) and would drop what was typed.
    """
    now = timezone.now()
    open_poll = Poll.objects.site_wide().open_poll(now)
    table = prepare_table(
        request,
        Poll.objects.site_wide().closed_at(now).with_totals(),
        search_fields=[],
        default_sort="opens_at",
        default_dir="desc",
        sortable=_SORTABLE,
    )
    context: dict[str, Any] = {
        **_get_hub_context(request),
        "open_poll": open_poll,
        "open_results": open_poll.results() if open_poll is not None else [],
        "open_written": open_poll.written_answers() if open_poll is not None else [],
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
        {
            **_get_hub_context(request),
            "poll": poll,
            "results": poll.results(),
            "written": poll.written_answers(),
            "is_open": poll.is_open(timezone.now()),
        },
    )


def _guild_tab(guild: Guild) -> str:
    """The guild page opened on its Polls tab."""
    return f"{reverse('hub_guild_detail', args=[guild.slug])}?tab=polls"


@login_required
@require_POST
def guild_poll_new(request: HttpRequest, slug: str) -> HttpResponse:
    """Open a guild poll now (#925 AC 7, 8): this guild's leads, staff, officers and admins only.

    A refusal (a field is wrong, or the guild already has a poll open) comes back on the Polls
    tab as an error message; the browser's own checks on the form catch the common mistakes first.
    """
    guild = get_object_or_404(Guild, slug=slug)
    if not can_edit_guild(request, guild):
        raise PermissionDenied("Only this guild's leads and admins post its polls.")
    form = NewPollForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(str(error) for errors in form.errors.values() for error in errors))
        return redirect(_guild_tab(guild))
    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees a User
    try:
        form.post(by=user, now=timezone.now(), guild=guild)
    except PollAlreadyOpenError:
        messages.error(request, "This guild already has a poll open. Close it first, then post this one.")
        return redirect(_guild_tab(guild))
    messages.success(request, "Poll posted. It is open now.")
    return redirect(_guild_tab(guild))


@login_required
@require_POST
def guild_poll_close(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Close now on one of this guild's polls, for whoever may post them."""
    guild = get_object_or_404(Guild, slug=slug)
    if not can_edit_guild(request, guild):
        raise PermissionDenied("Only this guild's leads and admins close its polls.")
    poll = get_object_or_404(Poll.objects.for_guild(guild), pk=pk)
    poll.close(timezone.now())
    messages.success(request, "Poll closed.")
    return redirect(_guild_tab(guild))


# --- Email polls (#1142) ----------------------------------------------------------------------


@login_required
def email_poll(request: HttpRequest, pk: int) -> HttpResponse:
    """The plain link: answer, update and see results, signed in as yourself.

    Any signed in member may open it. On an audience only poll anyone it was not sent to (who is
    not its sender or an admin) sees "This poll was sent to a different group." and nothing more.
    A poll still in drafts is a 404.
    """
    poll = get_object_or_404(EmailPoll.objects.sent().select_related("draft"), pk=pk)
    member = _get_member(request)
    if member is None:
        raise PermissionDenied("Polls are for members.")
    return _email_poll_page(request, poll, member, here=poll.answer_path, is_sender=_sends_poll(request, member, poll))


def _sends_poll(request: HttpRequest, member: Member, poll: EmailPoll) -> bool:
    """Whether this request may open the poll's row on Email Members, so counts as one of its senders.

    The one rule Email Members uses (``hub.views._announcement_rows``): a guild's lead and staff,
    a class's instructors, whoever saved it, and admins, view-as aware.
    """
    from hub.views import _announcement_rows
    from membership.models import AnnouncementDraft

    return _announcement_rows(request, member, within=AnnouncementDraft.objects.filter(pk=poll.draft_id)).exists()


def email_poll_link(request: HttpRequest, token: str) -> HttpResponse:
    """The personal email link: answers as the member the token names, without signing in.

    It works while the poll is open. A tampered or unknown token, or one whose poll has closed,
    lands on a friendly page with a Sign in link, never an error page. GET only shows the page,
    so a mail client opening the link answers nothing.
    """
    try:
        poll, member = EmailPoll.read_answer_token(token, timezone.now())
    except BadPollLinkError as error:
        message = str(error)
        status = 400 if message == BAD_LINK else 200
        sign_in = f"{reverse('account_login')}?next={reverse('polls:mine')}"
        return render(
            request,
            "polls/ask_bad_link.html",
            {**_get_hub_context(request), "message": message, "is_closed": message != BAD_LINK, "sign_in_url": sign_in},
            status=status,
        )
    return _email_poll_page(request, poll, member, here=request.path)


def _email_poll_page(
    request: HttpRequest, poll: EmailPoll, member: Member, *, here: str, is_sender: bool = False
) -> HttpResponse:
    """Show the poll for ``member``, or save their answers on a POST and come back to ``here``.

    ``is_sender`` (the plain link only) shows a sender the results without their answering; the
    emailed link names a member, not a sender.
    """
    now = timezone.now()
    page = EmailPollPage.load(poll, member, now, is_sender=is_sender)
    questions = list(poll.questions.prefetch_related("options"))
    form = None
    if page.can_answer:
        if request.method == "POST":
            form = EmailPollAnswerForm(request.POST, questions=questions)
            if form.is_valid():
                try:
                    poll.answer(member=member, answers=form.answers, now=now)
                except AnswerRefusedError as error:
                    form.add_error(None, str(error))
                else:
                    messages.success(
                        request, "Your answers are updated." if page.has_answered else "Thanks, your answers are in."
                    )
                    return redirect(here)
        else:
            form = EmailPollAnswerForm(questions=questions, saved=poll.answers_of(member))
    return render(
        request,
        "polls/ask.html",
        {**_get_hub_context(request), "page": page, "poll": poll, "form": form, "questions": questions},
    )


@login_required
def my_email_polls(request: HttpRequest) -> HttpResponse:
    """The Polls page: every poll sent to this member, newest first, open or closed, with what they can do."""
    member = _get_member(request)
    if member is None:
        raise PermissionDenied("Polls are for members.")
    polls = list(EmailPoll.objects.listed_for(member))
    now = timezone.now()
    return render(
        request,
        "polls/mine.html",
        {**_get_hub_context(request), "rows": [(poll, poll.is_open(now)) for poll in polls]},
    )
