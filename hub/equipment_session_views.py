"""Sessions on a machine reservation (#1096): Start Session, Stop Session, the session page and its QR.

A member starts and stops a session on their own confirmed reservation of an hourly item whose
managers turned Sessions on. Every gate lives on the model (:meth:`EquipmentReservation.start_session`,
:meth:`EquipmentReservation.stop_session`), so these views only parse the request and answer. The
session page is what the machine's session QR opens: one stable URL per item, which finds the
scanning member's current reservation there itself, so a printed sheet never goes stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from hub.equipment_views import (
    _equipment_queryset,
    _parse_day,
    _parse_week_value,
    _render_schedule,
    _require_can_print,
    _require_visible,
)
from hub.toast import trigger_toast
from hub.views import _get_hub_context, _get_member, _safe_next
from membership.models import (
    Equipment,
    EquipmentCredit,
    EquipmentError,
    EquipmentReservation,
    Member,
    SessionStop,
    _clock,
    duration_label,
)

if TYPE_CHECKING:
    from datetime import datetime


def _stop_message(result: SessionStop) -> str:
    """What the member is told after Stop Session: the time, then the credit or why there is none."""
    when = _clock(result.stopped_at)
    if result.already_stopped:
        return f"Your session already stopped at {when}."
    if result.minutes:
        return f"Session stopped at {when}. {duration_label(result.minutes)} of credit added."
    return f"Session stopped at {when}. {result.note}"


def _answer(request: HttpRequest, equipment: Equipment, level: str, text: str) -> HttpResponse:
    """Answer a Start or Stop the way End Early does: with a posted ``next``, a message and that page;
    from the item page's schedule, the refreshed partial and a toast."""
    if "next" in request.POST:
        getattr(messages, level)(request, text)
        return redirect(_safe_next(request, reverse("hub_equipment_session", args=[equipment.slug])))
    response = _render_schedule(
        request,
        equipment,
        week_offset=_parse_week_value(request.POST.get("week", "0")),
        selected_day=_parse_day(request.POST.get("day", "")),
    )
    trigger_toast(response, text, level)
    return response


def _reservation_on(slug: str, pk: int) -> tuple[Equipment, EquipmentReservation]:
    """The item and the reservation on it, or a 404.

    Whose reservation it is, its status and the switch are the model's gates, so a crafted POST on
    someone else's booking is refused with its reason rather than hidden.
    """
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    return equipment, get_object_or_404(EquipmentReservation, pk=pk, equipment=equipment)


