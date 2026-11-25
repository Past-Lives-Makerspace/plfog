"""My Hours (#1300, part 3): one person's week and hours, read only.

Reached by URL alone until part 5 wires the places hours are set today; there is no sidebar link
(Felix, 2026-11-23). Gated by :func:`hub.view_as.hours_access_required`. Whose hours a request
reads is decided once, in :func:`_target`: a person their own, an admin anyone who has a My Hours
page or has hours (``?member=``).
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from hours.page import MyHours, hours_people_q
from hub.calendar_pages import calendar_nav_params
from hub.view_as import ViewAs, hours_access_required
from membership.models import Member


def _people() -> Any:
    """Everyone an admin may pick: who has a My Hours page, and anyone who still has hours."""
    return Member.objects.filter(hours_people_q() | Q(hours__isnull=False)).distinct()


def _target(request: HttpRequest, viewer: Member, *, is_admin: bool) -> Member:
    """The person whose hours this request reads: the viewer, or an admin's pick (403 for anyone else)."""
    raw = request.GET.get("member", "")
    if not raw or raw == str(viewer.pk):
        return viewer
    if not is_admin:
        raise PermissionDenied("Only an admin can see someone else's hours.")
    if not raw.isdigit():
        raise Http404("Unknown member.")
    return get_object_or_404(_people(), pk=int(raw))


@hours_access_required
def my_hours(request: HttpRequest) -> HttpResponse:
    """My Hours: the week, Every Week, Available On, Days Off, Google Calendar and, for a guide, Booking Notice.

    ``?member=<pk>`` is an admin's pick of whose hours; ``?week_offset=<n>`` moves the week.
    """
    viewer: Member | None = getattr(request.user, "member", None)
    if viewer is None:
        raise Http404("This account has no member record.")
    is_admin = ViewAs.for_request(request).actual_is_admin
    target = _target(request, viewer, is_admin=is_admin)
    week_offset, _, _ = calendar_nav_params(request)
    page = MyHours.for_member(target, week_offset=week_offset)
    base = reverse("hours:my_hours")
    who = "" if target.pk == viewer.pk else f"member={target.pk}&"
    pickable = sorted(_people().exclude(pk=viewer.pk), key=lambda m: m.staff_label.casefold()) if is_admin else []
    return render(
        request,
        "hours/my_hours.html",
        {
            "page": page,
            "target": target,
            "is_self": target.pk == viewer.pk,
            "is_admin": is_admin,
            "picker": pickable,
            "prev_week_url": f"{base}?{who}week_offset={week_offset - 1}",
            "next_week_url": f"{base}?{who}week_offset={week_offset + 1}",
        },
    )
