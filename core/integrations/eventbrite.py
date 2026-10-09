"""Eventbrite API client + listing push (plfog → Eventbrite, one-way) for classes (#652).

Mirrors :mod:`core.integrations.discord_events`: build via :meth:`EventbriteClient.from_settings`,
check ``.enabled``, and **never raise to the caller**. An Eventbrite outage records a sync error
on the class and the plfog save proceeds regardless; ``retry_eventbrite_pushes`` tries again.

Five gates make a client ``enabled``: the admin toggle
(``SiteConfiguration.eventbrite_sync_enabled``), a private token, an organization ID, an organizer
ID and a venue ID (``EVENTBRITE_*`` settings). Staging is always disabled, because its database
is a copy of production and names the same classes.

plfog owns the seat count: the ticket quantity is always set from ``spots_remaining`` and never
read back. Eventbrite refuses to unpublish a paid event that holds orders, so ending a listing
closes ticket sales first; the unpublish that follows may then be refused and that is recorded,
not retried.

What goes to Eventbrite follows its Unauthorized Selling policy (#720, after Trust and Safety took
down class 675's event): no links, addresses or booking line in the listing's text, no
cancellation or refund FAQ, and an event plfog published that reads ``draft`` again is never
published a second time.
"""

from __future__ import annotations

import html as html_module
import logging
import re
from dataclasses import dataclass
from datetime import UTC
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx
from django.conf import settings
from django.db.models import TextChoices
from django.template.defaultfilters import linebreaks_filter
from django.utils import timezone
from django.utils.html import escape, strip_tags

from classes.eventbrite_categories import CLASS_FORMAT_ID
from core.html_sanitize import AllowlistCleaner
from core.linkify import _TLDS as _LINKIFY_TLDS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from classes.models import ClassOffering

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

# What the listing's text may carry (#720): the class editor's tags (core.html_sanitize) without
# ``a`` and with no attributes, so a link goes as its text and no href or src reaches Eventbrite.
_LISTING_CLEANER = AllowlistCleaner(
    ["p", "br", "strong", "b", "em", "i", "u", "h2", "h3", "ul", "ol", "li", "blockquote"], {}
)
_TEXT_CLEANER = AllowlistCleaner((), {})
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# An address with a scheme or ``www.``, any case.
_SCHEME_ADDRESS_RE = re.compile(r"\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<]+", re.IGNORECASE)
# A bare host: all lowercase dotted labels and a lowercase TLD, then an optional path. It counts
# only when the TLD is a known one or a "/" path follows, so a missing space ("glass.Bring",
# "Mr.Smith", "Node.js", "pattern.pdf", "e.g.leather") is text, not an address. Emails go
# first, so no host here is an email's tail.
_BARE_HOST_RE = re.compile(r"(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+([a-z]{2,24})\b(/[^\s<]*)?")
# core.linkify's TLDs (bleach's list) plus the newer ones a makerspace or an artist would use.
_HOST_TLDS = _LINKIFY_TLDS | frozenset(
    """space events gallery academy art studio studios glass shop store coffee design works xyz app
    dev io co me info biz us online site website club community center school education guru tools
    supply market live world life today fun page link blog music photo photography""".split()
)
# A FAQ about cancelling, refunds or no-shows tells buyers how to get money back off Eventbrite.
_OFF_PLATFORM_FAQ_RE = re.compile(r"\b(?:cancel\w*|refund\w*|no[\s\-\u2013\u2014]?shows?)\b", re.IGNORECASE)
# Statuses that show the event was published, by plfog or anyone: plfog never publishes it again (#720).
_PUBLISHED = frozenset({"live", "started"})
# A US phone number, with or without separators and a leading 1 (#725). Prices, times and
# dates have the wrong grouping; a digit, word, "$" or "." on either side rules a match out.
_PHONE_RE = re.compile(
    r"(?<![\w$.,/])(?:\+1[\s.-]?|1[\s.-])?(?:\(\d{3}\)\s?|\d{3}[\s.-]?)\d{3}[\s.-]?\d{4}(?!\w|[.,]\d)"
)
# A social handle (#725): "@" and a name of two or more, ending on a letter, digit or "_" so a
# full stop after it stays. Emails are matched first, so this never sees an address's tail.
_HANDLE_RE = re.compile(r"(?<![\w@.&/-])@[A-Za-z0-9_][A-Za-z0-9_.]*[A-Za-z0-9_]")

