"""The Leadership Directory admin at /manage/leadership/ (#476; tabs and auto save, #564).

The editor page, then one small endpoint per object, each ``@fog_admin_required`` and
``@require_POST``. A typed field posts ``field`` and ``value`` and gets the saved row back as
JSON, which the editor keeps as that field's last good value; a refused value answers 422
with the field's errors and an error toast, and the editor puts the field back (the meeting
workspace contract). A new role line's first save answers with the line's own URLs, so its
next save updates it rather than adding it again. A tab, card or line that is gone answers
404, and so does a line on a card another window took off its tab. An order that does not
name exactly what is there now answers 409, and the editor reloads. The two modals (Add a Tab, Add a Person) and the Delete tab confirm are plain POSTs that
redirect back with a message, which the hub shows as a toast.
"""

from __future__ import annotations

from django.contrib import messages
from django.forms import BaseForm
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from hub.forms import (
    LeadershipAddForm,
    LeadershipAutosaveForm,
    LeadershipEditor,
    LeadershipPageForm,
    LeadershipRoleForm,
    LeadershipTabAddForm,
    LeadershipTabForm,
)
from hub.toast import trigger_toast
from hub.view_as import fog_admin_required
from hub.views import _get_hub_context
from membership.models import (
    LeadershipListing,
    LeadershipPage,
    LeadershipRole,
    LeadershipOrderStaleError,
    LeadershipTab,
)

# --- Helpers (all above the views: a def between a decorator and its view would be decorated) ---


def _editor_url(tab: LeadershipTab) -> str:
    """The editor opened on ``tab``."""
    return f"{reverse('hub_admin_leadership')}?tab={tab.pk}"


def _render_editor(
    request: HttpRequest,
    *,
    add_form: LeadershipAddForm | None = None,
    tab_add_form: LeadershipTabAddForm | None = None,
) -> HttpResponse:
    """Render the editor, with a refused modal form re-rendering its errors when one is given."""
    editor = LeadershipEditor(
        requested=request.GET.get("tab"),
        add_member=request.GET.get("add"),
        add_form=add_form,
        tab_add_form=tab_add_form,
    )
    return render(request, "hub/admin/leadership.html", {**_get_hub_context(request), "editor": editor})


def _refused(form: BaseForm) -> JsonResponse:
    """422 with every field's errors as JSON and the first as an error toast; the editor reverts the field."""
    errors = {name: [str(message) for message in field_errors] for name, field_errors in form.errors.items()}
    first = next(iter(errors.values()))[0]
    response = JsonResponse({"errors": errors}, status=422)
    trigger_toast(response, f"Couldn't save that. {first}", "error")
    return response


def _autosave(
    request: HttpRequest,
    form_class: type[LeadershipAutosaveForm],
    instance: LeadershipPage | LeadershipTab | LeadershipRole,
) -> HttpResponse:
    """Save the one posted field: the saved row as JSON, 400 for a field the form does not edit, 422 if refused."""
    if "field" not in request.POST or "value" not in request.POST:
        return HttpResponseBadRequest("Post a field and a value.")
    form = form_class.for_field(request.POST["field"], request.POST["value"], instance)
    if form is None:
        return HttpResponseBadRequest("Unknown field.")
    if not form.is_valid():
        return _refused(form)
    form.save()
    return JsonResponse(form.editor_row())


def _posted_ids(request: HttpRequest) -> list[int] | None:
    """The posted ``order`` as ids, as posted (a repeat is the model's to refuse); None when one is not a number.

    ``isdecimal``, not ``isdigit``: "²" is a digit that ``int`` cannot read.
    """
    raw = request.POST.getlist("order")
    if not all(value.isdecimal() for value in raw):
        return None
    return [int(value) for value in raw]


def _stale_order(exc: LeadershipOrderStaleError) -> HttpResponse:
    """409 with an error toast: the list changed in another window, and the editor reloads to show it."""
    response = HttpResponse(str(exc), status=409)
    trigger_toast(response, "The list changed in another window. Reloading to show the latest.", "error")
    return response


# --- The page ---


@fog_admin_required
@require_GET
def hub_admin_leadership(request: HttpRequest) -> HttpResponse:
    """The Leadership Directory editor: the page wording, then a pane per tab, every edit saved as it happens.

    ``?tab=<id>`` opens a tab (the public page's Edit this page button and the member edit
    page link there); ``?add=<member id>`` opens Add a person with that member chosen.
    First it settles any card the release before tabs wrote with no tab (an admin path only,
    never the public page).
    """
    LeadershipListing.objects.adopt_untabbed()
    return _render_editor(request)


