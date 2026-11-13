"""The Get Set Up card's buttons (#1167): Got It, Skip This Step, Hide and Show the Get Set Up Card.

Each is a small POST on the member's own :class:`~membership.models.PermissionSetup` row, for one
permission they hold (a 404 for anyone else, the same as an unknown permission), and lands back
on the page it came from (``next``), else the permission's own page. A Got It that finishes setup
gives the badge and shows its toast there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from membership.permission_setup import SetupState

if TYPE_CHECKING:
    from django.http import HttpRequest


def _state(request: HttpRequest, permission: str) -> SetupState:
    """The viewer's card for ``permission``; 404 unless they hold it and it has a card."""
    member = getattr(request.user, "member", None)
    state = SetupState.load(member, permission) if member is not None else None
    if state is None:
        raise Http404("No Get Set Up card for this permission.")
    return state


def _back(request: HttpRequest, state: SetupState) -> HttpResponse:
    """Back to ``next`` when it is this site's, else the permission's page; a finish toasts there."""
    if state.just_finished:
        messages.success(request, state.spec.celebration)
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(state.spec.page_url_name)


@login_required
@require_POST
def permission_setup_got_it(request: HttpRequest, permission: str) -> HttpResponse:
    """Got It on a reading step (``step``); the card opens the next step."""
    state = _state(request, permission)
    key = request.POST.get("step", "")
    if not any(view.step.key == key and view.step.got_it for view in state.steps):
        return HttpResponseBadRequest("Unknown reading step.")
    state.row.mark_read(key)
    return _back(request, _state(request, permission))


@login_required
@require_POST
def permission_setup_skip(request: HttpRequest, permission: str) -> HttpResponse:
    """Skip This Step on an optional step (``step``)."""
    state = _state(request, permission)
    key = request.POST.get("step", "")
    if not any(view.step.key == key and view.step.optional for view in state.steps):
        return HttpResponseBadRequest("Unknown optional step.")
    state.row.skip(key)
    return _back(request, _state(request, permission))


@login_required
@require_POST
def permission_setup_hide(request: HttpRequest, permission: str) -> HttpResponse:
    """Hide the card for now; the Guide tab brings it back."""
    state = _state(request, permission)
    state.row.hide()
    messages.info(request, "Get Set Up is hidden. Bring it back from the Guide tab.")
    return _back(request, state)


@login_required
@require_POST
def permission_setup_show(request: HttpRequest, permission: str) -> HttpResponse:
    """Show the Get Set Up Card again, from the Guide tab."""
    state = _state(request, permission)
    state.row.show()
    return _back(request, state)