# Eventbrite's selling rules (#725, eventbrite.com/l/contentstandards and the Merchant Agreement).
# Payment asked for outside the ticket: a pay app; cash in a paying sense ("cash only", "in cash",
# "cash/venmo", "bring cash", never "no cash value" or "cash in on"); paying at the session, door,
# instructor or on the day; a fee due or collected there; or money brought along ("Bring $20").
_PAYMENT_RE = re.compile(
    r"\b(?:venmo|paypal|zelle|cash\s?app)\b"
    r"|\bcash\b(?=\s*(?:/|only\b|payments?\b|(?:is\s+)?due\b|at\s+the\b|to\s+the\b|,?\s*(?:or|and)\s+(?:venmo|paypal|zelle|check|card)\b))"
    r"|(?<=/)cash\b"
    r"|\b(?:in|with|by)\s+cash\b"
    r"|\b(?:pay|paid|paying|bring|bringing)\s+(?:\$?\d+(?:\.\d\d)?\s+)?(?:in\s+)?cash\b"
    r"|\b(?:pay(?:s|ing|able)?|paid|payments?)\b(?!\s+attention)[^.\n!?]{0,40}?\b(?:at|to|in|on)\s+(?:the\s+)?"
    r"(?:session|door|class|instructor|teacher|studio|workshop|day)\b"
    r"|\bpay(?:s|ing)?\s+(?:the\s+|your\s+)?(?:instructor|teacher)\b"
    r"|(?:\$\d|\bfees?\b|\bcosts?\b|\bpayments?\b)[^.\n!?]{0,40}?\b(?:due|collected)\s+(?:at|on|in)\s+(?:the\s+)?"
    r"(?:start\s+of\s+(?:the\s+)?)?(?:session|door|class|day|workshop)\b"
    r"|\bbring(?:ing)?\s+\$\d[\d.,]*",
    re.IGNORECASE,
)
# A cost on top of the ticket. A fee stated as included ("$10 materials fee is included in the
# class price") matches none of these; "no extra fee" is let through by _NEGATED_RE.
_FEE_RE = re.compile(
    r"\b(?:(?:lab|materials?|supply|supplies|studio|kiln|firing|kit)\s+)?(?:fees?|costs?|charges?)\s+"
    r"(?:apply|applies|(?:is\s+|are\s+)?extra|(?:is\s+|are\s+)?separate)\b"
    r"|\b(?:materials?|supplies|kits?)\s+(?:is\s+|are\s+)?(?:an?\s+)?(?:extra|additional|separate)\b"
    r"|\b(?:fees?|costs?|charges?|materials?|supplies|price)\b[^.\n!?;]{0,40}?\bnot\s+included\b"
    r"|\b(?:price|ticket|cost|fee)\s+(?:does\s+not|doesn[’']t)\s+include\b"
    r"|\b(?:additional|extra)\s+(?:costs?|fees?|charges?)\b"
    r"|\bplus\s+(?:an?\s+)?\$\d[\d.,]*\s+(?:[a-z]+\s+)?(?:fees?|charges?|costs?)\b",
    re.IGNORECASE,
)
_NEGATED_RE = re.compile(r"\b(?:no|without|zero)\s+$", re.IGNORECASE)
# A discount or coupon code, named as one or by the makerspace's own code shapes
# (PLHalfOff, PLMetal10, PL-10%off). A token after "code" counts only with a digit, a "%" or two
# capitals, so "dress code Black", "QR code below" and "use the code editor" are text.
_CODE_RE = re.compile(
    r"\b(?:discount|coupon|promo|promotional|promotion)\s+codes?\b"
    r"|\b(?:use|enter|apply)\s+(?:the\s+)?coupon(?:\s+codes?)?\b"
    r"|\bPL-?\d+\s?%\s?off\b",
    re.IGNORECASE,
)
_CODE_TOKEN_RE = re.compile(
    r"\b(?:(?:[Uu]se|[Ee]nter|[Aa]pply)\s+(?:the\s+)?)?[Cc]ode\s*[:=]?\s*[\"“'‘]?"
    r"(?=[\w%-]*(?:\d|%|[A-Z][\w-]*[A-Z]))[A-Za-z0-9][\w%-]{2,}"
    r"|\bPL(?:[A-Z][a-z]+)+\d*\b"
)
_TAG_RE = re.compile(r"<[^>]*>")


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
    TAKEN_DOWN = "Unpublished on Eventbrite outside plfog; not republished."
    RULES_REFUSAL = "Eventbrite would take this listing down. Fix these, then save again."

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

    def __init__(self, *, token: str, organization_id: str, venue_id: str, organizer_id: str) -> None:
        self._token = token
        self.timeout = _TIMEOUT_SECONDS
        self.organization_id = organization_id
        self.venue_id = venue_id
        self.organizer_id = organizer_id

    @classmethod
    def from_settings(cls) -> EventbriteClient:
        """Build a client from ``settings`` and ``SiteConfiguration``; never raises."""
        from core.models import SiteConfiguration

        if settings.IS_STAGING or not SiteConfiguration.load().eventbrite_sync_enabled:
            return cls(token="", organization_id="", venue_id="", organizer_id="")
        return cls(
            token=settings.EVENTBRITE_PRIVATE_TOKEN,
            organization_id=settings.EVENTBRITE_ORGANIZATION_ID,
            venue_id=settings.EVENTBRITE_VENUE_ID,
            organizer_id=settings.EVENTBRITE_ORGANIZER_ID,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.organization_id and self.venue_id and self.organizer_id)

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._call("GET", f"/events/{event_id}/")

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


