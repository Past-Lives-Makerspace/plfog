"""The video links plfog accepts, and how each one renders.

One registry. A URL is recognised here or it is not accepted anywhere, and the same
recognition decides how the link renders: YouTube embeds in an iframe, Instagram and
Facebook render as a linked card that opens on their own site (no third party embed
script runs on a member facing page).

Adding a fourth provider is a new :class:`VideoProvider` in :data:`PROVIDERS` — the
validator's message, the accepted URL shapes and the rendering all follow from that
one entry. No call site grows a branch.

Host matching is exact against :attr:`UrlRule.hosts`, taken from the parsed URL's
``hostname`` (so credentials in the authority, ``https://instagram.com@evil.test/p/x``,
resolve to ``evil.test`` and are refused). A lookalike domain — ``instagram.com.evil.test``,
``notinstagram.com`` — is simply not in the set, so it never matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

_ID = r"[A-Za-z0-9_-]{11}"  # YouTube's video id is always 11 of these.
_HAS_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://")


@dataclass(frozen=True)
class UrlRule:
    """One accepted URL shape: the hosts it may come from and the path it must match.

    ``pattern`` is full-matched against the URL's path plus its query string
    (``/watch?v=abc``), never against the whole URL, so a host can only ever be the
    parsed authority. A named ``id`` group, where present, is the video id.
    """

    hosts: frozenset[str]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class VideoProvider:
    """A site whose video links plfog accepts.

    ``embed_template`` carries the iframe source when the provider can be embedded
    without a third party script (``{video_id}`` is substituted); an empty template
    means the link renders as a card instead.
    """

    key: str
    name: str
    example: str
    rules: tuple[UrlRule, ...]
    embed_template: str = ""

    def match(self, host: str, route: str) -> str | None:
        """Return the video id (possibly ``""``) when this provider owns ``host``/``route``, else None."""
        for rule in self.rules:
            if host not in rule.hosts:
                continue
            found = rule.pattern.fullmatch(route)
            if found:
                return found.groupdict().get("id") or ""
        return None


@dataclass(frozen=True)
class VideoLink:
    """A recognised video link, ready to render."""

    provider: VideoProvider
    url: str
    video_id: str

    @property
    def is_embed(self) -> bool:
        """Whether this renders as an iframe (True) or as a linked card (False)."""
        return bool(self.provider.embed_template)

    @property
    def embed_src(self) -> str:
        """The iframe source. Empty for a provider that renders as a card."""
        if not self.provider.embed_template:
            return ""
        return self.provider.embed_template.format(video_id=self.video_id)


_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_YOUTU_BE_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_NOCOOKIE_HOSTS = frozenset({"youtube-nocookie.com", "www.youtube-nocookie.com"})
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com", "m.instagram.com"})
_FACEBOOK_HOSTS = frozenset(
    {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.com", "www.fb.com"}
)
_FB_WATCH_HOSTS = frozenset({"fb.watch", "www.fb.watch"})

YOUTUBE = VideoProvider(
    key="youtube",
    name="YouTube",
    example="https://www.youtube.com/watch?v=…",
    embed_template="https://www.youtube-nocookie.com/embed/{video_id}",
    rules=(
        UrlRule(_YOUTUBE_HOSTS, re.compile(rf"/watch/?\?(?:[^&]+&)*v=(?P<id>{_ID})(?:&.*)?")),
        UrlRule(_YOUTUBE_HOSTS, re.compile(rf"/embed/(?P<id>{_ID})/?(?:\?.*)?")),
        UrlRule(_YOUTUBE_HOSTS, re.compile(rf"/shorts/(?P<id>{_ID})/?(?:\?.*)?")),
        UrlRule(_YOUTU_BE_HOSTS, re.compile(rf"/(?P<id>{_ID})/?(?:\?.*)?")),
        UrlRule(_NOCOOKIE_HOSTS, re.compile(rf"/embed/(?P<id>{_ID})/?(?:\?.*)?")),
    ),
)

INSTAGRAM = VideoProvider(
    key="instagram",
    name="Instagram",
    example="https://www.instagram.com/reel/…",
    rules=(
        UrlRule(_INSTAGRAM_HOSTS, re.compile(r"/(?:p|tv|reel|reels)/(?P<id>[A-Za-z0-9_-]+)/?(?:\?.*)?")),
        UrlRule(
            _INSTAGRAM_HOSTS,
            re.compile(r"/[A-Za-z0-9_.]+/(?:p|tv|reel|reels)/(?P<id>[A-Za-z0-9_-]+)/?(?:\?.*)?"),
        ),
    ),
)

FACEBOOK = VideoProvider(
    key="facebook",
    name="Facebook",
    example="https://www.facebook.com/watch/?v=…",
    rules=(
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/watch/?\?(?:[^&]+&)*v=(?P<id>\d+)(?:&.*)?")),
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/video\.php\?(?:[^&]+&)*v=(?P<id>\d+)(?:&.*)?")),
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/[A-Za-z0-9_.-]+/videos/(?P<id>\d+)/?(?:\?.*)?")),
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/[A-Za-z0-9_.-]+/videos/[A-Za-z0-9_-]+/(?P<id>\d+)/?(?:\?.*)?")),
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/reel/(?P<id>\d+)/?(?:\?.*)?")),
        UrlRule(_FACEBOOK_HOSTS, re.compile(r"/share/[a-z]/(?P<id>[A-Za-z0-9]+)/?(?:\?.*)?")),
        UrlRule(_FB_WATCH_HOSTS, re.compile(r"/(?P<id>[A-Za-z0-9_-]+)/?(?:\?.*)?")),
    ),
)

PROVIDERS: tuple[VideoProvider, ...] = (YOUTUBE, INSTAGRAM, FACEBOOK)


def _host_and_route(url: str) -> tuple[str, str] | None:
    """Split ``url`` into its lowercased hostname and its path-plus-query.

    A link typed without a scheme (``youtube.com/watch?v=…``) is read as https, which is
    what ``forms.URLField`` would have made of it anyway. Anything that is not http(s),
    or carries no host, returns None.
    """
    cleaned = url.strip()
    if not cleaned:
        return None
    if not _HAS_SCHEME.match(cleaned):
        cleaned = f"https://{cleaned}"
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    route = parts.path or "/"
    if parts.query:
        route = f"{route}?{parts.query}"
    return host, route


def recognize(url: str | None) -> VideoLink | None:
    """The registry's one question: which provider, if any, owns this URL?

    Returns None for a blank, malformed, or unsupported link — every caller treats
    that as "there is no video here".
    """
    if not url:
        return None
    split = _host_and_route(url)
    if split is None:
        return None
    host, route = split
    for provider in PROVIDERS:
        video_id = provider.match(host, route)
        if video_id is not None:
            return VideoLink(provider=provider, url=url.strip(), video_id=video_id)
    return None


def _joined(values: list[str]) -> str:
    """``a``, ``a or b``, ``a, b, or c`` — the provider list as a person would read it."""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} or {values[1]}"
    return f"{', '.join(values[:-1])}, or {values[-1]}"


def unsupported_video_message() -> str:
    """The refusal a member reads, naming every provider the registry supports."""
    names = _joined([provider.name for provider in PROVIDERS])
    examples = _joined([provider.example for provider in PROVIDERS])
    return f"Enter a {names} video link. For example {examples}"


def validate_video_url(url: str | None) -> str:
    """Return the stripped URL, raising ValidationError when no provider recognises it.

    Blank passes through: every field using this is optional.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if recognize(cleaned) is None:
        raise ValidationError(unsupported_video_message())
    return cleaned
