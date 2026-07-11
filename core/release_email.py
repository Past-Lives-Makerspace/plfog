"""Renderer and helpers for the member-facing release-update email.

The release email is *derived data* — no DB model. It is assembled from three
sources:

- ``plfog/version.py::CHANGELOG`` — the structured feature list (title + bullets)
  for the current ``MAJOR.MINOR`` release line. Each entry may name an optional
  ``screenshot`` slug pointing at a :class:`FeaturePage`.
- :data:`FEATURE_PAGES` — the curated registry of member-hub pages we screenshot
  for release emails. It drives both the capture harness (what to shoot) and the
  composer's per-card screenshot ``<select>`` (what an admin can pick).
- the composer form — the admin's subject / preheader / intro plus per-card
  include-toggle and screenshot override.

:func:`render_release_email` assembles the hybrid HTML (brand hero band → light
feature cards → dark footer band) from the reusable inline-styled email partials
in ``templates/membership/emails/`` and a parallel plain-text body, so the
``.txt`` and ``.html`` parts stay in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe

from core.html_sanitize import render_rich_email_text, sanitize_rich_html

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# Where each feature screenshot lives in object storage (R2 in prod, the local
# filesystem in dev/CI). Stable key so the latest capture always sits at one URL.
_FEATURE_SHOT_PREFIX = "email/features"

# The hub home page ("Open Past Lives") — the release email's primary CTA target.
_CTA_LABEL = "Open Past Lives"


@dataclass(frozen=True)
class FeaturePage:
    """One member-hub page worth screenshotting for a release email.

    ``slug`` is the stable id used in a CHANGELOG entry's ``screenshot`` key, in the
    R2 object key, and as the composer ``<select>`` value. ``url_name`` (+ optional
    ``query``) is resolved lazily by :attr:`path` so the registry can be built at
    import time without depending on URLconf load order.
    """

    slug: str
    label: str
    url_name: str
    query: str = ""

    @property
    def path(self) -> str:
        """The hub path to screenshot / link to (e.g. ``/home/`` or ``/settings/?tab=guilds``)."""
        return f"{reverse(self.url_name)}{self.query}"


# Curated, expandable registry — the 3–5 highest-value member-hub pages that both
# screenshot well and back a real changelog feature. Grows as features ship (add a
# FeaturePage here, then seed its state in tests/e2e/screenshots_spec.py::_seed).
FEATURE_PAGES: list[FeaturePage] = [
    FeaturePage(slug="home", label="Member home dashboard", url_name="hub_home"),
    FeaturePage(slug="my-guilds", label="My Guilds settings", url_name="hub_user_settings", query="?tab=guilds"),
    FeaturePage(slug="org-info", label="Space & Org Info page", url_name="hub_org_info"),
    FeaturePage(slug="member-directory", label="Member directory", url_name="hub_member_directory"),
    FeaturePage(slug="guild-directory", label="Guilds directory", url_name="hub_guild_directory"),
    FeaturePage(slug="notifications", label="Notifications page", url_name="notification_list"),
    FeaturePage(slug="community-calendar", label="Community Calendar", url_name="hub_community_calendar"),
]


def _index_pages(pages: list[FeaturePage]) -> dict[str, FeaturePage]:
    """Index a registry by slug, failing loudly on a duplicate (a malformed registry)."""
    by_slug: dict[str, FeaturePage] = {}
    for page in pages:
        if page.slug in by_slug:
            raise ValueError(f"Duplicate FeaturePage slug: {page.slug!r}")
        by_slug[page.slug] = page
    return by_slug


# Built at import — a duplicate slug is a programming error and stops the app loudly.
_PAGES_BY_SLUG: dict[str, FeaturePage] = _index_pages(FEATURE_PAGES)


@dataclass
class Card:
    """One feature card in the assembled email (renderer input, in-memory).

    Mutable on purpose: the composer's per-card override sets ``included`` and swaps
    ``screenshot_url`` before render.
    """

    title: str
    bullets: list[str] = field(default_factory=list)
    slug: str = ""
    screenshot_url: str = ""
    feature_url: str = ""
    included: bool = True


def feature_shot_key(slug: str) -> str:
    """The object-storage key for a feature's screenshot (``email/features/<slug>.png``)."""
    return f"{_FEATURE_SHOT_PREFIX}/{slug}.png"