def _without_addresses(clean_html: str) -> str:
    """``nh3`` output with every email, web address, phone number and handle dropped, text and all."""
    text = _SCHEME_ADDRESS_RE.sub("", _EMAIL_RE.sub("", clean_html))
    text = _BARE_HOST_RE.sub(_drop_host, text)
    return _HANDLE_RE.sub("", _PHONE_RE.sub("", text))


def _drop_host(match: re.Match[str]) -> str:
    """A bare host goes when its TLD is known or a path follows it; anything else was text."""
    return "" if match.group(1) in _HOST_TLDS or match.group(2) else match.group(0)


# The rules as instructors and admins read them beside the Eventbrite switch (#725).
EVENTBRITE_RULES = (
    "Eventbrite takes down listings that send buyers anywhere else. To sell this class on Eventbrite, "
    "keep the title, description, FAQ and photos free of: payment outside the ticket (cash, Venmo, pay at "
    "the session); fees not included in the price; discount codes; links, web addresses, QR codes, emails, "
    "phone numbers or social handles; and booking, refund or cancellation instructions. Eventbrite shows "
    "its own refund policy. A takedown can suspend the makerspace's whole Eventbrite account."
)


class ListingRule(TextChoices):
    """What the listing check found (#725): the first four block the listing, the last two are dropped."""

    OFF_TICKET_PAYMENT = "payment", "payment outside the ticket"
    FEE_NOT_INCLUDED = "fee", "a cost not included in the price"
    DISCOUNT_CODE = "code", "a discount code"
    ADDRESS_IN_TITLE = "title_address", "a link, email, phone number or handle in the title"
    ADDRESS = "address", "a link, email, phone number or handle"
    REFUND_FAQ = "refund_faq", "a cancellation, refund or no-show question"


@dataclass(frozen=True)
class ListingFinding:
    """One rule found in one place, with every distinct piece of text that tripped it, in order."""

    where: str
    rule: ListingRule
    words: tuple[str, ...]

    def __str__(self) -> str:
        quoted = ", ".join(f"“{word}”" for word in self.words)
        return f"{self.where}: {self.rule.label}: {quoted}" if quoted else f"{self.where}: {self.rule.label}"