@fog_admin_required
@require_POST
def admin_leadership_page_save(request: HttpRequest) -> HttpResponse:
    """Save the page title or the lead line above the tabs."""
    return _autosave(request, LeadershipPageForm, LeadershipPage.load())


# --- Tabs ---


@fog_admin_required
@require_POST
def admin_leadership_tab_add(request: HttpRequest) -> HttpResponse:
    """Add a Tab: a new People tab, last, and the editor opened on it."""
    form = LeadershipTabAddForm(request.POST, prefix="newtab")
    if not form.is_valid():
        messages.error(request, "Couldn't add that tab. Check the highlighted fields.")
        return _render_editor(request, tab_add_form=form)
    tab = form.create()
    messages.success(request, f"Added the {tab.title} tab.")
    return redirect(_editor_url(tab))


@fog_admin_required
@require_POST
def admin_leadership_tab_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save a tab's title or intro; Guild Leads takes both too."""
    return _autosave(request, LeadershipTabForm, get_object_or_404(LeadershipTab, pk=pk))


@fog_admin_required
@require_POST
def admin_leadership_tab_order(request: HttpRequest) -> HttpResponse:
    """Save the strip's order after a move left or right: ``order`` is every tab id, first to last, once each."""
    ids = _posted_ids(request)
    if ids is None:
        return HttpResponseBadRequest("Post the order as tab ids.")
    try:
        LeadershipTab.objects.reorder(ids)
    except LeadershipOrderStaleError as exc:
        return _stale_order(exc)
    return JsonResponse({"order": ids})


@fog_admin_required
@require_POST
def admin_leadership_tab_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a People tab and its cards; the same people's cards on other tabs stay. Guild Leads is a 404."""
    tab = get_object_or_404(LeadershipTab.objects.people(), pk=pk)
    tab.delete()
    messages.success(request, f"Deleted the {tab.title} tab.")
    return redirect("hub_admin_leadership")


# --- People on a tab ---


@fog_admin_required
@require_POST
def admin_leadership_person_add(request: HttpRequest, pk: int) -> HttpResponse:
    """Add a Person to a People tab, last, with their first line; someone taken off it earlier is relisted."""
    tab = get_object_or_404(LeadershipTab.objects.people(), pk=pk)
    form = LeadershipAddForm(request.POST, tab=tab)
    if not form.is_valid():
        messages.error(request, "Couldn't add that person. Check the highlighted fields.")
        return _render_editor(request, add_form=form)
    listing = form.save()
    messages.success(request, f"Added {listing.member.display_name} to {tab.title}.")
    return redirect(_editor_url(tab))


@fog_admin_required
@require_POST
def admin_leadership_people_order(request: HttpRequest, pk: int) -> HttpResponse:
    """Save a People tab's card order after a drop or an arrow: ``order`` is every card on show, once each."""
    tab = get_object_or_404(LeadershipTab.objects.people(), pk=pk)
    ids = _posted_ids(request)
    if ids is None:
        return HttpResponseBadRequest("Post the order as card ids.")
    try:
        tab.reorder_listings(ids)
    except LeadershipOrderStaleError as exc:
        return _stale_order(exc)
    return JsonResponse({"order": ids})


@fog_admin_required
@require_POST
def admin_leadership_person_remove(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove from tab: hide the card and keep its lines, so adding the member back restores them."""
    get_object_or_404(LeadershipListing, pk=pk).remove_from_tab()
    return HttpResponse(status=204)


# --- Role lines ---


@fog_admin_required
@require_POST
def admin_leadership_role_add(request: HttpRequest, pk: int) -> HttpResponse:
    """A new role line's first save: add it under the card's last line and answer with its id and URLs.

    A card another window took off its tab is a 404, so the editor drops it rather than
    saying Saved for a line nobody can see.
    """
    listing = get_object_or_404(LeadershipListing, pk=pk, is_listed=True)
    form = LeadershipRoleForm(request.POST)
    if not form.is_valid():
        return _refused(form)
    form.create_on(listing)
    return JsonResponse(form.editor_row())


@fog_admin_required
@require_POST
def admin_leadership_role_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save a role line's title or email; a line on a card another window took off its tab is a 404."""
    return _autosave(request, LeadershipRoleForm, get_object_or_404(LeadershipRole, pk=pk, listing__is_listed=True))


@fog_admin_required
@require_POST
def admin_leadership_role_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove a role line; a line on a card another window took off its tab is a 404 and stays kept."""
    get_object_or_404(LeadershipRole, pk=pk, listing__is_listed=True).delete()
    return HttpResponse(status=204)
