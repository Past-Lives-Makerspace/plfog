# Branded notification-spine emails — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-25
**Surface:** Email (recipient's mail client) + admin notifications copy editor (`pastlives.test` → Notifications).
**Related:** `docs/superpowers/plans/2026-06-21-instructor-welcome-email.md` (reuses the same `_base.html` shell idea); the notification-spine phases (registry / emit / copy / channels).

---

## 1. Overview

The notification spine (`core.events.emit`) is the single send point for every event-driven
email. Today, the emails it sends from **DB-editable copy** — most visibly the **member invite**
— arrive as a bare stack of `<p>` tags with no Past Lives branding: no logo, no navy card, no
footer, no gold button. Meanwhile every *legacy* email (orientation, billing, class confirmation,
login codes, new-login alert) already renders inside a polished branded shell
(`templates/membership/emails/_base.html`) and looks sharp.

This feature makes the spine **reuse that exact shell** for its copy-mode emails. After the change,
a spine email — invite included — lands in the inbox as the same dark-navy branded card the rest of
the app sends, with a real gold CTA button on the invite. Nothing about the legacy emails changes,
and the copy team keeps editing the same inner copy fragment they edit today.

### Locked decisions

| Decision | Choice |
|---|---|
| Which emails get wrapped | **All** notification-spine emails sent on `Channel.EMAIL` (and `Channel.SCHEDULED_EMAIL`) that render from DB/seeded copy — one reusable wrapper, not invite-only. |
| Reuse vs new brand | **Reuse** `templates/membership/emails/_base.html` verbatim. This spec invents no brand. |
| Where the wrap happens | At **copy render time** in `core.events.templates.rendered_message`, scoped to email channels — *not* in `EmailAdapter`. (See §4 for why this is the only correct choke point.) |
| Invite CTA | The invite's `<a>` becomes a **gold button** by carrying the same inline button styling the legacy CTAs already use (`orientation_confirmed.html`). No new "structured CTA" concept in v1. |
| Plain-text body | **Unchanged** — only the HTML alternative is wrapped. |
| Admin preview | Copy editor keeps editing the inner fragment; the live preview is updated to additionally show the **wrapped** result in an iframe so the copy team isn't editing blind to the brand. |

---

## 2. The problem — unbranded spine vs branded legacy

There are two families of email in the app, and only one is branded:

**Branded (legacy / structural).** A sender renders a *complete HTML document* that extends or
mirrors `_base.html`, then hands the rendered string to the spine as a per-channel override or as an
explicit `html_body`. Examples:
- Orientation emails — `membership/orientations.py:159` calls `emit_with_email_shell(...)` with
  `html_template="membership/emails/orientation_confirmed.html"` (which `{% extends %}` `_base.html`
  at line 1).
- New-login alert — `core/signals.py:28-40` renders the standalone full document
  `core/email/new_login.html` and passes it as `emit("new_login", ..., html_body=html_body)`.
- Classes / billing — `classes/emails.py`, `billing/notifications.py` all go through
  `email_shell_message` (`core/events/senders.py:40-72`).

These are already correct. They must stay byte-for-byte identical.

**Unbranded (copy-mode / spine-rendered).** When a caller passes *no* explicit body and *no*
override, `emit()` renders each channel's message from the DB-editable copy catalogue via
`core.events.templates.rendered_message` → `core.events.rendering.render_html`. That produces a bare
HTML **fragment**, e.g. `member.invited` (`core/events/copy.py:384-388`):

```html
<p>You've been invited to join <strong>Past Lives Makerspace</strong>!</p>
<p><a href="{{ signup_url }}">Create your account</a></p>
<p>If you didn't expect this invite, you can ignore this email.</p>
```

That fragment is handed straight to `core.email.send` with no `<html>`, no card, no logo, no footer,
no styled button. So the invite — and every other copy-mode spine email (voting reminders, funding
results, release announcements, and the generated long-tail copy) — arrives unbranded.

The fix is to wrap that fragment in `_base.html` at send time, and *only* that fragment.

---

## 3. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Branded shell (navy card + logo + footer, `{% block content %}`) | `_base.html` | `templates/membership/emails/_base.html:1-33` (card td + content block at `:18-19`) |
| Example: a template that extends the shell | `orientation_confirmed.html` | `templates/membership/emails/orientation_confirmed.html:1` (`{% extends %}`) |
| Example: gold CTA **button** markup (inline-styled `<a>`) | same file | `templates/membership/emails/orientation_confirmed.html:8-9` |
| Example: a standalone full branded document | `new_login.html` | `templates/core/email/new_login.html:1-47` (gold CTA at `:31`) |
| Copy-mode message build (the fragment we must wrap) | `rendered_message` | `core/events/templates.py:66-83` |
| Raw copy resolution (DB row → seeded default) | `resolved_copy` / `rendered_copy` | `core/events/templates.py:47-63` |
| Safe merge-field substitution (autoescapes values, trusts literal) | `render_html` | `core/events/rendering.py:67-83` |
| The email choke-point (send + `TransactionalEmailLog`) | `core.email.send` | `core/email.py:56-121` |
| Per-recipient email send | `EmailAdapter.deliver` | `core/events/channels.py:120-142` |
| **Explicit-address email send (the invite's actual send path)** | `_explicit_email_fan_out` → `send_email` | `core/events/emit.py:228-269` |
| Copy-mode vs override decision (`message_for`) | `emit` | `core/events/emit.py:117-127` |
| Structural-shell override path (NOT to be wrapped) | `email_shell_message` / `emit_with_email_shell` | `core/events/senders.py:40-149` |
| The invite copy fragment + placeholders | `member.invited` `EventCopy` | `core/events/copy.py:369-391` |
| The invite emit call (`email_to` + `signup_url`) | `Invite.send_invite_email` | `core/models.py:350-381` |
| The invite event def (forced email-only) | `MEMBER_INVITED` | `core/events/registry.py:310-333` |
| Admin live preview (renders inner fragment today) | `preview_copy` | `hub/notification_views.py:222-246`; partial `templates/hub/admin/notifications/_preview.html:40-49` |

**Gap to close (small):** one thin wrapper template + one `wrap_email_html()` helper, called from
`rendered_message` for email channels, plus the invite CTA restyle and the preview update.

---

## 4. Approach — wrap mechanism + the no-double-wrap proof

### 4.1 The wrapper

Add a thin layout template that drops a fragment into the existing shell's content block:

```django
{# templates/membership/emails/notification_shell.html #}
{% extends "membership/emails/_base.html" %}
{% block content %}{{ body_html|safe }}{% endblock %}
```

And a one-line helper (in `core/events/templates.py`, beside `rendered_message`):

```python
def wrap_email_html(fragment: str) -> str:
    """Render a copy-mode HTML fragment inside the branded email shell."""
    return render_to_string("membership/emails/notification_shell.html", {"body_html": fragment})
```

`fragment` is the output of `render_html` — the trusted admin-authored literal with each interpolated
**value already HTML-escaped** (`core/events/rendering.py:77-83`). `|safe` therefore re-escapes
nothing and the escaping guarantee is preserved end-to-end: the shell is trusted, the merge values
stay escaped.

### 4.2 The single choke point: `rendered_message`, scoped to email channels

Wrap inside `rendered_message` (`core/events/templates.py:66-83`), only when the channel is an email
channel and the body is non-empty:

```python
def rendered_message(event_key, channel, context, *, url=""):
    rendered = rendered_copy(event_key, channel, context)
    html = rendered.body_html or None
    if html and channel in (Channel.EMAIL, Channel.SCHEDULED_EMAIL):
        html = wrap_email_html(html)
    return Message(title=rendered.subject, body=rendered.body_text, url=url,
                   html_body=html, trigger_kind=event_key)
```

- In-app rows ignore `html_body` (they use `body`), so they're unaffected — but we scope the wrap to
  email channels anyway so an in-app/Discord Message never carries a full email document.
- Discord's copy falls back to the email copy (`copy.py` `copy_for`), but `DiscordAdapter.broadcast`
  builds its embed from `title`+`body` (text) and never reads `html_body`
  (`core/events/channels.py:302-314`) — so leaving Discord's `html_body` unwrapped is harmless and
  correct.

### 4.3 Why `rendered_message` — and **not** `EmailAdapter` — is the only correct point

This is the single most important design call. Two distinct code paths actually put a spine email on
the wire, and **the invite uses the one that bypasses `EmailAdapter` entirely**:

1. **Per-recipient** — `EmailAdapter.deliver` (`core/events/channels.py:131-142`), for events that
   resolve to logged-in users.
2. **Explicit address** — `_explicit_email_fan_out` (`core/events/emit.py:228-269`), which calls
   `core.email.send` **directly**, used whenever `emit(..., email_to=...)` is set.

`Invite.send_invite_email` (`core/models.py:375-381`) calls `emit("member.invited", email_to=self.email, ...)`.
Because the invitee has no account, the `member.invited` event is email-only with no per-user
recipient — its email is sent by **path 2**, never touching `EmailAdapter`. **If we wrapped in
`EmailAdapter.deliver`, the headline use case — the invite — would still arrive unbranded.** That
alone rules out the adapter as the choke point.

Both paths source their HTML from the same place: `message_for(channel)` in `emit`
(`core/events/emit.py:122-127`). Wrapping inside `rendered_message` (which `message_for` calls in
copy mode) covers **both** email paths with one change.

### 4.4 The no-double-wrap proof

`message_for(channel)` (`core/events/emit.py:122-127`) resolves an email's HTML by exactly one of
three branches, and only the third ever reaches `rendered_message`:

| Branch | When | HTML it carries | Calls `rendered_message`? | Wrapped? |
|---|---|---|---|---|
| `channel_messages[channel]` (override) | sender passed `messages={Channel.EMAIL: ...}` via `emit_with_email_shell` (`senders.py:135`) | **full document** from `email_shell_message` (already extends `_base.html`) | **No** — returned directly | No (correct) |
| `fixed_message` | sender passed explicit `title`/`body`/`html_body` to `emit` (e.g. new-login, `signals.py:32-40`) | **full document** from `render_to_string("core/email/new_login.html")` | **No** — returned directly | No (correct) |
| `templates.rendered_message(...)` | copy mode: no override, no explicit body (invite, voting, release, generic) | **bare fragment** from `render_html` | **Yes** | **Yes** (the fix) |

The two branches that carry an already-complete branded document **never call
`rendered_message`** — they are returned verbatim by `message_for`. So the wrap, living *inside*
`rendered_message`, is structurally unreachable for them. A card-inside-a-card is impossible by
construction, not by a content sniff. The trace, end to end:

- **Legacy orientation/class/billing email** → `emit_with_email_shell` → `messages[EMAIL]` =
  full doc → `message_for` returns it → EmailAdapter/explicit send → **already branded, untouched.**
- **Legacy new-login email** → `emit(..., html_body=full_doc)` → `fixed_message` → `message_for`
  returns it → **already branded, untouched.**
- **Invite (and every copy-mode email)** → `emit(...)` with no override/body → `message_for` →
  `rendered_message` → **fragment wrapped in `_base.html`** → explicit/per-recipient send → branded.

### 4.5 The invite CTA — the simplest thing that yields a real button

Once wrapped, the invite's `<a>` is a plain hyperlink on the navy card. To make it the gold button,
restyle the `<a>` in the `member.invited` copy fragment (`core/events/copy.py:384-388`) to carry the
same inline button styling the legacy CTAs already use (`orientation_confirmed.html:9`):

```html
<p>You've been invited to join <strong>Past Lives Makerspace</strong>!</p>
<div style="text-align:center; margin:24px 0 0;">
  <a href="{{ signup_url }}" style="display:inline-block; padding:12px 32px; background-color:#EEB44B; color:#092E4C; font-size:14px; font-weight:700; text-decoration:none; border-radius:6px;">Create your account</a>
</div>
<p style="margin:16px 0 0; font-size:13px; color:#96ACBB;">If you didn't expect this invite, you can ignore this email.</p>
```

This needs **no new spine concept** — the button is ordinary trusted literal markup in the copy
fragment, exactly like the `<strong>` already there, and `{{ signup_url }}` keeps flowing through the
autoescaping renderer. (The same gold-button snippet can be applied to the other curated copy CTAs —
voting, release — as a follow-on; it is the same one-line change per fragment.)

**The branded shell ships immediately; the button does not — it is gated on re-seeding.** This is a
crucial deploy nuance, because the two halves of the fix travel by different routes:

- The **shell wrap** (§4.1–4.2) lives in code (`rendered_message`) and wraps *whatever* HTML
  `resolved_copy` returns — so every spine email is branded the moment the code deploys, regardless
  of DB state.
- The **gold button** is a *fragment-level edit in `copy.py`*, which is only the **fallback** copy.
  `resolved_copy` (`core/events/templates.py:53-57`) returns the admin-edited `NotificationTemplate`
  **DB row first**, and `member.invited` IS in `seedable_rows()` (`copy.py:544-556`), so on a seeded
  prod the DB row — carrying the *old* plain-link fragment — shadows the new `copy.py` button until
  `python manage.py seed_notification_templates` re-runs and refreshes the un-overridden row. A fresh
  test DB has no seeded row, so an e2e hits the `copy.py` fallback and goes green **while seeded prod
  still ships a plain link** — false confidence. The test plan (§7) and done checklist (§9) call out
  asserting the button on a **seeded + refreshed** row, not only the fallback.
- For an **admin-overridden** row (hand-edited copy), `seed_notification_templates` intentionally
  does **not** touch it — so the button **never** arrives via the fragment edit there. The only fix
  for the overridden case is the deferred structured-CTA concept below. This is an accepted v1
  limitation: an admin who hand-edited the invite copy keeps their plain link (still branded by the
  shell) until they re-add a button themselves or we ship structured CTAs.

**Considered and deferred:** a structured "primary CTA" concept (`cta_label` + `cta_url` on
`EventCopy`, rendered as a button by the shell so the DB copy — seeded *or* overridden — can't lack
the button). It is more robust (and the *only* way the button reaches an overridden row) but adds
machinery to `copy.py`, `rendering.py`, and the shell. Not needed for v1; revisit if admins editing
copy break/lack the inline button. Noted in §8.

### 4.6 The admin preview

`preview_copy` (`hub/notification_views.py:222-246`) renders the **inner fragment** today and shows
it on a white "email client" canvas (`_preview.html:24-28`). Keep that panel as the copy-editing aid
(it's the thing the admin is actually editing). Additionally, for the email channel, compute the
**wrapped** HTML with the same `wrap_email_html()` helper and render it inside an
`<iframe srcdoc="...">` so the full document (its own dark background + navy card) renders honestly —
otherwise the copy team edits blind to the brand and to the card-on-dark contrast. The iframe is the
only correct way to show a full `<html>` document inside the admin page without its `<body>`/table
bleeding into the hub layout. This is a small, additive change to the view (pass `wrapped_html`) and
to `_preview.html` (an iframe block under the existing rendered-HTML panel).

> **FOOTGUN — the `srcdoc` value must be `{{ wrapped_html }}`, NEVER `{{ wrapped_html|safe }}`.**
> The sibling rendered-HTML panel uses `{{ rendered.body_html|safe }}` (`_preview.html:44`) because
> it injects markup *into the DOM*; a builder will reflexively copy that `|safe` onto the iframe and
> break it. `srcdoc` is an HTML **attribute**, so the value must be **attribute-escaped** — Django's
> default autoescape turns the document's inline-style `"` into `&quot;` so the attribute doesn't
> terminate at the first quote (and closes the injection vector). `wrapped_html` is a plain `str` from
> `render_to_string` (not a `SafeString`), so plain `{{ wrapped_html }}` does exactly the right thing.
> Using `|safe` here would emit raw quotes, truncate the attribute at the first inline style, and
> render a broken/empty iframe.

---

## 5. Changes by file

| File | Change |
|---|---|
| `templates/membership/emails/notification_shell.html` | **New.** Thin template: `{% extends "membership/emails/_base.html" %}` + `{% block content %}{{ body_html|safe }}{% endblock %}`. |
| `core/events/templates.py` | Add `wrap_email_html(fragment) -> str` (uses `render_to_string`). In `rendered_message`, wrap `body_html` when `channel in (Channel.EMAIL, Channel.SCHEDULED_EMAIL)` and the body is non-empty. Import `render_to_string`. |
| `core/events/copy.py` | Restyle the `member.invited` HTML body's `<a>` into the inline gold-button block (§4.5). Subject/text body unchanged. Placeholders/sample context unchanged (`signup_url` already present, `copy.py:370-373`). |
| `hub/notification_views.py` | In `preview_copy`, for the email channel also compute `wrap_email_html(rendered.body_html)` and pass it as `wrapped_html` to the template. |
| `templates/hub/admin/notifications/_preview.html` | Under the existing "HTML body (rendered)" panel, add an `<iframe srcdoc="{{ wrapped_html }}">` "as it arrives (branded)" preview for the email channel. **Plain `{{ wrapped_html }}` — no `|safe` (§4.6 footgun).** |
| **Deploy step (not a file edit)** | Re-run `python manage.py seed_notification_templates` post-deploy so the refreshed `copy.py` invite fragment (the gold button) reaches the seeded `member.invited` DB row. The shell wrap needs no seed; the button does (§4.5). Admin-overridden rows are deliberately not refreshed and keep their existing copy. |
| `plfog/version.py` | **At build time only** — bump `VERSION` + one member-friendly `CHANGELOG` entry (e.g. "Invitation and notification emails now arrive with the full Past Lives look — logo, card, and a clear button"). Not part of this spec. |

No change to `core/email.py`, `core/events/channels.py` (`EmailAdapter`), `core/events/emit.py`,
`core/events/senders.py`, `core/events/rendering.py`, or any legacy email template.

---

## 6. UI / UX — email anatomy (apply the completeness checklist to the rendered email)

> **Email is the exception to FRONTEND.md's web rules. State this so a reviewer doesn't "fix" it
> wrongly:**
> - **Inline styles + table layout are CORRECT here.** `_base.html` is table-based with inline styles
>   *because* mail clients (Outlook, Gmail) strip `<style>`/external CSS and ignore flexbox/grid. Do
>   **not** apply the no-inline-style `pl-` web rule to the email body.
> - **No theme tokens / CSS variables.** The email renders in the recipient's mail client, not on the
>   site, so `--hub-*` tokens and the dark/light `[data-theme]` switch do **not** apply. The shell
>   uses fixed, hand-tuned colors (`#12121f` page, `#092E4C` card, `#F4EFDD` text, `#EEB44B` gold).
>   Don't introduce a CSS variable into the email.

### Anatomy of the wrapped invite email (top → bottom)

- **Header / logo:** centered "Past Lives" wordmark (`_base.html:13-15`), on the deep `#12121f` page
  background.
- **Card:** the navy `#092E4C` rounded card, 40px/32px padding (`_base.html:18`). The invite fragment
  renders inside it.
- **Body copy:** "You've been invited to join **Past Lives Makerspace**!" — the trusted literal; the
  `<strong>` survives, merge values stay escaped.
- **Gold CTA button:** centered `#EEB44B` button, dark navy text, 12×32 padding, 6px radius, linking
  to `{{ signup_url }}` (§4.5) — a real tappable button, not a text link.
- **Secondary line:** the muted "If you didn't expect this invite…" note.
- **Footer:** "Past Lives Makerspace" + "Do It Together" (`_base.html:22-27`).

### States

- **Happy path:** fragment + CTA wrapped → branded card with button. (See §4.5.)
- **Missing CTA url (fallback):** if `signup_url` is absent from context, `render_html` substitutes
  the visible marker `[missing: signup_url]` as the (escaped) `href` (`rendering.py:80-81`). The
  button still renders inside the branded card; the link is visibly broken rather than silently
  blank — the intended fail-loud behavior. The wrap never raises on this.
- **Fragment with no `<a>` (generic long-tail copy):** the wrapped email is the branded card with
  body text and **no button**. Acceptable; the generic copy carries no CTA by design (`copy.py`
  `_generic_copy`).
- **Empty HTML body:** `rendered_message` leaves `html_body=None` (no wrap), so the email sends as
  text-only exactly as today — the shell is never rendered around nothing.

### Mobile mail clients

- `_base.html` is a single 480px-max centered column with `width:100%` (`:11`) — it reflows to a
  single column on a phone, no horizontal scroll.
- The CTA is a padded block-level `<a>` (~40px tall: 14px text + 12px top/bottom padding) — inherited
  from the legacy buttons (`orientation_confirmed.html`, `new_login.html`), an acceptable, already-
  shipped tap target.
- The 8px-grid spacing in the shell holds at any width.

### Light vs dark mail-client backgrounds

The shell paints its **own** page background (`#12121f`) and card (`#092E4C`), so it looks identical
whether the client's chrome is light or dark — the card is self-contained and never relies on the
client's background. **Risk to note:** some clients (notably Gmail dark mode) auto-invert email
colors; because our palette is already dark-on-dark with light text, the result stays legible, but
the spec flags this as the one place to eyeball in a real client. This matches the legacy emails'
behavior exactly — we are inheriting their tradeoffs, not adding new ones.

### Admin preview screen (the one in-app UI this touches)

- **Screen:** `templates/hub/admin/notifications/_preview.html`, swapped into the edit page
  (`edit_copy.html`) via HTMX on input.
- **Components / theme:** the editor itself is a normal hub page — `form_field.html` fields, hub
  tokens, both themes already handled (`edit_copy.html:36-41`). The HTML-body panels intentionally
  use fixed email colors on a white/dark canvas, not theme tokens (already the case at
  `_preview.html:22-28`).
- **New control:** under the existing "HTML body (rendered)" fragment panel, an **"As it arrives
  (branded)"** `<iframe srcdoc>` panel showing the wrapped result. The copy editor still types into
  the same `body_html` field (the inner fragment); the iframe is read-only output. No new form, no
  new Save — the existing "Save copy" button (`edit_copy.html:46`) is unchanged.
- **Empty state:** before typing, the existing "Start typing — the preview renders here" message
  (`_preview.html:51`) still shows; the iframe panel appears once there's HTML.

---

## 7. Tests

BDD `*_spec.py`, `describe_*` / `it_*` only (`context_*` is **not** collected), factory-boy, run in
the `plfog-web` Docker image, ≥98% gate, full type hints. Extend the existing
`tests/core/events/` specs and the notification view specs.

**Wrap helper + `rendered_message` (`tests/core/events/templates_spec.py`):**
- `wrap_email_html` output **contains the brand markers** — the "Past Lives" wordmark, the `#092E4C`
  card color, and the footer "Do It Together" — **and** the passed fragment content.
- `rendered_message` for `Channel.EMAIL` returns an `html_body` that is the **wrapped** document
  (contains both the brand markers and the rendered fragment).
- `rendered_message` for `Channel.SCHEDULED_EMAIL` is **also** wrapped.
- `rendered_message` for `Channel.IN_APP` / `Channel.DISCORD` is **not** wrapped (no `<html>` /
  card markers); in-app `body` text is unchanged.
- Empty `body_html` → `html_body is None` (no shell rendered around nothing).
- **Merge values stay autoescaped after wrapping:** render a fragment whose context value contains
  `<script>` / `&` and assert the value is escaped in the final wrapped HTML while the trusted
  literal markup (and the card) is intact.

> **Assert email bodies on `django.core.mail.outbox`, NOT `TransactionalEmailLog`.** The log row
> persists only `to_email` / `subject` / `trigger_kind` / `status` / `error_message` / `created_at`
> (`core/models.py:529-534`) — `core.email.send` never stores the body. The locmem backend captures
> the actual message in `mail.outbox`; the wrapped HTML alternative is
> `mail.outbox[0].alternatives[0][0]` (Django's `send_mail(html_message=...)` and
> `EmailMultiAlternatives` both populate `.alternatives`). Use `TransactionalEmailLog` only to assert
> the send *happened* (`status=SENT`, the `trigger_kind`, the recipient).

**Invite end-to-end (`tests/core/events/` or `tests/core/invite_email_spec.py`):**
- Emitting `member.invited` (via `Invite.send_invite_email`, with a seeded `MembershipPlan`) yields a
  message in `mail.outbox` whose **HTML alternative is wrapped** (shell-only markers present — see the
  marker note below) and the corresponding `TransactionalEmailLog` row is `SENT` with
  `trigger_kind="member.invited"` and the invitee address.
- The send goes through the **explicit-address path** (`email_to`) and is still wrapped — the
  regression guard for §4.3 (proving the wrap doesn't depend on `EmailAdapter`).
- The plain-text alternative (`mail.outbox[0].body`) is **unchanged** (no HTML, no shell).
- **Gold CTA on a SEEDED + refreshed row, not only the fallback** (guards §4.5): seed the DB
  (`seed_notification_templates`) — the freshly seeded `member.invited` email row carries the new
  `copy.py` fragment — then emit and assert the outbox HTML contains the **styled gold CTA**
  (`#EEB44B` button styling) pointing at `signup_url`. Asserting the button *only* on the unseeded
  `copy.py` fallback would pass on a fresh test DB while seeded prod shipped a plain link.
- (Optional, documents the v1 limitation) An **overridden** `member.invited` email row with an
  old plain-link body: after re-seed it is still wrapped by the shell but the CTA is **still a plain
  link** (the fragment edit doesn't reach overridden rows).

> **Marker choice for shell-presence / double-wrap asserts:** use a **shell-only** string — the
> footer `"Do It Together"` or the card `<td>`'s `#092E4C` background — **not** the `"Past Lives"`
> wordmark, which also appears in the invite *body* text and would false-positive.

**No-double-wrap guards (`tests/core/events/emit_spec.py` / `senders_spec.py`):**
- An `emit_with_email_shell(...)` send (override path) yields an HTML alternative that is the
  structural template's document and is **not** double-wrapped — assert the **footer `"Do It
  Together"` (or the `#092E4C` card `<td>`) appears exactly once**, so there is no card-inside-a-card.
- An `emit(..., html_body=<full doc>)` send (fixed-message path, e.g. new-login) is **not** wrapped
  (the passed document is delivered verbatim; the shell-only marker count is unchanged).

**Admin preview (`tests/hub/notification_*_spec.py`):**
- `preview_copy` for the email channel returns context with `wrapped_html` containing the brand
  markers + the fragment; for a non-email channel it does not.
- The rendered partial includes the iframe panel for the email channel and omits it otherwise.

---

## 8. Out of scope

- A structured "primary CTA" (`cta_label`/`cta_url`) concept on `EventCopy` — deferred (§4.5);
  the inline-styled `<a>` covers v1. **Consequence:** an admin-overridden `member.invited` copy row
  keeps its existing (plain-link) body after re-seed — it is still branded by the shell but gets no
  button until structured CTAs ship. Accepted v1 limitation.
- Any change to legacy/structural emails (orientation, classes, billing, login codes, new-login) —
  they are already branded and must stay byte-identical.
- Changes to `core.email.send`, `EmailAdapter`, `emit`, `senders`, or `render_html` themselves.
- Per-event subject prefixes, unsubscribe-footer logic, plain-text restyling, or a dark/light email
  variant — none are part of "reuse the existing shell."
- Discord embed appearance (it reads `title`/`body`, never the HTML).
- The version bump + changelog — done **at build time**, one entry per PR, per CLAUDE.md.

---

## 9. Done checklist

- [ ] `templates/membership/emails/notification_shell.html` added; renders a fragment inside
      `_base.html`'s content block with `|safe` (no re-escaping).
- [ ] `wrap_email_html()` added in `core/events/templates.py`; `rendered_message` wraps `html_body`
      for `Channel.EMAIL` / `Channel.SCHEDULED_EMAIL` only, and only when non-empty.
- [ ] `member.invited` copy fragment restyled to a gold CTA button (inline styles, matching
      `orientation_confirmed.html`); subject/text body and placeholders unchanged.
- [ ] `preview_copy` + `_preview.html` show the wrapped result in an iframe for the email channel;
      `srcdoc` uses plain `{{ wrapped_html }}` (**no `|safe`** — §4.6); copy editor still edits the
      inner fragment; "Save copy" unchanged.
- [ ] **No-double-wrap verified:** override-path (`emit_with_email_shell`) and fixed-message-path
      (new-login) emails are unchanged — the shell-only marker (footer "Do It Together" / `#092E4C`
      card `<td>`, **not** the "Past Lives" wordmark) appears exactly once.
- [ ] Email-body asserts read `mail.outbox[…].alternatives[0][0]` (the HTML), not
      `TransactionalEmailLog` (which stores no body); the log is asserted only for `SENT` /
      `trigger_kind` / recipient.
- [ ] Invite email verified wrapped **via the `email_to` path** (not just `EmailAdapter`).
- [ ] Gold CTA verified on a **seeded + refreshed** `member.invited` row, not only the `copy.py`
      fallback.
- [ ] Merge values remain autoescaped in the final wrapped HTML.
- [ ] Manual check on `pastlives.test:8000` notifications editor + a real send (Mailpit at :8025):
      invite arrives as the branded navy card with a tappable gold button; legacy orientation email
      still looks identical to before.
- [ ] Specs green in the `plfog-web` image (≥98%); `ruff format .` + `ruff check .` clean.
- [ ] (Deploy) Re-run `python manage.py seed_notification_templates` so the invite button reaches the
      seeded DB row (the shell wrap needs no seed; the button does — §4.5).
- [ ] (Build time) `plfog/version.py` VERSION bumped + member-friendly CHANGELOG entry.

> Spec only — do not build until approved.