@dataclass(frozen=True)
class ListingCheck:
    """The listing check's answer: ``problems`` block the listing, ``left_out`` go quietly on the way out."""

    problems: tuple[ListingFinding, ...]
    left_out: tuple[ListingFinding, ...]

    @property
    def refusal_lines(self) -> list[str]:
        """The refusal as the form shows it: what to do, then one line per problem."""
        return [EventbriteSync.RULES_REFUSAL, *(str(problem) for problem in self.problems)]

    @property
    def refusal(self) -> str:
        """The refusal on one line, as a class's sync error records it."""
        return " ".join([EventbriteSync.RULES_REFUSAL, "; ".join(str(problem) for problem in self.problems)])


def _plain(text: str) -> str:
    """Author HTML or text as words: every tag a space, entities read back."""
    return html_module.unescape(_TAG_RE.sub(" ", text))


def _addresses(text: str) -> list[str]:
    """Every address the listing drops from ``text``, in the order :func:`_without_addresses` drops them."""
    found: list[str] = _EMAIL_RE.findall(text)
    text = _EMAIL_RE.sub(" ", text)
    found += _SCHEME_ADDRESS_RE.findall(text)
    text = _SCHEME_ADDRESS_RE.sub(" ", text)
    found += [match.group(0) for match in _BARE_HOST_RE.finditer(text) if not _drop_host(match)]
    text = _BARE_HOST_RE.sub(_drop_host, text)
    found += _PHONE_RE.findall(text)
    return found + _HANDLE_RE.findall(_PHONE_RE.sub(" ", text))


def _fees(text: str) -> list[str]:
    """Each cost on top of the ticket, skipping one the text says there is none of ("no extra fee")."""
    return [match.group(0) for match in _FEE_RE.finditer(text) if not _NEGATED_RE.search(text[: match.start()])]


def _codes(text: str) -> list[str]:
    """Each code mention in order; mentions that overlap are quoted as one ("Use code PLHalfOff")."""
    merged: list[list[int]] = []
    for start, end in sorted(match.span() for regex in (_CODE_RE, _CODE_TOKEN_RE) for match in regex.finditer(text)):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [text[start:end] for start, end in merged]


def _finding(where: str, rule: ListingRule, words: list[str]) -> list[ListingFinding]:
    """One finding carrying each distinct word once, or none when nothing matched."""
    distinct = tuple(dict.fromkeys(" ".join(word.split()) for word in words))
    return [ListingFinding(where, rule, distinct)] if distinct else []


def _selling_problems(where: str, text: str) -> list[ListingFinding]:
    return [
        *_finding(where, ListingRule.OFF_TICKET_PAYMENT, _PAYMENT_RE.findall(text)),
        *_finding(where, ListingRule.FEE_NOT_INCLUDED, _fees(text)),
        *_finding(where, ListingRule.DISCOUNT_CODE, _codes(text)),
    ]


def check_listing(title: str, subtitle: str, description: str, faqs: Sequence[Mapping[str, str]]) -> ListingCheck:
    """Check a class's text against Eventbrite's selling rules before it is listed (#725).

    Blocks payment outside the ticket, a cost not included in the price, a discount code, and
    any address in the title (the title goes out as written). Addresses anywhere else, and a
    FAQ about cancelling, refunds or no-shows, are reported as left out: the push drops them.
    A dropped FAQ is never read for anything else, since none of it reaches Eventbrite.

    Args:
        title: The class title, sent as written.
        subtitle: The subtitle, plain text.
        description: The description, author HTML.
        faqs: The class's own FAQ rows, each with ``question`` and ``answer``.

    Returns:
        The problems that block the listing and the parts the push leaves out.
    """
    plain_title = _plain(title)
    problems = [
        *_selling_problems("Title", plain_title),
        *_finding("Title", ListingRule.ADDRESS_IN_TITLE, _addresses(plain_title)),
    ]
    left_out: list[ListingFinding] = []
    for where, text in (("Subtitle", _plain(subtitle)), ("Description", _plain(description))):
        problems += _selling_problems(where, text)
        left_out += _finding(where, ListingRule.ADDRESS, _addresses(text))
    for faq in faqs:
        question = " ".join(_plain(faq["question"]).split())
        where, text = f"FAQ “{question}”", f"{question}\n{_plain(faq['answer'])}"
        if _OFF_PLATFORM_FAQ_RE.search(text):
            left_out.append(ListingFinding(where, ListingRule.REFUND_FAQ, ()))
            continue
        problems += _selling_problems(where, text)
        left_out += _finding(where, ListingRule.ADDRESS, _addresses(text))
    return ListingCheck(tuple(problems), tuple(left_out))


