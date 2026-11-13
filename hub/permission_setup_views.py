"""The Get Set Up card's buttons (#1167): Got It, Skip This Step, Hide and Show the Get Set Up Card.

Also what a page needs to host the cards (:func:`page_setups`) and the shared Guide page
(:func:`permission_guide`) for a permission whose page has no Guide tab of its own.

Each is a small POST on the member's own :class:`~membership.models.PermissionSetup` row, for one
permission they hold (a 404 for anyone else, the same as an unknown permission), and lands back
on the page it came from (``next``), else the permission's own page. A Got It that finishes setup
gives the badge and shows its toast there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from hub.view_as import ROLE_ADMIN
from membership.models import WikiPage
from membership.permission_setup import SetupState, spec_for
from membership.permissions import can_edit_wiki_page

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
    return redirect(state.page_url)


def page_setups(request: HttpRequest, *permissions: str) -> dict[str, Any]:
    """Context for a page hosting the Get Set Up cards of ``permissions``: those the viewer holds.

    ``hub/partials/_permission_setups.html`` draws them. A load that finishes setup, or first sees
    it finished, toasts the badge here.
    """
    member = getattr(request.user, "member", None) if request.user.is_authenticated else None
    states = SetupState.for_page(member, permissions)
    for state in states:
        if state.just_finished:
            messages.success(request, state.spec.celebration)
    return {"setups": states, "setup_next": request.get_full_path()}


@login_required
def permission_guide(request: HttpRequest, permission: str) -> HttpResponse:
    """The Guide page (#1167): the wiki page an admin linked to ``permission``, and a hidden card's way back.

    For the holder and for admins (404 for anyone else). A permission whose page has a Guide tab of
    its own (Tour Guide) goes there.
    """
    spec = spec_for(permission)
    if spec is None:
        raise Http404("No guide for this permission.")
    if spec.guide_url_name:
        return redirect(spec.guide_url)
    member = getattr(request.user, "member", None)
    state = SetupState.load(member, permission) if member is not None else None
    view_as = getattr(request, "view_as", None)
    is_admin = view_as is not None and view_as.has_actual(ROLE_ADMIN)
    if state is None and not is_admin:
        raise Http404("No guide for this permission.")
    if state is not None and state.just_finished:
        messages.success(request, spec.celebration)
    page = WikiPage.guide_page_for(permission)
    if page is not None:
        page = WikiPage.objects.select_related("updated_by").prefetch_related("facts").get(pk=page.pk)
    return render(
        request,
        "hub/permission_guide.html",
        {
            "spec": spec,
            "is_admin": is_admin,
            "page": page,
            "facts": list(page.facts.all()) if page is not None else [],
            "toc": page.toc() if page is not None and page.has_written_body else [],
            "can_edit": page is not None and can_edit_wiki_page(request, page),
            "setup": state,
            "back_url": state.page_url if state is not None else spec.page_url,
            "tab": "guide",
        },
    )


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
    messages.info(request, state.spec.hidden_message)
    return _back(request, state)


@login_required
@require_POST
def permission_setup_show(request: HttpRequest, permission: str) -> HttpResponse:
    """Show the Get Set Up Card again, from the Guide tab."""
    state = _state(request, permission)
    state.row.show()
    return _back(request, state)
