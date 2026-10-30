"""Vendor Tables' Google Sheet: the link an admin pastes, and the one HTTP read of it (#1013).

The portal reads Kate's sheet through its public CSV export
(``https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>``), so the sheet must be shared as anyone
with the link can view; no Google credentials are involved. :func:`sheet_link` reads a pasted link into the sheet's
id and tab (``gid``) or refuses it, and :func:`fetch_sheet_csv` is the only function here that touches the network:
specs replace it (or mock its ``httpx`` call with ``respx``) and never reach Google.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from hq.vendor_import import MAX_IMPORT_BYTES

SHEET_HOST = "docs.google.com"
SIGN_IN_HOST = "accounts.google.com"
FETCH_TIMEOUT_SECONDS = 10.0
# Google answers the export with a redirect or two to its content host; a sign in page is a redirect too.
MAX_REDIRECTS = 5

NOT_A_SHEET_LINK = (
    "That is not a Google Sheets link. Paste the link from the sheet's Share button, "
    "like https://docs.google.com/spreadsheets/d/..."
)
NOT_SHARED = (
    'The sheet is not shared with anyone with the link. In the sheet, choose Share, then "Anyone with the link".'
)
NOT_FOUND = "There is no sheet at that link. Check the link."
NO_ANSWER = "Google did not answer in time. The pull will try again tomorrow."
TOO_LARGE = "The sheet is over 1 MB. Nothing was pulled."
NOT_GOOGLE = "Google sent the pull to an address outside Google. Nothing was pulled."
# The hosts a fetch may reach, Google's redirects included: Google's own and its content host.
ALLOWED_HOST_ENDS = (".google.com", ".googleusercontent.com")

_ID = re.compile(r"^/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:/|$)")
_GID = re.compile(r"^\d+$")


class SheetUnreadable(Exception):
    """The sheet could not be read, so nothing in it is written. The message says why, in plain words."""


@dataclass(frozen=True)
class SheetLink:
    """A Google Sheet's id and the tab (``gid``) its link named, 0 (the first tab) when it named none."""

    sheet_id: str
    gid: str

    @property
    def edit_url(self) -> str:
        """The sheet's own address for that tab, built from the id: what is stored and linked, never the pasted text."""
        return f"https://{SHEET_HOST}/spreadsheets/d/{self.sheet_id}/edit#gid={self.gid}"

    @property
    def export_url(self) -> str:
        """The sheet's public CSV export address for that tab."""
        return f"https://{SHEET_HOST}/spreadsheets/d/{self.sheet_id}/export?format=csv&gid={self.gid}"


def sheet_link(url: str) -> SheetLink:
    """The sheet ``url`` points at: an ``https://docs.google.com/spreadsheets/d/<id>/...`` link, its tab from
    ``?gid=`` or ``#gid=`` (0 when neither is there).

    Raises:
        ValueError: the link is anything else, a published ``/d/e/`` link included; the message is
            :data:`NOT_A_SHEET_LINK`.
    """
    text = url.strip()
    parts = urlsplit(text)
    match = _ID.match(parts.path)
    # A backslash or a user part ("https://evil.example\\@docs.google.com/...") reads as Google to Python but as
    # another host to a browser, so the host must be exactly docs.google.com: no user part, no port.
    if "\\" in text or parts.netloc != SHEET_HOST or parts.scheme != "https" or match is None:
        raise ValueError(NOT_A_SHEET_LINK)
    gids = parse_qs(parts.query).get("gid", []) + parse_qs(parts.fragment).get("gid", [])
    gid = next((value for value in gids if _GID.match(value)), "0")
    return SheetLink(sheet_id=match.group(1), gid=gid)


def fetch_sheet_csv(link: SheetLink) -> bytes:
    """The sheet's tab as CSV bytes, read from its public export, following Google's redirects.

    Reads at most one byte past :data:`MAX_IMPORT_BYTES`, so an oversized sheet is refused without being held whole.

    Raises:
        SheetUnreadable: a redirect led outside Google (``ALLOWED_HOST_ENDS``); Google did not answer in time; it sent a sign in page (a redirect to
            accounts.google.com, a 401 or 403, or an HTML page: the sheet is not shared with anyone with the link);
            there is no such sheet (404); any other answer than 200; or the sheet is over the size cap.
    """
    try:
        with (
            httpx.Client(
                timeout=FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                event_hooks={"request": [_only_google]},
            ) as client,
            client.stream("GET", link.export_url) as response,
        ):
            _check_answer(response)
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_IMPORT_BYTES:
                    raise SheetUnreadable(TOO_LARGE)
            return body
    except httpx.HTTPError as exc:
        raise SheetUnreadable(NO_ANSWER) from exc


def _only_google(request: httpx.Request) -> None:
    """Refuse a request, a followed redirect included, to any host outside Google, before it is sent."""
    if not request.url.host.endswith(ALLOWED_HOST_ENDS):
        raise SheetUnreadable(NOT_GOOGLE)


def _check_answer(response: httpx.Response) -> None:
    """Refuse an answer that is not the sheet's CSV, in plain words."""
    if response.url.host == SIGN_IN_HOST or response.status_code in (401, 403):
        raise SheetUnreadable(NOT_SHARED)
    if response.status_code == 404:
        raise SheetUnreadable(NOT_FOUND)
    if response.status_code != 200:
        raise SheetUnreadable(
            f"Google answered with an error ({response.status_code}). The pull will try again tomorrow."
        )
    if "text/html" in response.headers.get("content-type", ""):
        raise SheetUnreadable(NOT_SHARED)