def _listing_html(html: str) -> str:
    """Author HTML as the listing may carry it: the editor's formatting, links as their text, no addresses."""
    return _without_addresses(_LISTING_CLEANER.clean(html))


def _listing_text(text: str) -> str:
    """Plain text as the listing may carry it: tags gone, no addresses, line breaks kept."""
    plain = html_module.unescape(_without_addresses(_TEXT_CLEANER.clean(text)))
    return re.sub(r"[ \t]+(?=\n|$)", "", re.sub(r"[ \t]{2,}", " ", plain)).strip()


def _description_html(offering: ClassOffering, sessions: list[Any], faq_html: str = "") -> str:
    """The class description and every session (a series lists them all), then the FAQ when it goes as text.

    No booking line and no link back to the site (#720): Eventbrite counts sending buyers
    elsewhere as Unauthorized Selling.
    """
    local = [timezone.localtime(s.starts_at) for s in sessions]
    dates = "".join(f"<li>{moment:%A %B %-d, %Y at %-I:%M %p}</li>" for moment in local)
    return f"{_listing_html(offering.description)}<p>Sessions:</p><ul>{dates}</ul>{faq_html}"


def _sendable_faqs(faqs: list[dict[str, str]]) -> list[dict[str, str]]:
    """The FAQ items Eventbrite may show: none about cancelling, refunds or no-shows (#720), locked or not."""
    return [
        faq
        for faq in faqs
        if not _OFF_PLATFORM_FAQ_RE.search(f"{strip_tags(faq['question'])} {strip_tags(faq['answer'])}")
    ]


def _faq_items(offering: ClassOffering) -> list[dict[str, str]]:
    """The locked questions first, then the class's own rows in order (the arrival FAQ stays off Eventbrite)."""
    from classes.models import LOCKED_CLASS_FAQS

    return [*(dict(faq) for faq in LOCKED_CLASS_FAQS), *offering.own_faqs()]


def _faq_entries(faqs: list[dict[str, str]]) -> list[dict[str, str]]:
    """The FAQ in order, as the plain text Eventbrite's FAQ section holds (#716), without addresses (#720)."""
    return [{"question": _listing_text(faq["question"]), "answer": _listing_text(faq["answer"])} for faq in faqs]


def _faq_html(faqs: list[dict[str, str]]) -> str:
    """The FAQ as description text, when Eventbrite refuses its FAQ section: each question bold, its answer under it.

    Answers keep their line breaks (``linebreaks``, escaping on), as on the class page, but no
    address becomes a link: addresses are dropped (#720).
    """
    items = "".join(
        f"<p><strong>{escape(_listing_text(faq['question']))}</strong></p>"
        f"{linebreaks_filter(_listing_text(faq['answer']), autoescape=True)}"
        for faq in faqs
    )
    return f"<p>Questions:</p>{items}"


