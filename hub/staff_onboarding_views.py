"""The guild staff rules popup and the walkthrough buttons (#1076).

Two small POSTs, each recorded through ``membership.staff_onboarding``: the popup's confirm, and
Take the Tour or Exit on any walkthrough button. Take records the choice and starts the walkthrough
on the tool's own page (the button's ``start``) or else at its entry page
(``core.tours.entry_url_for``); Exit records it and returns the member where they were. The
popup and the onboarding button are drawn by ``hub/base.html`` from
``Member.staff_onboarding_step``, so both go away once the step moves on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.tours import TOURS, entry_url_for
from hub.forms import StaffRulesConfirmForm, WalkthroughChoiceForm
from membership import staff_onboarding

if TYPE_CHECKING:
    from django.http import HttpRequest


def _with_tour(path: str, tour_key: str) -> str:
    """``path`` with ``?tour=<key>`` set, keeping its other query (a Guild Settings ``tab``)."""
    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "tour"] + [("tour", tour_key)]
    return urlunsplit(("", "", parts.path, urlencode(query), ""))


def _back(request: HttpRequest) -> HttpResponse:
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("hub_home")


@login_required
@require_POST
def staff_rules_confirm(request: HttpRequest) -> HttpResponse:
    """The popup's I Understand: log that they read the rules, then back where they were.

    An unticked switch changes nothing and lands back on the same page, where the popup is still
    owed, so the only way past it is to turn the switch on.
    """
    member = getattr(request.user, "member", None)
    if member is not None and StaffRulesConfirmForm(request.POST).is_valid():
        staff_onboarding.confirm_rules(member)
    return _back(request)


@login_required
@require_POST
def walkthrough_choice(request: HttpRequest) -> HttpResponse:
    """Take the Tour or Exit on a walkthrough button: log it, then start the tour or go back.

    400 on an unknown tour or choice; 403 for a member the tour is not for (an ordinary member
    never sees these buttons, so a POST from one is not a click).
    """
    form = WalkthroughChoiceForm(request.POST)
    member = getattr(request.user, "member", None)
    if member is None or not form.is_valid():
        return HttpResponseBadRequest("Unknown walkthrough or choice.")
    tour = TOURS[form.cleaned_data["tour_key"]]
    if not tour.audience(member):
        return HttpResponseForbidden("This walkthrough is not for you.")
    taken = form.cleaned_data["choice"] == WalkthroughChoiceForm.Choice.TAKE
    staff_onboarding.record_tour_choice(member, tour.key, taken=taken)
    if taken:
        posted = request.POST.get("start", "")
        start = _with_tour(posted, tour.key)
        hosts = {request.get_host()}
        # The tool's own button: run the walkthrough right here, on this guild. Both the posted URL
        # and the one we would send must be ours: "http://<our host>//elsewhere" passes the first
        # check and keeps only the path "//elsewhere", which is another site.
        if posted and url_has_allowed_host_and_scheme(posted, hosts) and url_has_allowed_host_and_scheme(start, hosts):
            return redirect(start)
        return redirect(entry_url_for(tour, member))
    return _back(request)
