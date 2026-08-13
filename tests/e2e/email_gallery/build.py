"""Render every registered email and write the static gallery site.

``render_one`` dispatches on the entry's :class:`~tests.e2e.email_gallery.registry.Renderer`
and always uses the *real* send-path renderer, so each card is faithful to what
actually ships. ``build_site`` renders everything (printing a per-email ok/FAILED
line), **re-raises on any failure** — a render error fails the CI build red rather
than publishing a broken/blank card — and writes one self-contained ``index.html``.
"""

from __future__ import annotations

import html as html_mod
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tests.e2e.email_gallery import context as ctx_module
from tests.e2e.email_gallery.comment_widget import COPY_REVIEW_WIDGET  # TEMPORARY — remove on/after 2026-08-10
from tests.e2e.email_gallery.context import ORIENTATION_SUBJECTS, SampleData
from tests.e2e.email_gallery.registry import (
    NO_EMAIL_SECTION,
    OPT_IN_SECTION,
    SECTIONS,
    GalleryEmail,
    Renderer,
    assert_registry_complete,
    gallery_emails,
    no_email_events,
)


@dataclass(frozen=True)
class RenderedEmail:
    """One fully rendered gallery card."""

    email: GalleryEmail
    subject: str
    html: str  # "" for a plain-text-only email (no iframe)
    text: str


def _built_context(email: GalleryEmail, data: SampleData) -> dict[str, object]:
    if email.context_builder is None:
        raise ValueError(f"Gallery entry {email.key!r} names no context builder")
    builder = getattr(ctx_module, email.context_builder)
    built: dict[str, object] = builder(data)
    if email.key in ORIENTATION_SUBJECTS:
        built["subject"] = ORIENTATION_SUBJECTS[email.key].format(guild=data.guild.name)
    return built


def _render_spine(email: GalleryEmail) -> RenderedEmail:
    from core.events.copy import sample_context_for
    from core.events.registry import Channel, get_event
    from core.events.rendering import render_copy
    from core.events.templates import resolved_copy, wrap_email_html

    subject, body_text, body_html = resolved_copy(email.key, Channel.EMAIL)
    rendered = render_copy(
        subject=subject, body_text=body_text, body_html=body_html, context=sample_context_for(email.key)
    )
    html = wrap_email_html(rendered.body_html, shell=get_event(email.key).email_shell) if rendered.body_html else ""
    return RenderedEmail(email=email, subject=rendered.subject, html=html, text=rendered.body_text)