def _event_body(offering: ClassOffering, client: EventbriteClient, sessions: list[Any]) -> dict[str, Any]:
    summary = _listing_text(offering.subtitle) or _listing_text(offering.title)
    body: dict[str, Any] = {
        "event": {
            "name": {"html": offering.title},
            "summary": summary[:_SUMMARY_MAX],
            "organizer_id": client.organizer_id,
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
    faqs = _sendable_faqs(_faq_items(offering))
    try:
        client.set_description(event_id, _description_html(offering, sessions), image_ids, _faq_entries(faqs))
        return photo_note
    except EventbriteError as exc:
        if exc.status != 400:
            raise
        refusal = exc
    logger.warning("Eventbrite refused the widgets for class %s: %s", offering.pk, refusal)
    # The FAQ is never empty: the locked accessibility question rides on every class (#720 keeps cancellation off).
    dropped = ["its widgets (the FAQ went into the description as text)"]
    html = _description_html(offering, sessions, _faq_html(faqs))
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


def _list(client: EventbriteClient, offering: ClassOffering) -> tuple[str, str]:
    """Create or update the event and its ticket class, then publish it while it is a draft plfog never published.

    Whether to publish is Eventbrite's answer plus :attr:`ClassOffering.eventbrite_published`, not
    our sync state: the event object a create or update returns carries its ``status``, so a live
    event behind a failed or pending sync is never published again, and a draft plfog never
    published (or unpublished itself) is. A draft plfog published was taken down outside plfog,
    by Eventbrite or in the dashboard, and is left down (#720); the class records that as ``ENDED``.

    Returns the sync state and the note to record: blank, or why some photos are missing.
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
    if status in _PUBLISHED:
        # Remembered from Eventbrite's answer too, not only after plfog's own publish call: a
        # publish that landed but timed out or answered 5xx shows up here as live on the retry.
        offering.eventbrite_published = True
    if not offering.eventbrite_ticket_class_id:
        ticket = _ticket_body(offering, sessions, int(offering.spots_remaining or 0))
        created = client.create_ticket_class(offering.eventbrite_event_id, ticket)
        offering.eventbrite_ticket_class_id = str(field(created, "id"))
    else:
        ticket = _ticket_body(offering, sessions, _quantity_total(client, offering))
        client.update_ticket_class(offering.eventbrite_event_id, offering.eventbrite_ticket_class_id, ticket)
    note = _set_description(client, offering, sessions)
    listed = offering.EventbriteSyncState.LISTED
    if status == _DRAFT and offering.eventbrite_published:
        taken_down = " ".join(part for part in (EventbriteSync.TAKEN_DOWN, note) if part)
        return offering.EventbriteSyncState.ENDED, taken_down
    if status == _DRAFT:
        client.publish(offering.eventbrite_event_id)
        offering.eventbrite_published = True
    elif status in _NOT_SELLING:
        note = " ".join(part for part in (EventbriteSync.not_selling(status), note) if part)
    return listed, note


def _end(client: EventbriteClient, offering: ClassOffering) -> str:
    """Close ticket sales, then unpublish; returns the note to record ("" when fully down).

    plfog's own unpublish clears :attr:`ClassOffering.eventbrite_published`, so switching the
    class back on publishes it again. An event plfog published that already reads ``draft`` was
    taken down outside plfog: it is left as it is and stays remembered, so no relist undoes it (#720).
    """
    event_id = offering.eventbrite_event_id
    if offering.eventbrite_ticket_class_id:
        closed = {"ticket_class": {"sales_end": _utc(timezone.now())}}
        client.update_ticket_class(event_id, offering.eventbrite_ticket_class_id, closed)
    if offering.eventbrite_published and field(client.get_event(event_id), "status") == _DRAFT:
        return EventbriteSync.TAKEN_DOWN
    try:
        client.unpublish(event_id)
    except EventbriteError as exc:
        if exc.status != 400:
            raise
        return EventbriteSync.STILL_UP
    offering.eventbrite_published = False
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
    if wanted and (check := offering.eventbrite_listing_check()).problems:
        # Edited through a path the form check does not guard (#725): nothing is sent.
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.FAILED, check.refusal[:_SYNC_ERROR_MAX]
        return
    client = EventbriteClient.from_settings()
    if not client.enabled:
        offering.eventbrite_sync_state, offering.eventbrite_sync_error = state.PENDING, EventbriteSync.SYNC_OFF
        return
    try:
        if wanted:
            listed_state, note = _list(client, offering)
            offering.eventbrite_sync_state, offering.eventbrite_sync_error = listed_state, note[:_SYNC_ERROR_MAX]
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
