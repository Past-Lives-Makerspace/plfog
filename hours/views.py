"""My Hours (#1300, parts 3 and 4): one person's week and hours, and editing them.

Reached by URL alone until part 5 wires the places hours are set today; there is no sidebar link
(Felix, 2026-11-23). Gated by :func:`hub.view_as.hours_access_required`. Whose hours a request
reads or writes is decided once, in :func:`_target`: a person their own, an admin anyone who has a
My Hours page or has hours (``member``, from the query or the POST). Nobody else edits anyone's
hours, leads included (Felix, 2026-11-23).

Every Week and Available On save themselves as they are edited (#1279's idiom, ``hub.autosave``):
each is one formset form (``hours.forms``), checked and written under the person's lock. + Add
Dates posts once, all or nothing.
"""

from __future__ import annotations

from typing import Any

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hours.days_off import DayOffForm, remove_day_off, still_booked
from hours.edit import gives_tours, held_owners
from hours.follow import lock_person
from hours.forms import MAX_DATES_AHEAD, AddDatesForm, Context, HoursSetFormSet, hours_formset
from hours.models import DayOff, Hours
from hours.page import KIND_CLASSES, TOURS_KEY, MyHours, hours_people_q
from hub.autosave import autosave_refused, autosave_saved, wants_autosave
from hub.calendar_pages import calendar_nav_params
from hub.toast import trigger_toast
from hub.view_as import ViewAs, hours_access_required
from membership.models import Member
from tours import google
from tours.forms import TourCalendarChoiceForm, TourNoticeForm
from tours.models import FacilityTourSettings, TourCalendarConnection, TourGuideSettings


def _people() -> Any:
    """Everyone an admin may pick: who has a My Hours page, and anyone who still has hours."""
    return Member.objects.filter(hours_people_q() | Q(hours__isnull=False)).distinct()


def _viewer(request: HttpRequest) -> Member:
    viewer: Member | None = getattr(request.user, "member", None)
    if viewer is None:
        raise Http404("This account has no member record.")
    return viewer


def _target(request: HttpRequest, raw: str) -> Member:
    """The person whose hours this request reads or writes: the viewer, or an admin's pick (403 for anyone else)."""
    viewer = _viewer(request)
    if not raw or raw == str(viewer.pk):
        return viewer
    if not ViewAs.for_request(request).actual_is_admin:
        raise PermissionDenied("Only an admin can see or change someone else's hours.")
    if not raw.isdigit():
        raise Http404("Unknown member.")
    return get_object_or_404(_people(), pk=int(raw))


def _context(target: Member) -> Context:
    return Context(
        member=target, gives_tours=gives_tours(target), owners=held_owners(target), defaults=FacilityTourSettings.load()
    )


def _page_url(request: HttpRequest, target: Member) -> str:
    base = reverse("hours:my_hours")
    return base if target.pk == _viewer(request).pk else f"{base}?member={target.pk}"


def _kind_css(page: MyHours, context: Context) -> dict[str, str]:
    """Each kind's colour: the week's own, then the next in turn for a guild or tool not offered yet."""
    css = {kind.key: kind.css for kind in page.kinds}
    css.setdefault(TOURS_KEY, "pl-kind--tours")
    taken = len([key for key in css if key != TOURS_KEY])
    for owner in context.owners:
        if owner.key not in css:
            css[owner.key] = KIND_CLASSES[taken % len(KIND_CLASSES)]
            taken += 1
    return css


def _calendar(target: Member, form: AddDatesForm) -> dict[str, object]:
    """What the Add Dates calendar (``plTourDates``) draws: the pickable range, days with hours, days off, picks."""
    today = timezone.localdate()
    last = today + timedelta(days=MAX_DATES_AHEAD)
    dated = Hours.objects.filter(member=target, date__range=(today, last)).values_list("date", flat=True)
    off: set[date] = set()
    for day_off in DayOff.objects.filter(member=target, ends_on__gte=today, starts_on__lte=last):
        day = max(day_off.starts_on, today)
        while day <= min(day_off.ends_on, last):
            off.add(day)
            day += timedelta(days=1)
    return {
        "today": today.isoformat(),
        "last": last.isoformat(),
        "dated": sorted({day.isoformat() for day in dated if day is not None}),
        "off": sorted(day.isoformat() for day in off),
        "picked": form.picked_iso,
    }


@hours_access_required
def my_hours(request: HttpRequest) -> HttpResponse:
    """My Hours: the week, Every Week, Available On, Days Off, Google Calendar and, for a guide, Booking Notice.

    ``?member=<pk>`` is an admin's pick of whose hours; ``?week_offset=<n>`` moves the week.
    """
    target = _target(request, request.GET.get("member", ""))
    return _render(request, target)