def _render_shell(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    from core.events.senders import email_shell_message

    built = _built_context(email, data)
    event_key = next(iter(email.event_keys)) if email.event_keys else email.key
    message = email_shell_message(
        event_key=event_key,
        subject=str(built["subject"]),
        text_template=email.text_template or "",
        html_template=email.html_template,
        template_context=dict(built["template_context"]),  # type: ignore[call-overload]
    )
    return RenderedEmail(email=email, subject=message.title, html=message.html_body or "", text=message.body)


def _render_welcome(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    from classes.emails import _welcome_email_bodies

    built = _built_context(email, data)
    offering = built["offering"]
    text, html = _welcome_email_bodies(
        offering,  # type: ignore[arg-type]
        greeting_name=str(built["greeting_name"]),
        self_serve_url=str(built["self_serve_url"]),
    )
    return RenderedEmail(email=email, subject=offering.welcome_email_subject, html=html, text=text)  # type: ignore[attr-defined]


def _render_release(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    from core.release_email import render_release_email

    built = _built_context(email, data)
    subject = str(built["subject"])
    html, text = render_release_email(
        str(built["version"]),
        subject=subject,
        preheader=str(built["preheader"]),
        intro=str(built["intro"]),
        cards=list(built["cards"]),  # type: ignore[call-overload]
    )
    return RenderedEmail(email=email, subject=subject, html=html, text=text)


def _render_announcement(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    from django.conf import settings

    from core.html_sanitize import rich_html_to_text
    from membership.models import build_announcement_email_html

    built = _built_context(email, data)
    title, body = str(built["title"]), str(built["body"])
    html = build_announcement_email_html(title, body)
    base_url = settings.MEMBER_BASE_URL
    text = f"{title}\n\n{rich_html_to_text(body)}\n\n{base_url}"  # mirrors AnnouncementDraft.build_email_message
    return RenderedEmail(email=email, subject=title, html=html, text=text)


def _render_allauth(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    from plfog.adapters import AdminRedirectAccountAdapter, _extract_bodies

    built = _built_context(email, data)
    adapter = AdminRedirectAccountAdapter()
    msg = adapter.render_mail(f"account/email/{email.template_prefix}", str(built["email"]), dict(built["context"]))  # type: ignore[call-overload]
    text, html = _extract_bodies(msg)
    return RenderedEmail(email=email, subject=msg.subject, html=html or "", text=text)


def _render_inline(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    built = _built_context(email, data)
    return RenderedEmail(email=email, subject=str(built["subject"]), html="", text=str(built["text_body"]))


def render_one(email: GalleryEmail, data: SampleData) -> RenderedEmail:
    """Render one gallery entry via its real send-path renderer."""
    if email.renderer is Renderer.SPINE_COPY:
        return _render_spine(email)
    if email.renderer is Renderer.SHELL_TEMPLATE:
        return _render_shell(email, data)
    if email.renderer is Renderer.WELCOME:
        return _render_welcome(email, data)
    if email.renderer is Renderer.RELEASE:
        return _render_release(email, data)
    if email.renderer is Renderer.ANNOUNCEMENT:
        return _render_announcement(email, data)
    if email.renderer is Renderer.ALLAUTH:
        return _render_allauth(email, data)
    if email.renderer is Renderer.INLINE_STRING:
        return _render_inline(email, data)
    raise ValueError(f"Unknown renderer {email.renderer!r}")  # pragma: no cover - enum is exhaustive above


def _git_sha() -> str:
    """The short commit SHA stamped in the header (CI fallback: GITHUB_SHA)."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return os.environ.get("GITHUB_SHA", "unknown")[:7]


def _esc(value: str) -> str:
    return html_mod.escape(value, quote=True)


def _card_html(rendered: RenderedEmail) -> str:
    """One card: name, inbox row, audience, trigger, channels, edit pointer, email, plain text."""
    email = rendered.email
    channels = _channels_label(email)
    body: str
    if rendered.html:
        body = (
            f'<iframe class="card-email" title="{_esc(email.name)}" sandbox="allow-same-origin" '
            f'srcdoc="{_esc(rendered.html)}" onload="fitFrame(this)"></iframe>'
            f'<details class="card-text"><summary>Plain-text version</summary>'
            f"<pre>{_esc(rendered.text)}</pre></details>"
        )
    else:
        body = (
            '<p class="card-note">This email is plain-text only — the text below is the whole message.</p>'
            f'<pre class="card-plain">{_esc(rendered.text)}</pre>'
        )
    return f"""<article class="card" id="{_esc(email.key)}" data-section-key="{_esc(email.key)}">
  <h3>{_esc(email.name)}</h3>
  <p class="inbox-row"><span class="from">From: Past Lives</span> · <strong>{_esc(rendered.subject)}</strong></p>
  <dl class="card-meta">
    <div><dt>Audience</dt><dd>{_esc(email.audience)}</dd></div>
    <div><dt>Trigger</dt><dd>{_esc(email.trigger_note)}</dd></div>
    <div><dt>Channels</dt><dd>{_esc(channels)}</dd></div>
    <div><dt>Where to edit</dt><dd>{_esc(email.edit_pointer)}</dd></div>
  </dl>
  {body}
</article>"""


def _channels_label(email: GalleryEmail) -> str:
    """The channels badge — the owning spine event's channel list, or plain email."""
    from core.events.registry import get_event

    if email.renderer is Renderer.SPINE_COPY:
        event = get_event(email.key)
        return ", ".join(c.value.replace("_", " ") for c in event.channel_list)
    return "email"


def _sidebar_html(counts: dict[str, int], no_email_count: int) -> str:
    items = [
        f'<li><a href="#section-{_esc(_slug(section))}">{_esc(section)} <span class="count">{counts.get(section, 0)}</span></a></li>'
        for section in SECTIONS
    ]
    items.append(
        f'<li><a href="#section-no-email">{_esc(NO_EMAIL_SECTION)} <span class="count">{no_email_count}</span></a></li>'
    )
    return "<ul>" + "".join(items) + "</ul>"


def _slug(section: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in section.lower()).strip("-")


_PAGE_CSS = """
  * { box-sizing: border-box; }
  body { font: 15px/1.55 system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fafaf8; }
  .layout { display: flex; gap: 32px; max-width: 1100px; margin: 0 auto; padding: 24px; }
  nav.toc { position: sticky; top: 24px; align-self: flex-start; flex: 0 0 220px; }
  nav.toc ul { list-style: none; margin: 0; padding: 0; }
  nav.toc li { margin: 0 0 6px; }
  nav.toc a { color: #1a4e77; text-decoration: none; font-size: 14px; }
  nav.toc a:hover { text-decoration: underline; }
  nav.toc .count { color: #888; font-size: 12px; }
  main { flex: 1 1 auto; min-width: 0; }
  h1 { margin: 0 0 4px; }
  .meta { color: #666; margin: 0 0 24px; font-size: 14px; }
  .changed-callout { background: #eef4fb; border: 1px solid #cfe0f2; border-left: 4px solid #0d4876;
    border-radius: 8px; padding: 16px 20px; margin: 0 0 36px; }
  .changed-callout h2 { margin: 0 0 8px; border: 0; padding: 0; font-size: 17px; color: #092e4c; }
  .changed-callout p { margin: 0 0 8px; font-size: 14px; color: #33424f; }
  .changed-callout ul { margin: 0; padding-left: 20px; font-size: 14px; color: #33424f; }
  .changed-callout li { margin: 0 0 5px; }
  h2 { margin: 48px 0 4px; border-bottom: 2px solid #ddd; padding-bottom: 6px; }
  h2:first-of-type { margin-top: 0; }
  .section-desc { color: #666; margin: 0 0 20px; font-size: 14px; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 20px; margin: 0 0 28px; }
  .card h3 { margin: 0 0 8px; }
  .inbox-row { margin: 0 0 12px; padding: 10px 12px; background: #f2f2ee; border-radius: 6px;
               font-size: 14px; overflow-wrap: anywhere; }
  .inbox-row .from { color: #666; }
  .card-meta { margin: 0 0 14px; font-size: 13.5px; }
  .card-meta div { margin: 0 0 4px; }
  .card-meta dt { display: inline; font-weight: 600; color: #444; }
  .card-meta dt::after { content: ": "; }
  .card-meta dd { display: inline; margin: 0; color: #555; }
  iframe.card-email { width: 100%; border: 1px solid #ccc; border-radius: 6px; display: block;
                      max-height: 900px; background: #eef0f3; }
  iframe.card-email.sized { max-height: none; }
  .card-text { margin-top: 10px; }
  .card-text summary { cursor: pointer; font-size: 13.5px; color: #1a4e77; }
  .card-text pre, .card-plain { background: #f6f6f2; border: 1px solid #e2e2dc; border-radius: 6px;
    padding: 12px; font-size: 13px; white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: auto; }
  .card-note { color: #666; font-size: 13.5px; margin: 0 0 8px; }
  .empty { color: #888; font-style: italic; }
  table.no-email { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.no-email th, table.no-email td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e5e5e0; }
  table.no-email th { color: #444; }
  @media (max-width: 700px) {
    .layout { flex-direction: column; gap: 12px; padding: 16px; }
    nav.toc { position: static; flex: none; overflow-x: auto; }
    nav.toc ul { display: flex; gap: 14px; white-space: nowrap; padding-bottom: 6px; }
    nav.toc li { margin: 0; }
  }
"""

# Same-origin srcdoc, so contentDocument is readable; with scripting off the CSS
# max-height fallback keeps every email visible with internal scroll.
_PAGE_JS = """
  function fitFrame(f) {
    try {
      var d = f.contentDocument;
      if (d && d.documentElement) {
        f.style.height = (d.documentElement.scrollHeight + 40) + 'px';
        f.classList.add('sized');
      }
    } catch (e) { /* fall back to the CSS max-height */ }
  }
"""

# The "what changed" highlight at the top of the page — the delta reviewers should look at
# this round (the email copy + design refresh, finished in the white-background rebrand).
_CHANGED_CALLOUT = """<section class="changed-callout" aria-label="What changed in the emails">
  <h2>What changed in the emails</h2>
  <p>Since the last copy review the emails got a fresh coat of paint and clearer wording. What to look at:</p>
  <ul>
    <li><strong>New white-background look.</strong> The old dark navy card is gone. Bodies are now
      dark slate text on a light card, with navy links and navy buttons (the gold accents were retired).</li>
    <li><strong>Friendlier, shorter wording, consistent name.</strong> Every email now says
      “Past Lives Makerspace,” and copy across dozens of emails was tightened: class confirmations,
      waitlist updates, orientations, tab and lease notices, and voting reminders.</li>
    <li><strong>Buttons instead of bare links</strong> where they matter: Find a Class, Find a Space,
      Activate your account, View guild page, and more.</li>
    <li><strong>New this release:</strong> announcements can be marked <em>urgent</em> (emailed even to
      members who opted out of that announcement’s email), transactional emails carry a filtering
      category header, and the opt-in notification emails below are shown here for the first time.</li>
  </ul>
</section>"""


_SECTION_DESCRIPTIONS: dict[str, str] = {
    OPT_IN_SECTION: (
        "Notification emails that are OFF by default — a member only gets them after opting in "
        "on their email settings. They used to be listed under “No email is sent,” which was "
        "wrong: an email IS sent to members who opt in. Each card shows the generic notification "
        "copy those members receive."
    ),
    "Classes": "Emails a class registrant receives, from sign-up through reminders.",
    "Teaching": "Emails to instructors and reviewers around the class workflow.",
    "Guilds & Orientations": "Guild life: orientations, joins, announcements, and Discord linking.",
    "Billing": "Tab receipts and charge notices.",
    "Membership & Account": "Invitations and account-level notices.",
    "Voting": "The monthly guild-funding vote cycle.",
    "Events": "Community Calendar proposal and publishing workflow.",
    "Announcements & Release": "Staff broadcasts: announcements and release updates.",
    "System/Auth": "Sign-in and account-recovery emails.",
}


def build_site(out_dir: Path, data: SampleData) -> list[RenderedEmail]:
    """Render every registered email and write ``index.html`` to ``out_dir``.

    Raises on the first render failure (after printing which email failed) — the
    site is never published with a broken or missing card.
    """
    from plfog.version import VERSION

    assert_registry_complete()
    out_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[RenderedEmail] = []
    for email in gallery_emails():
        try:
            rendered.append(render_one(email, data))
            print(f"  {email.key}: ok")
        except Exception:
            print(f"  {email.key}: FAILED")
            raise

    by_section: dict[str, list[RenderedEmail]] = {section: [] for section in SECTIONS}
    for item in rendered:
        by_section[item.email.section].append(item)
    no_email = no_email_events()

    sections_html: list[str] = []
    for section in SECTIONS:
        cards = by_section[section]
        body = "\n".join(_card_html(c) for c in cards) if cards else '<p class="empty">No emails in this group.</p>'
        sections_html.append(
            f'<h2 id="section-{_slug(section)}">{_esc(section)}</h2>'
            f'<p class="section-desc">{_esc(_SECTION_DESCRIPTIONS[section])}</p>\n{body}'
        )
    no_email_rows = "\n".join(
        f'<tr><td><strong>{_esc(event.label)}</strong><br><span style="color:#777">{_esc(event.key)}</span></td>'
        f"<td>{_esc(reason)}</td></tr>"
        for event, reason in no_email
    )
    sections_html.append(
        f'<h2 id="section-no-email">{_esc(NO_EMAIL_SECTION)}</h2>'
        '<p class="section-desc">Notification events that send no email at all — Discord and/or in-app '
        "only, with no email channel. (Opt-in emails, which do send when a member opts in, are now "
        "carded up top under “Opt-in notification emails.”) Listed so this index is provably complete.</p>"
        f'<table class="no-email"><tr><th>Event</th><th>Why there is no email card</th></tr>{no_email_rows}</table>'
        '<p class="section-desc" style="margin-top:20px">Not shown here: free-text “email the class” blasts '
        "(instructor/admin-authored each time — no standing copy), allauth's packaged templates for flows this "
        "app never uses, and per-recipient variants of a carded email (one representative sample renders).</p>"
    )

    counts = {section: len(by_section[section]) for section in SECTIONS}
    meta = (
        f"{len(rendered)} emails across {len(SECTIONS)} sections · built {date.today():%B %-d, %Y} · "
        f"v{VERSION} · {_git_sha()} · seeded sample data only."
    )
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Past Lives — email copy review</title>
<style>{_PAGE_CSS}</style>
<script>{_PAGE_JS}</script>
</head>
<body>
<div class="layout">
<nav class="toc" aria-label="Sections">
{_sidebar_html(counts, len(no_email))}
</nav>
<main>
<h1>Email copy review</h1>
<p class="meta">{_esc(meta)}</p>
{_CHANGED_CALLOUT}
{chr(10).join(sections_html)}
</main>
</div>
{COPY_REVIEW_WIDGET}
</body></html>
"""
    (out_dir / "index.html").write_text(doc, encoding="utf-8")
    return rendered