def resolve_feature_shot_url(slug: str) -> str:
    """The public URL of a feature's screenshot, or ``""`` when there is none.

    Returns a URL **only if the object actually exists** in storage, so an unknown
    slug *or* a known-but-not-yet-captured slug both resolve to ``""`` — the card then
    renders text-only and no broken image ever reaches a member. ``exists()`` on R2 is
    a HEAD request, so callers that resolve many slugs should cache per render.
    """
    if not slug:
        return ""
    key = feature_shot_key(slug)
    return default_storage.url(key) if default_storage.exists(key) else ""


def feature_page_url(slug: str) -> str:
    """The absolute member-site URL for a feature page, or ``""`` when the slug is unknown."""
    page = _PAGES_BY_SLUG.get(slug)
    return f"{settings.MEMBER_BASE_URL}{page.path}" if page is not None else ""


def feature_shot_choices() -> list[tuple[str, str]]:
    """``(value, label)`` choices for the composer's per-card screenshot ``<select>``.

    Offers only slugs whose R2 asset actually exists (the primary broken-image guard),
    plus a leading "No screenshot" option. A changelog default slug that hasn't been
    captured yet is simply absent, so the composer can't pick an image that renders as
    text-only.
    """
    choices: list[tuple[str, str]] = [("", "No screenshot")]
    for page in FEATURE_PAGES:
        if resolve_feature_shot_url(page.slug):
            choices.append((page.slug, page.label))
    return choices


# A ``--lines`` token must look like a bare ``MAJOR.MINOR`` (e.g. ``0.21``), not a full version.
_LINE_RE = re.compile(r"^\d+\.\d+$")


def _minor(version: str) -> str:
    """The ``MAJOR.MINOR`` line a version string belongs to (``"0.21.4"`` → ``"0.21"``)."""
    return ".".join(str(version).split(".")[:2])


def line_entries(lines: list[str]) -> list[dict[str, str | list[str]]]:
    """CHANGELOG entries whose ``MAJOR.MINOR`` line is in ``lines`` (newest first).

    Preserves CHANGELOG order (already newest-first), so an email spanning several
    lines interleaves them exactly as the changelog lists them. Generalizes
    :func:`current_line_entries` from one line to a set — so one email can cover the
    0.20 *and* 0.21 feature batches at once.
    """
    from plfog.version import CHANGELOG

    wanted = set(lines)
    return [e for e in CHANGELOG if _minor(str(e["version"])) in wanted]


def current_line_entries(version: str) -> list[dict[str, str | list[str]]]:
    """The CHANGELOG entries sharing ``version``'s ``MAJOR.MINOR`` line (newest first)."""
    return line_entries([_minor(version)])


def parse_lines(raw: str) -> list[str]:
    """Parse a ``--lines 0.20,0.21`` CLI value into ``["0.20", "0.21"]``.

    Each token must look like a ``MAJOR.MINOR`` line; a malformed token (or an empty
    list) raises :class:`ValueError` so the calling command fails loudly rather than
    silently sending an empty or wrong-scoped email.
    """
    parsed = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    if not parsed:
        raise ValueError("--lines must name at least one MAJOR.MINOR line, e.g. 0.20,0.21")
    for token in parsed:
        if not _LINE_RE.match(token):
            raise ValueError(f"--lines entries must look like MAJOR.MINOR (got {token!r})")
    return parsed


def build_release_cards(version: str, lines: list[str] | None = None) -> list[Card]:
    """One :class:`Card` per changelog entry in scope (newest first).

    Scope is ``version``'s current line by default (unchanged, backward-compatible);
    pass ``lines`` (e.g. ``["0.20", "0.21"]``) to span several release lines in one
    email. Each card's default screenshot comes from the entry's optional ``screenshot``
    slug; the title links to that feature's page when the slug is known. The composer
    later overrides ``included`` / ``screenshot_url`` per card.
    """
    cards: list[Card] = []
    entries = current_line_entries(version) if lines is None else line_entries(lines)
    for entry in entries:
        # ``screenshot`` is a genuinely optional, additive key — absent on every
        # legacy entry — so a default here is correct, not a silent-fallback bug.
        slug = str(entry.get("screenshot", ""))
        changes = entry["changes"]
        bullets = [str(c) for c in changes] if isinstance(changes, list) else [str(changes)]
        cards.append(
            Card(
                title=str(entry["title"]),
                bullets=bullets,
                slug=slug,
                screenshot_url=resolve_feature_shot_url(slug),
                feature_url=feature_page_url(slug),
            )
        )
    return cards


