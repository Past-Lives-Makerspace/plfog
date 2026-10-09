"""Eventbrite API client + listing push (plfog → Eventbrite, one-way) for classes (#652).

Mirrors :mod:`core.integrations.discord_events`: build via :meth:`EventbriteClient.from_settings`,
check ``.enabled``, and **never raise to the caller**. An Eventbrite outage records a sync error
on the class and the plfog save proceeds regardless; ``retry_eventbrite_pushes`` tries again.

Four gates make a client ``enabled``: the admin toggle
(``SiteConfiguration.eventbrite_sync_enabled``), a private token, an organization ID and a venue
ID (``EVENTBRITE_*`` settings). Staging is always disabled, because its database is a copy of
production and names the same classes.

plfog owns the seat count: the ticket quantity is always set from ``spots_remaining`` and never
read back. Eventbrite refuses to unpublish a paid event that holds orders, so ending a listing
closes ticket sales first; the unpublish that follows may then be refused and that is recorded,
not retried.
"""

from __future__ import annotations

import logging
from datetime import UTC
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx
from django.conf import settings
from django.template.defaultfilters import linebreaks_filter, urlize
from django.utils import timezone
from django.utils.html import escape, strip_tags

from classes.eventbrite_categories import CLASS_FORMAT_ID

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from classes.models import ClassFaq, ClassOffering

logger = logging.getLogger(__name__)

API_BASE = "https://www.eventbriteapi.com/v3"
_TIMEOUT_SECONDS = 10.0
# The quantity push runs after commit inside a booking, a Stripe webhook or a refund request,
# so a slow Eventbrite must not hold that request for long; a miss is retried on the scheduler.
QUANTITY_PUSH_TIMEOUT_SECONDS = 3.0
_SYNC_ERROR_MAX = 500
_EVENT_TIMEZONE = "America/Los_Angeles"
_SUMMARY_MAX = 140
_DRAFT = "draft"  # the event status Eventbrite publishes from (others: live, started, ended, completed, canceled)
# Statuses where nothing is for sale; ``started`` is not one, the event is simply running.
_NOT_SELLING = frozenset({"ended", "completed", "canceled"})
# The listing's FAQ section reads back under two types, "faqs" and a "faq" copy Eventbrite makes
# itself. Only "faqs" may be written: a POST carrying "faq" is refused with a 400 FIELD_ERROR
# (tried live on event 2003170172911, 2026-10-08). Both are dropped from what was read.
_FAQ_WIDGET_TYPES = ("faqs", "faq")
_FAQ_WIDGET_WRITE_TYPE = "faqs"
# Eventbrite's US fees for a paid ticket (eventbrite.com/organizer/pricing, checked 2026-10-07).
SERVICE_FEE_PERCENT = 3.7
SERVICE_FEE_FIXED_CENTS = 179
PROCESSING_FEE_PERCENT = 2.9


class EventbriteError(Exception):
    """An Eventbrite API call failed; ``status`` is the HTTP status, or None for a transport error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def field(result: dict[str, Any], key: str) -> Any:
    """``result[key]``; a missing key is an :class:`EventbriteError`, so the class records it."""
    if key not in result:
        raise EventbriteError(f"Eventbrite's answer has no {key!r}: {result!r}"[:300])
    return result[key]


class EventbriteSync:
    """The copy recorded on a class when a push did not happen or ended short."""

    SYNC_OFF = "Eventbrite sync is off."
    STILL_UP = "Sales are closed. Eventbrite keeps the event page up while it holds orders."
    GALLERY_CHANGED = "The gallery changed."
    EDIT_SAVED = "your changes are saved and go to Eventbrite within 15 minutes"

    @staticmethod
    def photos_not_sent(reasons: list[str]) -> str:
        """The note a listed class carries when some gallery photos did not upload; the first reason shown."""
        count = len(reasons)
        return f"{count} photo{'' if count == 1 else 's'} could not be sent: {reasons[0]}"

    @staticmethod
    def not_selling(status: str) -> str:
        """The note a listed class carries when its Eventbrite event is over or canceled."""
        return f"The event is {status} on Eventbrite, so nothing is for sale."

    @staticmethod
    def left_out(parts: Sequence[str], reason: str) -> str:
        """The note a listed class carries when Eventbrite refused part of its page, naming every part left out."""
        return f"Eventbrite refused the page, so it went without {' and '.join(parts)}: {reason}"


def estimate_fee_cents(price_cents: int) -> int:
    """Eventbrite's fee on one ticket at ``price_cents``: the service fee plus processing."""
    percent = SERVICE_FEE_PERCENT + PROCESSING_FEE_PERCENT
    return round(price_cents * percent / 100) + SERVICE_FEE_FIXED_CENTS