@login_required
@require_POST
def hub_equipment_session_start(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST: the member starts the session on their own reservation now (#1096). A repeat changes nothing."""
    equipment, reservation = _reservation_on(slug, pk)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    try:
        result = reservation.start_session(member)
    except EquipmentError as exc:
        return _answer(request, equipment, "error", str(exc))
    when = _clock(result.started_at)
    if result.already_started:
        return _answer(request, equipment, "info", f"Your session already started at {when}.")
    return _answer(request, equipment, "success", f"Session started at {when}.")


@login_required
@require_POST
def hub_equipment_session_stop(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """POST: the member stops their session now; the unused time becomes credit as End Early gives it (#1096)."""
    equipment, reservation = _reservation_on(slug, pk)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    try:
        result = reservation.stop_session(member)
    except EquipmentError as exc:
        return _answer(request, equipment, "error", str(exc))
    return _answer(request, equipment, "info" if result.already_stopped else "success", _stop_message(result))


@dataclass
class SessionPage:
    """What the session page shows one member on one item (#1096).

    ``state`` is one of: ``off`` (Sessions are not on here), ``start``, ``running``, ``stopped``
    (their session today is over), ``waiting`` (their current booking awaits approval or payment)
    and ``none`` (no booking here right now).
    """

    state: str
    reservation: EquipmentReservation | None = None
    #: Their next booking here that has not started, for the ``none`` and ``stopped`` states.
    upcoming: EquipmentReservation | None = None
    #: Minutes Stop Session would credit right now, on ``running``.
    stop_minutes: int = 0
    #: Their credit balance on this item.
    credit_minutes: int = 0

    @property
    def stop_hours(self) -> str:
        """The credit Stop would give, in words ("6 hours"), "" when it would give none."""
        return duration_label(self.stop_minutes) if self.stop_minutes else ""

    @property
    def credit_label(self) -> str:
        """The member's credit here in words, "" when they have none."""
        return duration_label(self.credit_minutes) if self.credit_minutes > 0 else ""


def session_page(equipment: Equipment, member: Member, now: datetime) -> SessionPage:
    """Decide what the session page shows ``member`` on ``equipment`` at ``now`` (#1096).

    Their booking in progress here comes first (confirmed: Start or Stop; awaiting approval or
    payment: why it cannot start); otherwise their session that stopped earlier today, so the page
    they just pressed Stop on still says what happened; otherwise nothing is current.
    """
    if not equipment.has_sessions:
        return SessionPage(state="off")
    credit = EquipmentCredit.objects.balance_for(member, equipment)
    rows = EquipmentReservation.objects.reservations().filter(member=member, equipment=equipment, is_season=False)
    # One at most: a member's held bookings on one item never overlap.
    current = rows.holding().filter(starts_at__lte=now, ends_at__gt=now).first()
    upcoming = rows.holding().filter(starts_at__gt=now).order_by("starts_at").first()
    if current is not None and current.status != EquipmentReservation.Status.CONFIRMED:
        return SessionPage(state="waiting", reservation=current, upcoming=upcoming, credit_minutes=credit)
    if current is not None and current.session_started_at is None:
        return SessionPage(state="start", reservation=current, upcoming=upcoming, credit_minutes=credit)
    if current is not None and current.session_stopped_at is None:
        stop_minutes = current.session_stop_minutes(now, credited_minutes=EquipmentCredit.objects.credited_on(current))
        return SessionPage(state="running", reservation=current, stop_minutes=stop_minutes, credit_minutes=credit)
    today = timezone.localdate(now)
    stopped = current or (
        rows.confirmed()
        .filter(session_started_at__isnull=False, session_started_at__date=today, ends_at__lte=now)
        .order_by("-session_started_at")
        .first()
    )
    if stopped is not None:
        return SessionPage(state="stopped", reservation=stopped, upcoming=upcoming, credit_minutes=credit)
    return SessionPage(state="none", upcoming=upcoming, credit_minutes=credit)


@login_required
def hub_equipment_session(request: HttpRequest, slug: str) -> HttpResponse:
    """The session page (#1096): what the machine's session QR opens, for the signed in member.

    Signed out, ``login_required`` sends them through sign in and back here. It shows Start Session
    or Stop Session on their current reservation on this item, what happened once they stopped,
    or, with nothing current, says so and links to the item's page to book.
    """
    equipment = get_object_or_404(_equipment_queryset().select_related("area"), slug=slug)
    _require_visible(request, equipment)
    member = _get_member(request)
    if member is None:
        raise Http404("No member profile.")
    page = session_page(equipment, member, timezone.now())
    return render(
        request,
        "hub/equipment_session.html",
        {
            **_get_hub_context(request),
            "equipment": equipment,
            "page": page,
            "session_url": reverse("hub_equipment_session", args=[equipment.slug]),
        },
    )


@login_required
def hub_equipment_session_qr(request: HttpRequest, slug: str, fmt: str) -> HttpResponse:
    """Download the session QR (#1096) as SVG or PNG, gated like the QR sheet; 404 while Sessions are off."""
    equipment = get_object_or_404(_equipment_queryset(), slug=slug)
    forbidden = _require_can_print(request, equipment)
    if forbidden is not None:
        return forbidden
    if not equipment.has_sessions:
        raise Http404("Sessions are not on for this equipment.")
    if fmt == "svg":
        response = HttpResponse(equipment.session_qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        response = HttpResponse(equipment.session_qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    response["Content-Disposition"] = f'attachment; filename="{equipment.slug}-session-qr.{fmt}"'
    return response
