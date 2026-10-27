"""The front desk kiosk (#950): which device is the kiosk, and finding a visitor's tour for today.

**The kiosk key.** Today's visitor names show only on a device holding the kiosk key, which an
admin creates or rotates on the Tours page's Settings tab
(:meth:`tours.models.FacilityTourSettings.rotate_kiosk_key`). The lobby screen opens
``/kiosk/?key=<key>`` once; that sets :data:`COOKIE_NAME`, a cookie signed with the site's secret
whose value is the key's digest (:attr:`~tours.models.FacilityTourSettings.kiosk_fingerprint`),
never the key itself. A cookie whose digest is not the current key's is refused, so rotating the
key locks out every screen holding the old one. Without a valid cookie ``/kiosk/`` is the public
booking page.

**Finding a tour.** Only tours still booked for today (local) are ever matched
(:meth:`tours.models.TourBookingQuerySet.booked_on_day`): never a cancelled one, never another
day's. By email, ignoring case; by name, ignoring case, spacing, accents, word order and the
hyphens, apostrophes, dots and commas people type (:func:`name_matches`).
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from datetime import date
from typing import TYPE_CHECKING

from django.http import HttpRequest, HttpResponse

from tours.models import FacilityTourSettings, TourBooking

if TYPE_CHECKING:
    from tours.models import TourBookingQuerySet

COOKIE_NAME = "pl_tour_kiosk"
COOKIE_SALT = "tours.kiosk"
COOKIE_PATH = "/kiosk/"
#: How long the kiosk cookie lives: browsers cap a cookie at 400 days. Every visit to the kiosk
#: home sets it again, so a screen that is used never runs out.
COOKIE_MAX_AGE = 400 * 24 * 60 * 60


def key_matches(raw_key: str, tour_settings: FacilityTourSettings) -> bool:
    """Whether ``raw_key`` is the current kiosk key (constant time). Never true while no key is set."""
    return bool(tour_settings.kiosk_key) and hmac.compare_digest(raw_key.encode(), tour_settings.kiosk_key.encode())


def is_kiosk(request: HttpRequest, tour_settings: FacilityTourSettings) -> bool:
    """Whether the request comes from a device holding the current kiosk key's cookie."""
    fingerprint = tour_settings.kiosk_fingerprint
    if not fingerprint:
        return False
    held = request.get_signed_cookie(COOKIE_NAME, default="", salt=COOKIE_SALT)
    return bool(held) and hmac.compare_digest(str(held).encode(), fingerprint.encode())


def remember_kiosk(request: HttpRequest, response: HttpResponse, tour_settings: FacilityTourSettings) -> None:
    """Set (or renew) the kiosk cookie on ``response`` for the current key."""
    response.set_signed_cookie(
        COOKIE_NAME,
        tour_settings.kiosk_fingerprint,
        salt=COOKIE_SALT,
        max_age=COOKIE_MAX_AGE,
        path=COOKIE_PATH,
        secure=request.is_secure(),
        httponly=True,
        samesite="Lax",
    )


def _words(text: str) -> list[str]:
    """``text`` as lowercase words with accents folded (``José`` is ``jose``), split on spaces,
    hyphens, apostrophes, dots and commas (``Jean-Luc O'Neil`` is four words)."""
    folded = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    return [word for word in re.split(r"[\s\-'\u2019.,]+", folded.casefold()) if word]


def _in_order(typed: list[str], booked: list[str]) -> bool:
    """Whether ``typed`` names ``booked`` read front to back: the leading words (``Mary Ann``), the
    trailing words (``Smith``), or the leading words then the start of the last (``Sam R``)."""
    size = len(typed)
    if typed == booked[:size] or typed == booked[-size:]:
        return True
    return size >= 2 and len(booked) >= size and typed[:-1] == booked[: size - 1] and booked[-1].startswith(typed[-1])


def name_matches(typed: str, booked: str) -> bool:
    """Whether a name typed at the kiosk names the visitor who booked as ``booked``.

    Case, spacing, accents, hyphens, apostrophes, dots and commas never matter, nor word order. The
    whole name matches; so do the first name or names alone, the last name alone, and the first
    name with the start of the last name (``Sam R``), read either way round (``Rivera Sam``).
    """
    typed_words, booked_words = _words(typed), _words(booked)
    if not typed_words or not booked_words:
        return False
    if sorted(typed_words) == sorted(booked_words):
        return True
    return _in_order(typed_words, booked_words) or _in_order(typed_words[::-1], booked_words)


def todays_tours(today: date) -> TourBookingQuerySet:
    """Today's tours still booked: the only ones the kiosk lists or matches."""
    return TourBooking.objects.booked_on_day(today).select_related("guide")


def find_by_email(email: str, today: date) -> list[TourBooking]:
    """Today's booked tours under ``email``, ignoring case."""
    cleaned = email.strip()
    if not cleaned:
        return []
    return list(todays_tours(today).filter(visitor_email__iexact=cleaned))


def find_by_name(name: str, today: date) -> list[TourBooking]:
    """Today's booked tours whose visitor ``name`` names (:func:`name_matches`)."""
    return [booking for booking in todays_tours(today) if name_matches(name, booking.visitor_name)]


def find_by_pk(raw_pk: str, today: date) -> list[TourBooking]:
    """The one of today's booked tours a tapped name (or a pick list button) posts, or none."""
    if not raw_pk.isdigit():
        return []
    return list(todays_tours(today).filter(pk=int(raw_pk)))
