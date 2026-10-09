"""Copy an Instagram post's picture into storage, for the class page's Watch card.

An Instagram link renders as a linked card (no third party script runs on a member page,
:mod:`classes.video_providers`). This module gives that card the post's picture. A plain GET
of a post page carries ``<meta property="og:image">`` pointing at the picture on Instagram's
CDN; that URL is signed and expires, so the bytes are copied into our own storage rather
than hotlinked.

Nothing here runs inside a page request. :func:`refresh_video_thumbnails` is the whole job,
run by ``fetch_video_thumbnails`` from ``run_scheduled_tasks`` and by hand for a backfill.

**SSRF.** The only page ever requested is rebuilt from the provider match
(``https://www.instagram.com/p/<id>/``), never the stored string. The picture URL the page
names must be https on a host ending ``.cdninstagram.com`` or ``.fbcdn.net`` on the default
port; it must answer 200 with ``image/*`` and stay under :data:`IMAGE_MAX_BYTES`. Redirects
are never followed, timeouts are short, and the bytes go through
:func:`core.images.normalize_image` before they are stored.
"""

from __future__ import annotations

import codecs
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from time import monotonic
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from classes.models import VIDEO_THUMBNAIL_PREFIX, ClassOffering
from classes.video_providers import INSTAGRAM, VideoLink, recognize
from core.files import delete_if_unreferenced
from core.images import normalize_image, store_content_addressed

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# Instagram's post page runs to about 700 KB with og:image in the first 12 KB; reading stops
# at the tag, and a page that has not named one within this many bytes is a failure.
PAGE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
# The link preview crawler's agent, which Instagram answers with the og tags (checked
# 2026-10-09; a browser agent was answered the same).
USER_AGENT = "facebookexternalhit/1.1"
RETRY_AFTER = timedelta(days=1)
# The whole fetch (page and picture) ends here however slowly the bytes trickle in; the
# per read timeouts above only bound the gap between two reads.
FETCH_DEADLINE_SECONDS = 20.0
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# httpx raises these outside ``HTTPError`` (a URL it cannot send, a stream read twice).
_HTTPX_ERRORS = (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError)
# The columns the job reads and writes; nothing else on the row is loaded.
_JOB_COLUMNS = ("pk", "video_url", "video_thumbnail", "video_thumbnail_source_url", "video_thumbnail_checked_at")


class ThumbnailFetchError(Exception):
    """The post's picture could not be fetched; the message says why, for the job's log."""


def post_page_url(link: VideoLink) -> str:
    """The one page this module requests for ``link``, rebuilt from the provider match.

    Raises:
        ValueError: ``link`` is not an Instagram link.
    """
    if link.provider is not INSTAGRAM:
        raise ValueError(f"Not an Instagram link: {link.url}")
    return f"https://www.instagram.com/p/{link.video_id}/"


