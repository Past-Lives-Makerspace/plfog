"""Phone numbers as members type them: the ``tel:`` number in one, and whether a value is one.

The directory card's phone field (the ``tel_href`` filter in ``hub.templatetags.hub_tags``) and
a contact saved as a phone number (``MemberContact.as_link``) read numbers the same way.
"""

from __future__ import annotations

import re

# An extension marker and its digits: "x12", "ext 12", "ext. 12", "extension 12", "#12". Not \b
# before the letter: "0199x12" has no word boundary there.
_EXTENSION = re.compile(r"(?<![a-z])(?:x|ext\.?|extension|#)\s*(\d+)", re.IGNORECASE)
_SECOND_NUMBER = re.compile(r"[,/]")
_DIGITS = frozenset("0123456789")
# What is left of a phone number once its extensions are cut out: digits and the separators
# people type between them, and a second number after a comma or a slash.
_PHONE_SHAPE = re.compile(r"^[\d\s()+.,/-]+$")
# How many digits a dialable number has. Without a "+" it is a North American number: 7 (local),
# 10, or 11 with the leading 1. With a "+" it is E.164: at most 15. Anything else typed in
# digits and separators is a date range, an amount or an ID ("2024-2025", "123456789012345678").
_LOCAL_DIGIT_COUNTS = frozenset({7, 10, 11})
_MIN_DIGITS = 7
_MAX_DIGITS = 15


def tel_number(phone: str) -> str:
    """``phone`` as the number part of a ``tel:`` link: its digits, with a leading plus kept.

    Members type their number any way they like ("(503) 555 0199", "503.555.0199"); a dialer
    wants the digits. A ``+`` in front marks a country code and stays, so an international
    number still dials. An extension ("x12", "ext. 12", "#12", ", ext 12") rides along as RFC 3966's
    ``;ext=12`` instead of being dialled as part of the number, and of two numbers split by a
    comma or a slash only the first is linked. A value with no digits before any extension
    gives "", and the card shows it as text. ``_DIGITS`` and not ``str.isdigit``, which also
    takes superscripts.
    """
    first, *rest = _SECOND_NUMBER.split(phone, maxsplit=1)
    number = first
    extension = _EXTENSION.search(first)
    if extension is not None:
        number = first[: extension.start()]
    elif rest:
        extension = _EXTENSION.match(rest[0].strip())
    digits = "".join(ch for ch in number if ch in _DIGITS)
    if not digits:
        return ""
    href = f"+{digits}" if number.strip().startswith("+") else digits
    return f"{href};ext={extension.group(1)}" if extension is not None else href


def phone_tel_number(value: str) -> str:
    """The ``tel:`` number of a value that is nothing but a phone number, else "".

    :func:`tel_number` finds digits in anything ("Open Tue 5pm" gives "5"), which is right for a
    field that only holds a phone number and wrong for a free text contact. Here the value must
    be digits and phone separators once extensions are removed, and the number
    :func:`tel_number` reads from it must have a phone's digit count (``_LOCAL_DIGIT_COUNTS``,
    or ``_MIN_DIGITS`` to ``_MAX_DIGITS`` after a "+"). A date ("10/12/2024") or an amount
    ("1,000,000") splits at its first slash or comma and leaves too few digits.
    """
    if not _PHONE_SHAPE.match(_EXTENSION.sub("", value)):
        return ""
    href = tel_number(value)
    number = href.partition(";")[0]
    digits = len(number.lstrip("+"))
    if number.startswith("+"):
        dialable = _MIN_DIGITS <= digits <= _MAX_DIGITS
    else:
        dialable = digits in _LOCAL_DIGIT_COUNTS
    return href if dialable else ""