def _release_cta_url() -> str:
    """Absolute URL for the "Open Past Lives" button — the hub home."""
    return f"{settings.MEMBER_BASE_URL}{reverse('hub_home')}"


def _release_text(subject: str, intro: str, cards: list[Card], cta_url: str) -> str:
    """The plain-text (.txt) part, kept in sync with the HTML: subject, intro, cards, CTA, footer."""
    lines: list[str] = [subject, ""]
    intro_text = render_rich_email_text(intro) if intro else ""
    if intro_text:
        lines += [intro_text, ""]
    for card in cards:
        lines.append(f"## {card.title}")
        lines += [f"• {bullet}" for bullet in card.bullets]
        lines.append("")
    lines += [f"{_CTA_LABEL}: {cta_url}", ""]
    lines.append("You're getting this because you have a Past Lives account.")
    lines.append(f"Manage your email preferences or unsubscribe: {settings.MEMBER_BASE_URL}/settings/")
    return "\n".join(lines)


def render_release_email(
    version: str,
    *,
    subject: str,
    preheader: str,
    intro: str,
    cards: list[Card],
    lines: list[str] | None = None,
) -> tuple[str, str]:
    """Assemble the hybrid release email — returns ``(html, text)``.

    Only ``included`` cards render. ``intro`` is sanitized rich HTML shown on the light
    body; the hero band's version badge + date come from the release line by default, or
    from the NEWEST entry across ``lines`` when spanning several lines (so a 0.20+0.21
    email still reads "v0.21" with the latest date). The ``.txt`` part mirrors the HTML
    so the two never drift.
    """
    entries = current_line_entries(version) if lines is None else line_entries(lines)
    # Version badge: the current line by default; when spanning lines, the NEWEST
    # selected entry (CHANGELOG is newest-first) drives it — a 0.20+0.21 email reads "v0.21".
    badge_version = version if lines is None else (str(entries[0]["version"]) if entries else version)
    minor_label = "v" + _minor(badge_version)
    release_date = str(entries[0]["date"]) if entries else ""
    included = [c for c in cards if c.included]
    cta_url = _release_cta_url()
    intro_html = sanitize_rich_html(intro) if intro else ""
    html = render_to_string(
        "membership/emails/_release_shell.html",
        {
            "preheader": preheader,
            "minor_label": minor_label,
            "release_date": release_date,
            "intro_html": mark_safe(intro_html),  # allowlisted, sanitized above
            "cards": included,
            "cta_url": cta_url,
            "cta_label": _CTA_LABEL,
        },
    )
    text = _release_text(subject, intro, included, cta_url)
    # Coerce SafeString → plain str so the admin preview's srcdoc="{{ html }}"
    # attribute-escapes it (same reason as core.events.templates.wrap_email_html).
    return "" + html, text


def save_feature_shot(slug: str, png_bytes: bytes) -> str:
    """Upload a captured screenshot to storage at its stable key; return its public URL.

    Overwrites in place (delete-then-save) so the latest feature state always sits at
    one URL — R2's ``file_overwrite=False`` would otherwise suffix a duplicate name.
    """
    from django.core.files.base import ContentFile

    key = feature_shot_key(slug)
    if default_storage.exists(key):
        default_storage.delete(key)
    default_storage.save(key, ContentFile(png_bytes))
    return default_storage.url(key)


def send_release_test(admin_user: User, html: str, text: str, subject: str) -> None:
    """Send the assembled release email to ONLY the admin's own inbox (a test).

    A direct :func:`core.email.send` — deliberately **not** the notification spine. The
    real send emits ``site_announcement`` with a period; routing a test through ``emit``
    would consume/dedupe that slot and could block the real send. A test is a plain
    transactional send to one inbox: no spine, no ``EventDelivery``, no dedup.
    """
    from core.email import send

    send(
        to=admin_user.email,
        subject=subject,
        trigger_kind="release_email.test",
        text_body=text,
        html_body=html,
        best_effort=True,
    )