def _render(
    request: HttpRequest,
    target: Member,
    *,
    weekly: HoursSetFormSet | None = None,
    dated: HoursSetFormSet | None = None,
    add_dates: AddDatesForm | None = None,
    refused: bool = False,
    day_off_form: DayOffForm | None = None,
) -> HttpResponse:
    viewer = _viewer(request)
    is_admin = ViewAs.for_request(request).actual_is_admin
    week_offset, _, _ = calendar_nav_params(request)
    page = MyHours.for_member(target, week_offset=week_offset)
    base = reverse("hours:my_hours")
    who = "" if target.pk == viewer.pk else f"member={target.pk}&"
    pickable = sorted(_people().exclude(pk=viewer.pk), key=lambda m: m.staff_label.casefold()) if is_admin else []
    context = _context(target)
    add_dates = add_dates or AddDatesForm(context=context)
    is_self = target.pk == viewer.pk
    connection = TourCalendarConnection.objects.filter(guide=target).first()
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
            "weekly_formset": weekly or hours_formset(context, dated=False),
            "dated_formset": dated or hours_formset(context, dated=True),
            "summaries": {str(row.hours.pk): row for row in [*page.weekly, *page.dated]},
            "kind_css": _kind_css(page, context),
            "add_dates": add_dates,
            "add_dates_open": add_dates.is_bound,
            "add_dates_calendar": _calendar(target, add_dates),
            "hours_refused": refused,
            "day_off_form": day_off_form or DayOffForm(member=target),
            "open_day_off": day_off_form is not None,
            "gcal_form": TourCalendarChoiceForm(connection=connection)
            if is_self and context.gives_tours and connection is not None and not connection.is_broken
            else None,
            "notice_form": TourNoticeForm(instance=TourGuideSettings.objects.filter(guide=target).first()),
            "gcal_configured": google.is_configured(),
        },
    )


@hours_access_required
@require_POST
def hours_save(request: HttpRequest) -> HttpResponse:
    """Save Every Week (``list=weekly``) or Available On (``list=dated``): every row that changed, in one POST.

    The posted ``member`` is checked before anything is read. Valid and invalid alike are checked
    and written under the person's lock. An autosave post answers JSON (each row's set id in posted
    order, rows deleted elsewhere under ``gone``, a 422 keyed by field otherwise); without the
    script, a flash and a redirect, or the page again with the refused rows open.
    """
    target = _target(request, request.POST.get("member", ""))
    dated = request.POST.get("list") == "dated"
    with transaction.atomic():
        lock_person(target.pk)
        formset = hours_formset(_context(target), dated=dated, data=request.POST)
        saved = formset.is_valid()
        if saved:
            formset.save()
    if wants_autosave(request):
        if not saved:
            return autosave_refused(formset)
        gone = formset.gone_rows
        prefix = "dated" if dated else "weekly"
        response = autosave_saved({prefix: formset.rows()}, gone={prefix: gone} if gone else None)
        if gone:
            trigger_toast(
                response, "Some of these hours were deleted elsewhere, so they are gone from this page.", "info"
            )
        return response
    if not saved:
        if dated:
            return _render(request, target, dated=formset, refused=True)
        return _render(request, target, weekly=formset, refused=True)
    messages.success(request, "Hours saved.")
    return redirect(_page_url(request, target))


@hours_access_required
@require_POST
def dates_add(request: HttpRequest) -> HttpResponse:
    """+ Add Dates: the same hours, open to the same kinds, on every date picked; all of them or none.

    Valid: "Added 3 dates." and back to the page. Invalid: the page again with the panel open, the
    picks and fields kept, and each problem named with its date.
    """
    target = _target(request, request.POST.get("member", ""))
    with transaction.atomic():
        lock_person(target.pk)
        form = AddDatesForm(request.POST, context=_context(target))
        saved = form.is_valid()
        added = len(form.save()) if saved else 0
    if not saved:
        return _render(request, target, add_dates=form)
    messages.success(request, f"Added {added} date{'' if added == 1 else 's'}.")
    return redirect(_page_url(request, target))


def _day_off_modal(request: HttpRequest, target: Member, form: DayOffForm) -> HttpResponse:
    return render(
        request,
        "hours/partials/_day_off_form.html",
        {"target": target, "is_self": target.pk == _viewer(request).pk, "day_off_form": form, **_booked(form)},
    )


def _booked(form: DayOffForm) -> dict[str, object]:
    """The still booked lines for the days the form names so far, and whether it is one day."""
    chosen = form.chosen()
    if chosen is None:
        return {"booked": None, "one_day": True}
    return {"booked": still_booked(form.member, *chosen), "one_day": chosen[0] == chosen[1]}


@hours_access_required
def day_off_preview(request: HttpRequest) -> HttpResponse:
    """GET ``member``, ``day_off-starts_on``, ``day_off-ends_on``: the still booked line, swapped into the open modal."""
    target = _target(request, request.GET.get("member", ""))
    form = DayOffForm(request.GET, member=target)
    return render(request, "hours/partials/_day_off_impact.html", _booked(form))


@hours_access_required
@require_POST
def day_off_save(request: HttpRequest) -> HttpResponse:
    """Add Day Off: one day or a run of days for every kind. Bookings already made stay booked; nothing is emailed.

    Valid: a flash, then ``HX-Redirect`` back to the page over HTMX (a plain redirect otherwise).
    Invalid: the modal body again with its errors over HTMX, or the page with the modal open.
    """
    target = _target(request, request.POST.get("member", ""))
    is_htmx = request.headers.get("HX-Request") == "true"
    with transaction.atomic():
        lock_person(target.pk)
        form = DayOffForm(request.POST, member=target)
        saved = form.is_valid()
        day_off = form.save() if saved else None
    if day_off is None:
        if is_htmx:
            return _day_off_modal(request, target, form)
        return _render(request, target, day_off_form=form)
    messages.success(request, f"Day off saved: {day_off.label}. Anything already booked stays booked.")
    url = _page_url(request, target)
    if is_htmx:
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


@hours_access_required
@require_POST
def day_off_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a day off (after the confirm): the person's own, or anyone's for an admin (403 otherwise)."""
    day_off = get_object_or_404(DayOff, pk=pk)
    target = _target(request, str(day_off.member_id))
    with transaction.atomic():
        lock_person(target.pk)
        remove_day_off(day_off)
    messages.success(request, "Day off deleted. Those days are offered again.")
    return redirect(_page_url(request, target))