class EventbriteClient:
    """Minimal Eventbrite v3 client. Disabled when the toggle is off or any credential is blank."""

    def __init__(self, *, token: str, organization_id: str, venue_id: str) -> None:
        self._token = token
        self.timeout = _TIMEOUT_SECONDS
        self.organization_id = organization_id
        self.venue_id = venue_id

    @classmethod
    def from_settings(cls) -> EventbriteClient:
        """Build a client from ``settings`` and ``SiteConfiguration``; never raises."""
        from core.models import SiteConfiguration

        if settings.IS_STAGING or not SiteConfiguration.load().eventbrite_sync_enabled:
            return cls(token="", organization_id="", venue_id="")
        return cls(
            token=settings.EVENTBRITE_PRIVATE_TOKEN,
            organization_id=settings.EVENTBRITE_ORGANIZATION_ID,
            venue_id=settings.EVENTBRITE_VENUE_ID,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.organization_id and self.venue_id)

    def create_event(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/organizations/{self.organization_id}/events/", json=body)

    def update_event(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/events/{event_id}/", json=body)

    def create_ticket_class(self, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/events/{event_id}/ticket_classes/", json=body)

    def update_ticket_class(self, event_id: str, ticket_class_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/events/{event_id}/ticket_classes/{ticket_class_id}/", json=body)

    def set_description(
        self,
        event_id: str,
        html: str,
        image_ids: Sequence[str] = (),
        faqs: Sequence[dict[str, str]] | None = None,
    ) -> None:
        """Publish ``html`` as the listing's text module, one image module per media ID, and the FAQ.

        Structured content is versioned and replaced whole, so every call sends the next version
        with everything the page should show. An ``<img>`` inside the text module is stripped by
        Eventbrite, which is why photos go as image modules of uploaded media. The widgets ride in
        the same version when ``faqs`` is a list: every widget read back goes out unchanged (the
        dashboard's photo carousel among them) except the FAQ ones, which ``faqs`` replaces (an
        empty list drops them). ``faqs=None`` sends no ``widgets`` key at all, the request verified
        before #716. A missing or null ``widgets`` in the edit payload means none to keep.
        """
        current = self._call("GET", f"/events/{event_id}/structured_content/edit/", params={"purpose": "listing"})
        try:
            version = int(field(current, "page_version_number")) + 1
        except (TypeError, ValueError) as exc:
            raise EventbriteError(f"Unreadable structured content version: {current!r}"[:300]) from exc
        text = {"type": "text", "data": {"body": {"type": "text", "text": html, "alignment": "left"}}}
        images = [{"type": "image", "data": {"image": {"type": "image", "image_id": i}}} for i in image_ids]
        body: dict[str, Any] = {"modules": [text, *images], "publish": True, "purpose": "listing"}
        if faqs is not None:
            widgets = [w for w in current.get("widgets") or [] if w.get("type") not in _FAQ_WIDGET_TYPES]
            if faqs:
                widgets.append({"id": "", "type": _FAQ_WIDGET_WRITE_TYPE, "data": {"faqs": list(faqs)}})
            body["widgets"] = widgets
        self._call("POST", f"/events/{event_id}/structured_content/{version}/", json=body)

    def upload_logo(self, filename: str, content: bytes) -> str:
        """Upload the event's main image and return its media ID."""
        return self._upload_media(filename, content, "image-event-logo")

    def upload_content_image(self, filename: str, content: bytes) -> str:
        """Upload a photo for an image module of the description and return its media ID."""
        return self._upload_media(filename, content, "image-structured-content")

    def _upload_media(self, filename: str, content: bytes, media_type: str) -> str:
        """Eventbrite's three step media upload: a ticket, the file to its bucket, then the finish."""
        ticket = self._call("GET", "/media/upload/", params={"type": media_type})
        files = {field(ticket, "file_parameter_name"): (filename, content)}
        url, data = field(ticket, "upload_url"), field(ticket, "upload_data")
        try:
            stored = httpx.post(url, data=data, files=files, timeout=_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise EventbriteError(str(exc)) from exc
        if stored.is_error:
            raise EventbriteError(f"Image upload: {stored.status_code} {stored.text[:200]}", stored.status_code)
        finished = self._call("POST", "/media/upload/", json={"upload_token": field(ticket, "upload_token")})
        return str(field(finished, "id"))

    def get_ticket_class(self, event_id: str, ticket_class_id: str) -> dict[str, Any]:
        return self._call("GET", f"/events/{event_id}/ticket_classes/{ticket_class_id}/")

    def get_order(self, order_id: str) -> dict[str, Any]:
        """The order with its attendees: one attendee per ticket, each with its own costs and profile."""
        return self._call("GET", f"/orders/{order_id}/", params={"expand": "attendees"})

    def refund_order(self, order_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/orders/{order_id}/refunds/", json=body)

    def publish(self, event_id: str) -> None:
        self._call("POST", f"/events/{event_id}/publish/")

    def unpublish(self, event_id: str) -> None:
        self._call("POST", f"/events/{event_id}/unpublish/")

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One authenticated request; any failure becomes :class:`EventbriteError`."""
        try:
            response = httpx.request(
                method,
                f"{API_BASE}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise EventbriteError(str(exc)) from exc
        if response.is_error:
            raise EventbriteError(f"{method} {path}: {response.status_code} {response.text}", response.status_code)
        try:
            result = response.json()
        except ValueError as exc:
            raise EventbriteError(f"{method} {path}: not JSON: {response.text[:200]}", response.status_code) from exc
        if not isinstance(result, dict):
            raise EventbriteError(f"{method} {path}: expected an object, got {result!r}"[:300], response.status_code)
        return result


def _utc(moment: datetime) -> str:
    """A datetime as Eventbrite's plain UTC string, whole seconds; what a ticket's ``sales_end`` takes."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(moment: datetime) -> dict[str, str]:
    """A datetime in Eventbrite's ``datetime-tz`` shape (local zone plus UTC); events take this, tickets do not."""
    return {"timezone": _EVENT_TIMEZONE, "utc": _utc(moment)}


def _description_html(offering: ClassOffering, sessions: list[Any], faq_html: str = "") -> str:
    """The class description, every session (a series lists them all), the FAQ when it goes as text, and the link back."""
    local = [timezone.localtime(s.starts_at) for s in sessions]
    dates = "".join(f"<li>{moment:%A %B %-d, %Y at %-I:%M %p}</li>" for moment in local)
    link = f'<p>Full details and booking: <a href="{offering.public_url}">{offering.public_url}</a></p>'
    return f"{offering.description}<p>Sessions:</p><ul>{dates}</ul>{faq_html}{link}"


def _faq_entries(faqs: list[ClassFaq]) -> list[dict[str, str]]:
    """The class's own FAQ rows in order, as the plain text Eventbrite's FAQ section holds (#716)."""
    return [{"question": strip_tags(faq.question), "answer": strip_tags(faq.answer)} for faq in faqs]


def _faq_html(faqs: list[ClassFaq]) -> str:
    """The FAQ as description text, when Eventbrite refuses its FAQ section: each question bold, its answer under it.

    Answers go through the class page's own filters (``urlize`` then ``linebreaks``, escaping on),
    so Eventbrite shows what the page shows.
    """
    items = "".join(
        f"<p><strong>{escape(faq.question)}</strong></p>{linebreaks_filter(urlize(faq.answer, autoescape=True), autoescape=True)}"
        for faq in faqs
    )
    return f"<p>Questions:</p>{items}"


def _event_body(offering: ClassOffering, client: EventbriteClient, sessions: list[Any]) -> dict[str, Any]:
    summary = offering.subtitle or offering.title
    body: dict[str, Any] = {
        "event": {
            "name": {"html": offering.title},
            "summary": summary[:_SUMMARY_MAX],
            "start": _when(sessions[0].starts_at),
            "end": _when(sessions[-1].ends_at),
            "currency": "USD",
            "venue_id": client.venue_id,
            "online_event": False,
            "listed": True,
            "format_id": CLASS_FORMAT_ID,
        }
    }
    # Only a chosen category goes out; none chosen sends neither, as before #716.
    if offering.eventbrite_category:
        body["event"]["category_id"] = offering.eventbrite_category
    if offering.eventbrite_subcategory:
        body["event"]["subcategory_id"] = offering.eventbrite_subcategory
    return body


def _quantity_total(client: EventbriteClient, offering: ClassOffering) -> int:
    """The ticket quantity that leaves Eventbrite exactly :attr:`spots_remaining` seats to sell.

    Eventbrite's ``quantity_total`` counts the tickets it already sold, and those sales are
    plfog registrations already out of ``spots_remaining``, so they are added back.
    """
    ticket = client.get_ticket_class(offering.eventbrite_event_id, offering.eventbrite_ticket_class_id)
    return int(offering.spots_remaining or 0) + int(field(ticket, "quantity_sold"))


def _ticket_body(offering: ClassOffering, sessions: list[Any], quantity_total: int) -> dict[str, Any]:
    price = offering.sale_price_cents
    ticket: dict[str, Any] = {
        "name": "Series ticket" if offering.is_series else "Ticket",
        "quantity_total": quantity_total,
        "sales_end": _utc(offering.registration_closes_at or sessions[0].starts_at),
    }
    if price:
        # A ticket's cost is the string "USD,<cents>"; the object form an order reports is refused (400).
        ticket["cost"] = f"USD,{price}"
        ticket["include_fee"] = offering.eventbrite_fee_payer == offering.EventbriteFeePayer.INCLUDED
    else:
        ticket["free"] = True
    return {"ticket_class": ticket}


def _logo_id(client: EventbriteClient, offering: ClassOffering) -> str:
    """Upload the class photo; ``""`` on any failure, because a picture never fails a listing."""
    if not offering.image:
        return ""
    try:
        with offering.image.open("rb") as photo:
            return client.upload_logo(PurePosixPath(offering.image.name or "class.jpg").name, photo.read())
    except (EventbriteError, OSError) as exc:
        logger.warning("Eventbrite image upload failed for class %s: %s", offering.pk, exc)
        return ""


def _gallery_image_ids(client: EventbriteClient, offering: ClassOffering) -> tuple[list[str], list[str]]:
    """The gallery's Eventbrite media IDs in gallery order, and why any photo was left out.

    A photo is uploaded once: its media ID is stored on the row and reused by every later sync.
    A photo that will not upload is skipped, because a picture never fails a listing.
    """
    image_ids: list[str] = []
    refused: list[str] = []
    for photo in offering.gallery_images.all():
        if not photo.eventbrite_image_id:
            try:
                with photo.image.open("rb") as file:
                    name = PurePosixPath(photo.image.name or "photo.jpg").name
                    photo.remember_eventbrite_image(client.upload_content_image(name, file.read()))
            except (EventbriteError, OSError) as exc:
                logger.warning("Eventbrite gallery upload failed for class image %s: %s", photo.pk, exc)
                refused.append(str(exc))
                continue
        image_ids.append(photo.eventbrite_image_id)
    return image_ids, refused


def _set_description(client: EventbriteClient, offering: ClassOffering, sessions: list[Any]) -> str:
    """Publish the description with the gallery and the FAQ; returns the note for the class ("" when all went).

    The first request carries the widgets (the FAQ section, and every other widget sent back as
    read), whose write shape was read back from the dashboard rather than documented. Any 400 to it
    retries once as the request verified before #716: no ``widgets`` key, the FAQ as text in the
    description. A 400 to that with photos retries as text alone. So widgets never fail a sync,
    and the worst case is the text only page. Any other failure raises as before.
    """
    event_id = offering.eventbrite_event_id
    image_ids, refused = _gallery_image_ids(client, offering)
    photo_note = EventbriteSync.photos_not_sent(refused) if refused else ""
    faqs = list(offering.faqs.all())
    try:
        client.set_description(event_id, _description_html(offering, sessions), image_ids, _faq_entries(faqs))
        return photo_note
    except EventbriteError as exc:
        if exc.status != 400:
            raise
        refusal = exc
    logger.warning("Eventbrite refused the widgets for class %s: %s", offering.pk, refusal)
    dropped = ["its widgets (the FAQ went into the description as text)" if faqs else "its widgets"]
    html = _description_html(offering, sessions, _faq_html(faqs) if faqs else "")
    try:
        client.set_description(event_id, html, image_ids)
        return " ".join(note for note in (EventbriteSync.left_out(dropped, str(refusal)), photo_note) if note)
    except EventbriteError as exc:
        if exc.status != 400 or not image_ids:
            raise
        refusal = exc
    logger.warning("Eventbrite refused the gallery for class %s: %s", offering.pk, refusal)
    client.set_description(event_id, html)
    return EventbriteSync.left_out([*dropped, "its photos"], str(refusal))


def _list(client: EventbriteClient, offering: ClassOffering) -> str:
    """Create or update the event and its ticket class, then publish it while it is a draft.

    Whether to publish is Eventbrite's answer, not our sync state: the event object a create or
    update returns carries its ``status``, so a live event behind a failed or pending sync is
    never published again, and a draft (never published, or unpublished) always is.

    Returns the note to record on the listed class: blank, or why some photos are missing.
    """
    sessions = list(offering.sessions.order_by("starts_at"))
    event = _event_body(offering, client, sessions)
    if not offering.eventbrite_event_id:
        logo = _logo_id(client, offering)
        if logo:
            event["event"]["logo_id"] = logo
        answer = client.create_event(event)
        offering.eventbrite_event_id = str(field(answer, "id"))
    else:
        answer = client.update_event(offering.eventbrite_event_id, event)
    status = field(answer, "status")
    if not offering.eventbrite_ticket_class_id:
        ticket = _ticket_body(offering, sessions, int(offering.spots_remaining or 0))
        created = client.create_ticket_class(offering.eventbrite_event_id, ticket)
        offering.eventbrite_ticket_class_id = str(field(created, "id"))
    else:
        ticket = _ticket_body(offering, sessions, _quantity_total(client, offering))
        client.update_ticket_class(offering.eventbrite_event_id, offering.eventbrite_ticket_class_id, ticket)
    note = _set_description(client, offering, sessions)
    if status == _DRAFT:
        client.publish(offering.eventbrite_event_id)
    elif status in _NOT_SELLING:
        note = " ".join(part for part in (EventbriteSync.not_selling(status), note) if part)
    return note


def _end(client: EventbriteClient, offering: ClassOffering) -> str:
    """Close ticket sales, then unpublish; returns the note to record ("" when fully down)."""
    if offering.eventbrite_ticket_class_id:
        closed = {"ticket_class": {"sales_end": _utc(timezone.now())}}
        client.update_ticket_class(offering.eventbrite_event_id, offering.eventbrite_ticket_class_id, closed)
    try:
        client.unpublish(offering.eventbrite_event_id)
    except EventbriteError as exc:
        if exc.status != 400:
            raise
        return EventbriteSync.STILL_UP
    return ""


def sync_class_listing(offering: ClassOffering) -> None:
    """Bring the class's Eventbrite listing in line with the class; set the sync fields.

    NEVER raises: records ``PENDING`` (sync off) or ``FAILED`` (API error) instead, so the plfog
    save is never rolled back. Does not save; :meth:`ClassOffering.sync_eventbrite_listing` does.
    """
    state = offering.EventbriteSyncState
    wanted = offering.wants_eventbrite_listing
    if not wanted and not offering.eventbrite_event_id:
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.IDLE, ""
        return
    if not wanted and offering.eventbrite_sync_state == state.ENDED:
        return
    client = EventbriteClient.from_settings()
    if not client.enabled:
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.PENDING, EventbriteSync.SYNC_OFF
        return
    try:
        if wanted:
            note = _list(client, offering)
            offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.LISTED, note[:_SYNC_ERROR_MAX]
        else:
            offering.eventbrite_sync_error = _end(client, offering)
            offering.eventbrite_sync_state = state.ENDED
        offering.eventbrite_synced_at = timezone.now()
    except EventbriteError as exc:
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.FAILED, str(exc)[:_SYNC_ERROR_MAX]


def push_ticket_quantity(offering: ClassOffering) -> bool:
    """Set the listed ticket's quantity from :attr:`spots_remaining`; True when Eventbrite took it.

    NEVER raises. False records ``PENDING`` (sync off) or ``FAILED`` (API error) on the class
    without saving it; :meth:`ClassOffering.push_eventbrite_quantity` saves.
    """
    state = offering.EventbriteSyncState
    client = EventbriteClient.from_settings()
    if not client.enabled:
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.PENDING, EventbriteSync.SYNC_OFF
        return False
    client.timeout = QUANTITY_PUSH_TIMEOUT_SECONDS
    try:
        body = {"ticket_class": {"quantity_total": _quantity_total(client, offering)}}
        client.update_ticket_class(offering.eventbrite_event_id, offering.eventbrite_ticket_class_id, body)
    except Exception as exc:
        # Any failure, not only an API error: this runs after the booking committed, so raising
        # would turn a saved booking into an error page. The retry command picks the class up.
        logger.exception("Eventbrite quantity push failed for class %s", offering.pk)
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.FAILED, str(exc)[:_SYNC_ERROR_MAX]
        return False
    return True