class _OgImageParser(HTMLParser):
    """Collects the first ``<meta property="og:image" content="...">`` it sees."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.image_url:
            return
        values = dict(attrs)
        if values.get("property") == "og:image":
            self.image_url = (values.get("content") or "").strip()


def _check_deadline(deadline: float) -> None:
    """Raise once the fetch has run past ``deadline`` (a :func:`time.monotonic` reading)."""
    if monotonic() > deadline:
        raise ThumbnailFetchError(f"the fetch took longer than {FETCH_DEADLINE_SECONDS:g} seconds")


def find_og_image(client: httpx.Client, page_url: str, deadline: float) -> str:
    """Read ``page_url`` until its og:image tag, and return the tag's URL.

    The page is asked for uncompressed (``Accept-Encoding: identity``) so the cap counts
    the bytes that actually crossed the wire.

    Raises:
        ThumbnailFetchError: the page did not answer 200, named no picture within
            :data:`PAGE_MAX_BYTES`, or ran past ``deadline``.
    """
    with client.stream("GET", page_url, headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
            raise ThumbnailFetchError(f"the post page answered {response.status_code}")
        parser = _OgImageParser()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        read = 0
        for chunk in response.iter_bytes():
            _check_deadline(deadline)
            read += len(chunk)
            parser.feed(decoder.decode(chunk))
            if parser.image_url:
                return parser.image_url
            if read > PAGE_MAX_BYTES:
                break
    raise ThumbnailFetchError("the post page names no og:image picture")


def validate_image_url(url: str) -> str:
    """Return ``url`` when it is an https picture on Instagram's CDN, else raise.

    A backslash is refused outright for the reason :func:`classes.video_providers._host_and_route`
    gives: Python and the client may disagree about where the host ends. So is a control
    character (a tab or newline survives the page's entity decoding), and anything
    :func:`urlsplit` itself refuses (a bracketed host, a host that changes under NFKC).

    Raises:
        ThumbnailFetchError: any other scheme, host or port, or a malformed URL.
    """
    if "\\" in url or _CONTROL_CHARS.search(url):
        raise ThumbnailFetchError("the picture URL is malformed")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ThumbnailFetchError("the picture URL is malformed") from exc
    if parts.scheme != "https":
        raise ThumbnailFetchError(f"the picture URL is not https: {parts.scheme or 'no scheme'}")
    host = (parts.hostname or "").lower()
    if not host.endswith(IMAGE_HOST_SUFFIXES):
        raise ThumbnailFetchError(f"the picture is not on Instagram's CDN: {host or 'no host'}")
    if port not in (None, 443):
        raise ThumbnailFetchError(f"the picture URL names port {port}")
    return url


def download_image(client: httpx.Client, url: str, deadline: float) -> bytes:
    """Return the picture's bytes, read no further than :data:`IMAGE_MAX_BYTES`.

    Raises:
        ThumbnailFetchError: not 200, not ``image/*``, too large, or ran past ``deadline``.
    """
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ThumbnailFetchError(f"the picture answered {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("image/"):
            raise ThumbnailFetchError(f"the picture is not an image: {content_type or 'no content type'}")
        body = bytearray()
        for chunk in response.iter_bytes():
            _check_deadline(deadline)
            body += chunk
            if len(body) > IMAGE_MAX_BYTES:
                raise ThumbnailFetchError(f"the picture is larger than {IMAGE_MAX_BYTES // (1024 * 1024)} MB")
    return bytes(body)


def fetch_post_picture(client: httpx.Client, link: VideoLink) -> bytes:
    """The post's picture for ``link``, normalized to a JPEG ready to store.

    Raises:
        ThumbnailFetchError: anything on the way failed, the network included.
    """
    deadline = monotonic() + FETCH_DEADLINE_SECONDS
    try:
        image_url = validate_image_url(find_og_image(client, post_page_url(link), deadline))
        raw = download_image(client, image_url, deadline)
    except _HTTPX_ERRORS as exc:
        raise ThumbnailFetchError(f"{type(exc).__name__}: {exc}") from exc
    try:
        normalized = normalize_image(
            ContentFile(raw, name="instagram.jpg"), max_long_edge=settings.IMAGE_MAX_LONG_EDGE_GALLERY
        )
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ThumbnailFetchError("the picture could not be read as an image") from exc
    return normalized.read()


@dataclass
class RefreshSummary:
    """What one run did, by class pk. ``failed`` carries each reason for the log."""

    fetched: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    cleared: list[int] = field(default_factory=list)
    waiting: list[int] = field(default_factory=list)


def _write(offering: ClassOffering, name: str, source_url: str, checked_at: datetime | None) -> None:
    """Point ``offering`` at picture ``name`` (``""`` for none), then drop the old file if orphaned.

    A queryset update, not ``save()``: the job writes its own three columns and nothing in
    ``ClassOffering.save`` (slug, grouping, hero crop, the orphan sweep of the hero) applies.
    Clones share the stored key, so the old file goes only once no row points at it.
    """
    old_name = offering.video_thumbnail.name if offering.video_thumbnail else ""
    ClassOffering.objects.filter(pk=offering.pk).update(
        video_thumbnail=name,
        video_thumbnail_source_url=source_url,
        video_thumbnail_checked_at=checked_at,
    )
    offering.video_thumbnail = name  # type: ignore[assignment]
    offering.video_thumbnail_source_url = source_url
    offering.video_thumbnail_checked_at = checked_at
    if old_name and old_name != name:
        delete_if_unreferenced(ClassOffering, "video_thumbnail", old_name, exclude_pk=offering.pk)


def refresh_one(client: httpx.Client, offering: ClassOffering, now: datetime, summary: RefreshSummary) -> None:
    """Bring one class's picture in line with its video link, recording what happened."""
    link = recognize(offering.video_url)
    if link is None or link.provider is not INSTAGRAM:
        if offering.video_thumbnail_source_url:
            _write(offering, "", "", None)
            summary.cleared.append(offering.pk)
        return
    url = link.url
    if offering.video_thumbnail_source_url == url:
        if offering.video_thumbnail:
            return
        checked_at = offering.video_thumbnail_checked_at
        if checked_at is not None and now - checked_at < RETRY_AFTER:
            summary.waiting.append(offering.pk)
            return
    try:
        data = fetch_post_picture(client, link)
    except ThumbnailFetchError as exc:
        # The link is recorded even on failure, so the retry waits a day; a picture from an
        # earlier link is dropped, since it shows the wrong post.
        _write(offering, "", url, now)
        summary.failed.append((offering.pk, str(exc)))
        logger.warning("Could not fetch the Instagram picture for class %s (%s): %s", offering.pk, url, exc)
        return
    name = store_content_addressed(data, prefix=VIDEO_THUMBNAIL_PREFIX)
    _write(offering, name, url, now)
    summary.fetched.append(offering.pk)


def refresh_video_thumbnails(now: datetime | None = None) -> RefreshSummary:
    """Fetch, refetch or clear every class's Instagram picture as its video link says.

    A class whose Instagram link has no picture yet, or a different link than the picture
    came from, is fetched; a failed try waits :data:`RETRY_AFTER`; a removed or non Instagram
    link clears the picture. Fetch failures are collected on the summary and logged, never
    raised, so one bad post never stops the rest or fails the run.
    """
    now = now or timezone.now()
    summary = RefreshSummary()
    # An archived class is left as it is: nobody reads its page, and a post deleted since would
    # otherwise fail a run and alert the webmasters once a day for good.
    candidates = (
        ClassOffering.objects.filter(Q(video_url__icontains="instagram") | Q(video_thumbnail_source_url__gt=""))
        .exclude(status=ClassOffering.Status.ARCHIVED)
        .only(*_JOB_COLUMNS)
        .order_by("pk")
    )
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for offering in candidates:
            refresh_one(client, offering, now, summary)
    return summary
