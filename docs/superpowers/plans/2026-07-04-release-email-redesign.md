# Release-Update Email — Redesign (sectioned layout, feature screenshots, better preview) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-04
**Base:** release-0.20.x (anchor by symbol name; the email/composer code is stable across 0.19.x/0.20.x, but spec/build on the current wave). Independent of PR #118 — its own PR.
**Surface:** FOG hub **admin** — Site Settings → **Announcements** tab (`pastlives.test`); the member-facing **email templates** (`templates/membership/emails/`); and the **screenshot-capture harness** (`tests/e2e/`, CI). No book CMS.
**Related:**
- `reference_sitewide_announcements_composer` (the existing compose/preview/send flow — the release email is a manual send from here).
- `reference_rich_text_email_editor` (the Quill body path this reuses for the freeform intro).
- `.github/workflows/discord-notify.yml` (the auto-posted Discord release embed — parity reference; §10 defers a shared renderer).
- `.github/workflows/copy-review.yml` + `tests/e2e/screenshots_spec.py` (the existing CI capture → `copy-review.pastlives.space` gallery this extends for auto-captured feature shots).

---

## 1. Summary

When Past Lives ships a release, an admin sends a "what's new" email to every activated member. Today that email is **one flat dark card** — a bare `<h2>` glued above a wall of changelog bullets, with **no button, no images, no hero, no sections, no inbox preview line**. It reads like a system notice, not "look what we built." This feature rebuilds the release email into a **sectioned, polished layout**: a brand-colored **hero band** with the version + date, a short intro, then **one feature card per changelog entry** (title + bullets + an optional **screenshot of that feature in action**), a clear **"Open Past Lives" call-to-action**, and the existing footer. The screenshots are **auto-captured** by the CI harness that already feeds `copy-review.pastlives.space` (seeded fake data → no PII), hosted on R2, and assembled automatically from the changelog. The admin gets a **much better preview** — a mobile-width toggle, the subject + inbox preheader, the plain-text part, and a **"send a test to myself"** so they can see it in a real inbox before it goes to everyone. The whole thing is built from a small set of **reusable inline-styled email partials** (`_hero`, `_feature_card`, `_button`, `_screenshot`) — our own lightweight "email component library," no Node build step.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Ambition for v1 | **Core + screenshots** — sectioned redesign (hero, version badge, per-feature cards from the changelog, CTA, preheader) + the reusable email-partial library + the preview upgrade + one screenshot per feature. |
| How screenshots get in | **Auto-capture.** Reuse/extend the existing Playwright capture harness (`screenshots_spec.py`, already run in CI by `copy-review.yml`) to shoot the member-hub **feature pages** on seeded data; upload to R2 at stable keys; the email resolves each card's image from the changelog. The composer offers a light per-card override (swap/hide), but the default is fully automatic. |
| Look | **Hybrid** — a brand-colored **hero band** over a **light** content area with dark text. Impressive *and* survives mail-client dark-mode inversion (a fully-dark email is the least predictable choice across clients). |
| Template library | **Build our own** reusable inline-styled Django email partials. MJML/Maizzle/Foundation all need a Node build step; this app is deliberately pip-only (no npm/bundler; Render + SQLite CI). Compose-over-custom. |
| GIFs / motion | **Out of scope** (deferred, §10). Outlook desktop shows only a GIF's first frame and there is no GIF pipeline; PNG stills only for v1. |
| New DB model? | **No.** The release email is derived from `plfog/version.py::CHANGELOG` + the capture registry + the composer form. No persisted draft (deferred, §10). |
| Blast radius on other emails | **Contained.** The shared dark shell (`_base.html`) and every transactional email (receipts, reminders) are **untouched**; only the release/announcement email gets the new hybrid layout. Migrating transactional emails onto the new partials is deferred (§10). |

## 2. What already exists (reuse, don't reinvent)

All anchors verified by the reuse scout on the current tree.

| Need | Existing thing | Location |
|---|---|---|
| The release email's current builder | `_announcement_email_html(title, body)` → `wrap_email_html` | `hub/views.py:3083`; wrapper `core/events/templates.py:73` |
| "Draft from latest release" (aggregates the current MAJOR.MINOR changelog line) | `_release_announcement_draft()` | `hub/views.py:3100` (wired via `?draft=release`, `:3258`) |
| Compose → preview → send handler (preview/send actions) | `_handle_announcement_action(request, action)`; preview returns `{html, count, post_to_discord}` | `hub/views.py:3163` (preview dict `:3178`) |
| The send itself (emits via the spine, EMAIL override, Discord gate) | `_send_site_announcement` → `emit("site_announcement", messages={Channel.EMAIL: email}, suppress_broadcast=not post_to_discord, period=…)` | `hub/views.py:3120` |
| Exact recipient count (`ALL_ACTIVE_MEMBERS`) | `_activated_member_count()` | `hub/views.py:3071` |
| Admin gate + tab page | `admin_site_settings` (`@fog_admin_required`), Announcements tab | `hub/views.py:3208`; `hub/urls.py:255`; `templates/hub/admin/site_settings.html:371` |
| Live HTML preview (iframe) | `<iframe srcdoc="{{ announce_preview.html }}">` | `templates/hub/admin/site_settings.html:422` (chrome `background:#fff` `:423`) |
| Compose form + body sanitize | `SiteAnnouncementForm` (Quill body); `clean_body` runs `sanitize_rich_html` | `hub/forms.py:876` (`:898`) |
| The gold CTA button pattern to lift into a partial | inline `<a>` — `padding:12px 32px; background:#EEB44B; color:#092E4C; border-radius:6px` | `templates/membership/emails/guild_welcome.html:8` |
| Branded shell + footer (KEEP for transactional; the release email gets its own layout) | `_base.html`, `_footer.html` (unsubscribe link `:5`), `notification_shell.html` | `templates/membership/emails/` |
| Multipart text+HTML send (plain-text parity) | `core.email.send()` → `EmailMultiAlternatives` + `attach_alternative` | `core/email.py:46` |
| The `.txt` part renderer | `render_rich_email_text` / `rich_html_to_text` | `core/html_sanitize.py` |
| Absolute-URL base for links | `MEMBER_BASE_URL`; helper `_absolute_url(path)` | `plfog/settings.py:64`; `membership/orientations.py:32` |
| **Public, unsigned, cacheable image hosting** | R2 `S3Storage` with `custom_domain=R2_PUBLIC_URL`, `querystring_auth=False`; `default_storage.save/.url` | `plfog/settings.py:271-290` (`_R2_READY` `:232`) |
| **The screenshot-capture harness** (generic `capture(surface,label,path)` → `page.goto`+`page.screenshot`; page lists; seeds fake data; gated on `CAPTURE_SCREENSHOTS`) | `screenshots_spec.py` (`capture` `:280`, `_public_pages` `:154`, `_members_pages` `:174`, `_seed` `:62`) | `tests/e2e/screenshots_spec.py` |
| **CI capture → public gallery** (seeded fake data, no PII) | `copy-review.yml` → GitHub Pages `copy-review.pastlives.space` | `.github/workflows/copy-review.yml` |
| Structured release data (feature title + bullets, per entry) | `CHANGELOG` (`version`/`date`/`title`/`changes[]`) | `plfog/version.py` |
| Release Discord embed (parity reference) | `discord-notify.yml` aggregates the same MAJOR.MINOR line into one embed | `.github/workflows/discord-notify.yml` |

**Genuine gaps to close (kept minimal):**

1. **A hybrid release layout + 4 reusable email partials** (`_release_shell.html`, `_hero.html`, `_feature_card.html`, `_button.html`, `_screenshot.html`) — light body, inline styles, no Node.
2. **A renderer** `render_release_email(version, *, subject, preheader, intro, cards)` → `(html, text)` that reads `CHANGELOG`, builds one card per entry, resolves each screenshot URL, and assembles the sectioned HTML + a matching `.txt`.
3. **A feature-shot registry** (`FeaturePage(slug, label, path)` list) — the member-hub pages worth showing; drives both what the harness captures and what the composer can pick.
4. **Capture + upload** — extend the harness to shoot each feature page **framed** (not `full_page`) on seeded data and upload to R2 at `email/features/<slug>.png`; an optional `CHANGELOG` `screenshot` slug names each card's image.
5. **Composer upgrades** — a "Release" compose mode (`ReleaseAnnouncementForm`): subject + preheader + intro + a per-card screenshot **override** (swap/hide); and a **preview upgrade** (mobile toggle, subject/preheader inbox row, plain-text view, **send-test-to-myself**).

**Not a gap (do not touch):** the shared dark `_base.html`, the inline stylers `_style_copy_fragment`/`_RICH_TAG_STYLES` (tuned for the dark copy path), and every transactional email. `bundle_screenshots.py` inlines PNGs as **base64 data-URIs** — good for a shareable file, **useless in email** (Gmail strips data-URIs); the email uses hosted R2 URLs, not the bundle.

## 3. Where the code lives

```
templates/membership/emails/
  _release_shell.html      # NEW — hybrid layout: hero band + light body + _footer; preheader slot;
                           #   <meta name="color-scheme" content="light dark"> + supported-color-schemes; ~600px
  _hero.html               # NEW — brand-colored band: version badge + "What's new" + date
  _feature_card.html       # NEW — light card: optional _screenshot, linked title, bullet list
  _button.html             # NEW — the gold CTA button (lifted from guild_welcome.html:8), href+label params
  _screenshot.html         # NEW — responsive <img> (width 560, max-width:100%, height:auto, display:block, alt),
                           #   wrapped in a link to the feature page; renders nothing when no url
core/
  release_email.py  (new)  # render_release_email(version,*,subject,preheader,intro,cards) -> (html_str, text_str);
                           #   FeaturePage registry + resolve_feature_shot_url(slug); build_release_cards(version)
hub/
  forms.py                 # + ReleaseAnnouncementForm (subject, preheader, intro rich-text, per-card override rows)
  views.py                 # Announcements tab: a "Release" mode branch in _handle_announcement_action;
                           #   + announce_test action (direct send to the admin — NOT emit); preview uses the renderer
templates/hub/admin/
  site_settings.html       # Announcements tab: Release sub-mode; upgraded preview (mobile toggle, inbox row,
                           #   plain-text tab, "Send test to me" button); per-card override list
plfog/version.py           # CHANGELOG entries gain an optional "screenshot": "<feature-slug>" key
tests/e2e/
  screenshots_spec.py      # + _feature_pages() registry-driven list + a framed capture + R2 upload step
core/management/commands/
  capture_feature_screenshots.py (new, optional)  # run the feature capture + R2 upload outside CI (Render one-off)
static/css/hub.css         # .pl-email-preview-* (mobile toggle frame, inbox row) — admin-page chrome only
```

Home apps: `core` (renderer + registry), `hub` (composer form/view/template), `membership/emails` (the partials). All inside the existing coverage/mypy scope. The capture lives in `tests/e2e` (dev/CI only, opt-in), exactly as today.

## 4. Data model

**No new DB model, no migration.** The release email is derived data. Three code-level "shapes":

### 4.1 `CHANGELOG` entry — one optional key (`plfog/version.py`)

Each entry already carries `version`, `date`, `title`, `changes: list[str]`. Add **one optional** key:

| Key | Type | Note |
|---|---|---|
| `screenshot` | `str` (a `FeaturePage.slug`) — **optional** | Names which feature page's captured shot appears on this card. Absent → the card renders text-only (graceful). The dev who writes the changelog entry also names the shot; valid slugs = the registry (§4.2). |

Additive and backward-compatible — every existing entry (no `screenshot`) still renders, just without an image.

### 4.2 Feature-shot registry (`core/release_email.py`)

A frozen list — the single source of "which member-hub pages we screenshot for release emails," used by **both** the capture harness (what to shoot) and the composer (what you can pick):

```python
@dataclass(frozen=True)
class FeaturePage:
    slug: str      # stable id, used in CHANGELOG "screenshot" + the R2 key + the composer <select>
    label: str     # human label in the composer override <select> and the <img alt>
    path: str      # the hub URL to screenshot (e.g. reverse("hub_home"))

FEATURE_PAGES: list[FeaturePage] = [ ... ]   # curated; grows as features ship
```

- **Image URL resolution (existence-checked — the broken-image guard):** `resolve_feature_shot_url(slug) -> str` returns the R2 public URL **only if the object actually exists**: `key = f"email/features/{slug}.png"; return default_storage.url(key) if slug and default_storage.exists(key) else ""`. So an unknown slug **or a known-but-not-yet-captured** slug both resolve to `""` → `_screenshot.html` drops the `<img>` and the card renders text-only — **no broken image ever reaches a member**, even on the very first send before the capture job has run. Fail-loud on a *malformed* registry (dupe slug) at import. *(`default_storage.exists()` on R2 is a HEAD request; cache per-render results so a 6-card email isn't 6 round-trips. The composer-side gating in §6 Screen A means this is defense-in-depth, not the primary guard.)*
- **Card title link:** when a `FeaturePage.path` exists for the card's slug, the card title links to `MEMBER_BASE_URL + path` (FRONTEND email rule — link the subject noun); otherwise the title is plain text.

### 4.3 The assembled card (renderer input, in-memory)

`build_release_cards(version) -> list[Card]` reads `CHANGELOG`, filters to the current `MAJOR.MINOR` line (same logic as `_release_announcement_draft`/`discord-notify.yml`), and yields one `Card{title, bullets, screenshot_url, feature_url}` per entry (newest first). The composer's per-card override mutates `screenshot_url`/`included` before render.

## 5. Business logic (renderer + thin view)

Views stay thin. The logic is a pure renderer + small resolvers in `core/release_email.py`:

- **`render_release_email(version, *, subject, preheader, intro, cards) -> tuple[str, str]`** — assembles the hybrid HTML from the partials (`_release_shell` → `_hero` + intro + `{% for card %}_feature_card{% endfor %}` + `_button` + `_footer`) and a parallel plain-text body (title, `## <feature>` + `• bullet` lines, the CTA URL, the unsubscribe line) so `.txt`/`.html` stay in sync. No side effects; fully typed. Rendered via Django templates (inline styles), **not** string concatenation.
- **`build_release_cards(version)`** / **`resolve_feature_shot_url(slug)`** — §4.2/4.3.
- **`send_release_test(admin_user, html, text, subject) -> None`** — sends the assembled email to **only** the admin's own address via **`core.email.send()` directly — NOT through `emit()`.
  - *Why direct, not `emit`:* the real send uses `emit("site_announcement", …, period=f"site:{…}")`; routing a test through `emit` with any release-tied period would consume/dedupe that slot and could **block the real send** (see `reference_emit_period_required`). A test is a plain transactional send to one inbox — no spine, no `EventDelivery`, no dedup.
- **The real send is unchanged in mechanism** — `_send_site_announcement` still `emit("site_announcement", messages={Channel.EMAIL: html}, suppress_broadcast=True, period=f"site:{timestamp}")` with a **timestamp-unique period** (exactly as the existing composer does today), **not** a version-keyed one. This is deliberate: a version-keyed period would let the spine deliver a given release's email **only once ever**, so an admin who caught a bad card *after* sending could never send the corrected version (`reference_emit_period_required`). The `confirm(count)` dialog already guards accidental double-clicks; intentional **corrective resends must deliver**. Only the `html`/`.txt` change vs today. Discord stays **off** for the release send (the GitHub Action auto-posts the embed on merge) — the release-draft `post_to_discord=False` default holds (recovery escape hatch in §10).

## 6. UI / UX  ← completeness checklist applied per screen

Two screens: **(A)** the admin **Release compose + preview** surface (Site Settings → Announcements), and **(B)** the **email itself** (the rendered artifact). Plus the offline **capture** step (no interactive UI).

### Screen A — Release compose & preview (Site Settings → Announcements tab)

Extends the existing Announcements tab (`site_settings.html:371`), which already has draft-from-release, an iframe preview, a recipient count, and a confirm-guarded Send. We add a **Release sub-mode** and upgrade the preview.

- **Screen / partial:** `templates/hub/admin/site_settings.html` Announcements section; the compose form is `ReleaseAnnouncementForm`.
- **Layout & container:** an **inline form on the page** (it's 4+ fields plus a per-card list — per the FRONTEND interaction table, 4+ fields → inline form, not a modal). Stays inside the existing `hub-card`.
- **Entry into Release mode (the named front door):** repurpose the existing **"Draft from latest release"** button (`site_settings.html:383`) to submit `mode=release`; the view branches on that discriminator and binds **`ReleaseAnnouncementForm`** (not the freeform `SiteAnnouncementForm`). So that button / `?draft=release` now **swaps the composer to Release mode** — a **correction to the reuse note in §2**: today `?draft=release` merely *pre-fills* the freeform title+body; this feature makes it *select the Release form*. The freeform composer stays the default for ad-hoc blasts, reachable via a small **"← plain announcement"** link. Without this, an admin would land on the freeform composer with no way into the feature — so the switch is mandatory, not optional.
- **Components used:** `components/form_field.html` for subject / preheader / intro (Quill via the existing `RichTextEditorWidget`) and for each per-card screenshot `<select>`; `components/toggle.html` for each per-card **include** switch; the existing gold `pl-btn pl-btn--primary` for actions.

**The controls, named explicitly:**

- **Subject** — `form_field.html` text input, prefilled `What's new at Past Lives: {latest title}` (editable). Shown in the preview inbox row.
- **Preheader** — `form_field.html` text input, `field_hint`: "The gray preview line next to the subject in the inbox. ~90 characters." Prefilled from the first feature. (Absent today — high-impact.)
- **Intro** — the Quill rich-text intro (reuses the sanitize path), prefilled with a one-line "here's what shipped." Optional.
- **Per-feature card list (fixed, not a formset):** one row per current-line changelog entry, each showing the **read-only** feature title + bullets (from `CHANGELOG` — not editable here; the changelog is the source of truth) plus two controls:
  - a themed **screenshot `<select>`** (via `form_field.html`) — options = `FEATURE_PAGES` labels + **"No screenshot"**, defaulting to the entry's `screenshot` slug. The admin can swap or clear it. **Only slugs whose R2 asset exists are offered as picked-and-shown**; if the changelog's default slug isn't captured yet, the card **defaults to "No screenshot"** and the preview shows a muted **"Screenshot for '{label}' hasn't been captured yet — run the capture or pick another."** So the admin never unknowingly ships a card that *expects* an image but renders text-only. (This composer gating is the primary broken-image guard; the §4.2 `exists()` check is the backstop.)
  - an **include toggle** (`toggle.html`, default **on**) — off drops the whole card from the email.
  - **Why no "+ Add / Delete" triad:** cards are **derived from the changelog**, not created/deleted by the admin — so the §1 list-editor rules don't apply (called out so the UX reviewer doesn't flag a missing Add button). The admin curates via *include* toggles + *screenshot* selects, and the **Save-equivalent is the Preview / Send actions** below. There is nothing half-built: everything visible is actionable.
  - **Form construction (so the builder isn't guessing):** `ReleaseAnnouncementForm.__init__` reads `build_release_cards(version)` and adds a **pair of dynamically-named fields per entry** keyed by a slugified version — `include_<vslug>` (BooleanField → `toggle.html`) and `screenshot_<vslug>` (ChoiceField → themed `<select>`). **Not** a Django formset (nothing is added/removed) — a fixed, dynamically-built field set, iterated in the template next to each read-only card. Validation (e.g. "at least one card included") lives in the form's `clean()`, not the view.
- **Actions (the Save-equivalents), a button row (8px-grid gap, `margin-top:1rem`):**
  - **Preview** (`pl-btn pl-btn--secondary`) — POSTs the form, re-renders the preview (below). No send.
  - **Send test to me** (`pl-btn pl-btn--secondary`) — POSTs `announce_test`; calls `send_release_test(request.user, …)`; returns to the page with `messages.success("Test sent to {admin.email} — check your inbox.")`. (Full-page post → Django messages, matching the existing pattern.)
  - **Send to N members** (`pl-btn pl-btn--primary`, `confirm()`-guarded with the live count, exactly as `site_settings.html:406` today) — POSTs `announce_send`; assembles + `emit`s; redirects with the standard success message.

**The upgraded preview (the confidence-builder):**

- **Rendered HTML** in the existing `<iframe srcdoc>` — but the wrapper chrome fixed: drop the misleading `background:#fff` (`:423`) and frame the iframe in a neutral device shell so the real email background shows.
- **Mobile-width toggle:** two `pl-btn--sm` toggles **[ Desktop | Mobile ]** above the iframe that set its width (`~600px` vs `~375px`) via an Alpine `x-data` width binding — **no inline `display` on any `x-show` element** (Alpine strips it; use a `pl-` width class / a bound `style="width:…"` only, which is a genuine one-off nudge, not a layout `display`).
- **Inbox row:** above the iframe, a small **sender · subject · preheader** line rendered from the form so the admin sees exactly what lands in the inbox (both are otherwise invisible in the HTML body).
- **Plain-text tab:** a **[ HTML | Plain text ]** `pl-btn--sm` toggle; the plain-text pane shows the `.txt` part (surfacing the alternative that's produced today but never shown). **Rule 12 applies to all three `x-show` panes** here — the desktop/mobile iframe frames *and* the HTML/plain-text panes: put any `display:flex/grid` in a `pl-` class, never inline on the `x-show` element (Alpine strips inline `display` on reveal → the pane collapses). The mobile-width swap is the one allowed inline nudge — a bound `style="width:…"`, not `display`.

**States:**
- *Empty / nothing to send:* the **reachable** degenerate case is **every card toggled off** — the include toggles let an admin exclude all cards, which would send a hero+intro+CTA with no what's-new. So when **zero cards are included**, disable **both** Send and Send-test with a muted **"Include at least one feature to send."** (The near-unreachable no-entries case — no changelog entries at all in the current `MAJOR.MINOR` line, which normally can't happen since VERSION always shares its line with its own entry — shows **"No unreleased changes for v{minor} yet."** with the same disabled Send. Don't lean on it.)
- *Loading:* full-page POST (standard browser submit); no HTMX in-flight state to design. (The Quill editor is client-side, already handled.)
- *Error:* `ReleaseAnnouncementForm` invalid (blank subject) → re-render the tab with `messages.error` + field errors. A screenshot slug that no longer resolves → the card silently drops the image (defense-in-depth; the picker only offers valid slugs).
- *Success:* Preview → the preview panel populates; Send test → `messages.success` naming the admin's address; Send → redirect with the recipient-count success message.
- **No dead ends:** every action returns to the tab with a message; the admin can preview → test → tweak → send in one place.

**Dark + light (admin page):** the compose controls are all `form_field.html` / `toggle.html` (themed for both). The preview *chrome* (device frame, inbox row, toggles) uses `pl-` classes with theme tokens in `hub.css` — **no inline `background`/`color`** on any control, **no `--surface`** fallback. The iframe *content* is the email itself (its own light look — Screen B). Verify both themes on the admin page.

**Mobile (admin page):** the compose form and per-card rows are single-column and reflow; the preview iframe scrolls within its framed container (never widens the viewport); the action buttons wrap. 8px-grid spacing; the action row and each per-card `<select>`/toggle clear the element above (`margin-top:0.75rem`).

**Implementation note (not a blocker):** the app ships **no** Content-Security-Policy today (no django-csp, no edge CSP), so the preview iframe loads R2 images fine as-is. *If* a CSP is ever added at the edge, its `img-src` must include the R2 `custom_domain` or the **preview** would show broken images (the sent email is unaffected — a mail client ignores the app's CSP). The real screenshot risk is asset **existence** (§4.2 / the composer gating above), not CSP.

### Screen B — the email itself (the rendered artifact)

The deliverable. Built from the partials; **hybrid** look; must satisfy the FRONTEND *Email Templates* rules.

- **Layout (top → bottom):**
  1. **Preheader** — a hidden `<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{{ preheader }}</div>` immediately after `<body>` (seeds the inbox preview; absent today).
  2. **Hero band (`_hero.html`)** — a **brand-colored** full-width band (`#092E4C` deep blue with cream `#F4EFDD` text, or the gold accent — the one place we keep the brand mood), containing a **version badge** ("v0.20"), the headline **"What's new"**, and the release **date**. This is the "brand hero" half of the hybrid.
  3. **Intro** — the admin's one-line intro on the **light** body.
  4. **Feature cards (`_feature_card.html`, one per included entry)** — on a **light** card (`#FFFFFF`/near-white, dark `#1c2b36` text): the optional **`_screenshot.html`** at top (responsive, `alt`=feature label, links to the feature page), the **feature title** (linked to its page when known), and the **bullet list** of `changes`. Cards stack with 8px-grid spacing.
  5. **Primary CTA (`_button.html`)** — a gold button **"Open Past Lives"** → `MEMBER_BASE_URL` (the hub home). The lifted `guild_welcome.html:8` pattern.
  6. **Footer (in its own dark band).** `_footer.html`'s copy is **tuned for a dark card** (muted `#96ACBB` text, gold `#EEB44B` links — which **fail WCAG AA on white**). So `_release_shell.html` renders the reused `_footer.html` **inside its own dark band** (`#092E4C`, matching the hero) at the bottom of the light body — the existing colors are legible there and the include stays reused unchanged. Net hybrid: **dark hero → light feature body → dark footer band.**
- **Images-off safety (Gmail/Outlook block images by default):** every card is **fully legible with no image** — the title + bullets carry all meaning; the screenshot is enhancement. Every `<img>` has descriptive `alt`. Sized for retina (capture ~1120px wide, display `width="560" max-width:100%; height:auto; display:block`).
- **Dark-mode resilience:** the light **body** survives client auto-inversion far better than today's dark-on-dark; add `<meta name="color-scheme" content="light dark">` + `supported-color-schemes` to `_release_shell.html` (respected by Apple Mail/Outlook; Gmail recolors regardless — the light base is the real mitigation *for the body*). The **hero band is the one dark element**, so design it to read **both** upright and inverted: a mid-tone brand background with near-white text that still clears AA if a client flips it (avoid pale-cream-on-navy, which inverts to pale-cream-on-pale). **Verifying the hero in a real client via Send-test-to-me is a required pre-ship step** (§6 Screen A) — it's the one element the light-base mitigation doesn't cover, so it's a gate, not an aspiration.
- **`.txt` parity:** the renderer emits a matching plain-text body (subject, `## feature` + `• bullets`, CTA URL, unsubscribe). Change one, change the other (covered by a spec).
- **Absolute URLs everywhere** (`MEMBER_BASE_URL` / `resolve_feature_shot_url`); branded, **no "BETA."**
- **Mobile:** single-column, ~600px max, cards full-width, image `max-width:100%`; the hero band and buttons reflow. No `@media` needed (single column); inline styles only (clients strip `<style>`).

### The capture step (offline / CI — no interactive UI)

- Extend `screenshots_spec.py` with **`_feature_pages()`** driven by `FEATURE_PAGES` (§4.2) and a **framed** capture (a fixed viewport, e.g. 1200×800 @2×, screenshotting the viewport or a hero `locator` — **not** `full_page`, which produces tall scroll-shots wrong for a card). Seed each feature page's data in `_seed()` so the shot shows the feature *in a good state* (this seeding is the bulk of the effort — see §10).
- After capture, **upload** each `email/features/<slug>.png` to R2 via `default_storage` (stable key, overwrite-in-place so the latest feature state always sits at a stable URL). Runs in CI (`copy-review.yml`, needs R2 secrets — §10 ops) or via the optional `capture_feature_screenshots` management command as a Render one-off job.
- Seeded **fake** data only (as today) → **no member PII** ever appears in a screenshot. This is a feature of reusing the copy-review harness.

## 7. Notifications / emails / activity

- **No new spine event.** The real release send stays on the existing `site_announcement` event (`registry.py:220` → `ALL_ACTIVE_MEMBERS`), `emit(messages={Channel.EMAIL: html}, suppress_broadcast=True, period=f"site:{timestamp}")` — only the assembled `html`/`.txt` change. **Timestamp-unique `period`** (like the existing composer), **not** version-keyed, so a corrective resend actually delivers (§5) — the `confirm(count)` dialog guards accidental double-clicks.
- **The test send is NOT a spine event** — direct `core.email.send()` to the admin only (§5). No `EventDelivery`, no dedup, so it never blocks the real send.
- **Discord:** unchanged and **off** for the email send (the `discord-notify.yml` Action auto-posts the embed on merge to main). §10 defers a *shared* changelog→(email cards + Discord embed) renderer so the two never drift.
- **No `SiteActivity`** — an admin email blast isn't a member activity (parity with today).
- **Email checklist (FRONTEND):** subject noun (each feature title) links to its page; one obvious CTA ("Open Past Lives") + the per-card feature links; the human-written intro is surfaced (guarded — only if set); absolute URLs; branded shell, no BETA; subject + body one timezone; `.txt` + `.html` in sync.

## 8. Build order (phased; each phase ships green)

1. **Email partial library + renderer (no send wiring).** Add `_release_shell/_hero/_feature_card/_button/_screenshot.html` (hybrid, inline styles, preheader, color-scheme meta) and `core/release_email.py::render_release_email` + `build_release_cards`. Specs render HTML+txt from a fixture CHANGELOG (cards, linked titles, preheader, CTA, `.txt` parity, no-image graceful). Full suite + lint + mypy green. *(Ships invisibly — nothing sends it yet.)*
2. **Wire the Release send + preview to the renderer.** `ReleaseAnnouncementForm` (subject/preheader/intro + per-card include/select); the Release-mode branch in `_handle_announcement_action` builds via `render_release_email`; the iframe preview uses it. Real send unchanged in mechanism. Specs: preview assembles; send emits with the new html + unique period. Green.
3. **Preview upgrade + send-test.** Mobile toggle, inbox row, plain-text tab (admin-page chrome in `hub.css`); `announce_test` action → `send_release_test` (direct send, asserted **not** to touch the spine/period). Green.
4. **Feature-shot registry + capture + R2 upload + changelog `screenshot` key.** `FEATURE_PAGES`, `resolve_feature_shot_url`, the `_feature_pages()` capture + framed shot + R2 upload, per-feature `_seed()` states, the optional `capture_feature_screenshots` command, and the composer's screenshot `<select>`. Specs mock `default_storage`. Green. *(Carries the go-live ops: R2 secrets in CI — §10.)*
5. **Housekeeping.** Bump `plfog/version.py` VERSION + a member-friendly CHANGELOG entry (this *is* the feature that will announce itself — dogfood it: give this entry a `screenshot` slug so the first redesigned email shows the redesigned composer). Runbook note for the capture step.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under each app's `spec/`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, `respx`/mocked storage (never a real R2/SMTP/Discord call), ≥98% branch coverage, run in the `plfog-web` Docker image (`--no-cov` for subsets).

- **`core.release_email` (renderer + resolvers):**
  - `build_release_cards`: one card per current-line entry, newest first; excludes other MAJOR.MINOR lines; a card's `feature_url` set iff the slug maps to a `FeaturePage.path`.
  - `render_release_email`: HTML contains the preheader div, the hero band with version+date, each feature title (linked when known) + its bullets, the CTA button; the `.txt` part contains the same feature titles/bullets + the CTA URL + unsubscribe line (**parity**); an entry with no `screenshot` renders **no `<img>`** but the card still appears; an entry with a `screenshot` renders `<img alt="…" src="…features/<slug>.png">`.
  - `resolve_feature_shot_url`: known slug → the `default_storage.url` (mock storage); unknown/empty → `""`; a duplicate slug in the registry raises at import.
- **`hub` — `ReleaseAnnouncementForm` + view actions:**
  - Form: blank subject invalid with a friendly message; preheader/intro optional; per-card include defaults on, screenshot select defaults to the entry's slug and offers all `FEATURE_PAGES` + "No screenshot".
  - `announce_preview`: assembles the HTML from the submitted form + count; an omitted card (include off) is absent; a swapped screenshot select changes the `<img src>`.
  - **`announce_test`: sends to ONLY the admin's address via `core.email.send` — assert exactly one message, to `request.user.email`, and that NO `EventDelivery` row / NO `site_announcement` period is consumed (so a subsequent real send still delivers).**
  - `announce_send`: emits `site_announcement` with the assembled html, `suppress_broadcast=True`, a **timestamp-unique** period; a **corrective resend delivers** (not deduped away — §5).
  - Disabled-send guard: **every card toggled off → Send + Send-test disabled** ("Include at least one feature"); the near-unreachable no-entries state also disables Send; gated `@fog_admin_required` (non-admin 403).
- **Template states (parse the rendered HTML, per `reference_nested_form_save_bug` — a test-client 200 won't catch structure):** the email has the hidden preheader div, a real CTA `<a>` button, and `<img alt>` on shots; the admin preview panel renders the inbox row + the mobile/HTML-text toggles.
- **Capture harness:** `_feature_pages()` yields a `(label, path)` per `FEATURE_PAGES`; the feature capture writes a framed PNG (not `full_page`) and calls the R2 upload for each slug (mock `default_storage`); gated on `CAPTURE_SCREENSHOTS` like the existing spec (doesn't run in the normal suite).
- **Gotchas:** the version-badge date comes from the entry `date` (subject + body one tz); the test-vs-real send period isolation is the one correctness-sensitive line — pin it.

## 10. Open / deferred

- **GIFs / motion** — deferred (locked). Outlook desktop shows only the first frame and there's no GIF pipeline (Playwright emits PNG/webm, not GIF; a GIF would be an offline ffmpeg artifact). If ever added: one hero GIF, first-frame-legible, <1MB.
- **Persisted release-email draft** — none (locked). Compose → preview → test → send happens in one session from the form + changelog. Add a draft model only if admins ask to prepare-ahead-and-send-later.
- **Card reorder** — deferred. Cards render newest-first from the changelog; the admin can include/exclude and swap screenshots but can't hand-reorder to lead with a non-newest flagship. Add drag-reorder only if newest-first proves wrong in practice.
- **Discord recovery toggle on the Release form** — deferred. Discord is auto-posted by `discord-notify.yml` on merge, so the Release form intentionally omits a Discord option. If that Action fails, recovery is a manual `workflow_dispatch` re-run (per `reference_discord_release_notify`), outside this composer. A default-**off** "also post to Discord" recovery checkbox is a cheap future add if the Action proves flaky.
- **Migrating transactional emails (receipts, reminders) onto the new hybrid partials** — out of scope; the shared dark `_base.html` and its stylers are untouched. The partials are built reusable so this is a clean follow-up.
- **One shared changelog→(email cards + Discord embed) renderer** — deferred. Today the email (this spec) and the `discord-notify.yml` embed each read the changelog independently; a single renderer feeding both would stop them drifting. Nice, not needed for v1.
- **Auto-mapping every changelog entry to a screenshot with zero authoring** — v1 uses an explicit optional `screenshot` slug + a composer override; a heuristic (entry title → best-match feature page) is deferred.
- **Fully-automatic per-feature seed states** — the `_seed()` work to make each feature page screenshot *well* is the real effort and grows with each `FeaturePage`; start with the 3–5 highest-value pages, expand over time. A page with no good seed state simply isn't in `FEATURE_PAGES` yet (card renders text-only).
- **`List-Unsubscribe` header** (beyond the footer link) — a deliverability nicety configured at Anymail/Resend, outside the template; note for the ops backlog.

## Out of scope

- Any change to the shared dark shell, the copy stylers, or non-release transactional emails.
- A new spine event, new audience, or Discord HTML cards (Discord gets markdown/embeds, not the email partials).
- Member-authored images / expanding the Quill sanitizer to allow `<img>` in bodies (the screenshots are system-sourced from the registry, not pasted by an author).
- Editing the changelog *content* from the composer (the changelog in `version.py` is the source of truth; the composer curates presentation only).

## Done checklist

- [ ] `_release_shell/_hero/_feature_card/_button/_screenshot.html` — hybrid (brand hero + light body), inline styles, preheader div, `color-scheme` meta, ~600px; reusable params.
- [ ] `render_release_email()` assembles HTML **and** a parity `.txt`; `build_release_cards()` filters the current MAJOR.MINOR line; `resolve_feature_shot_url()` → R2 stable key or `""`.
- [ ] `FEATURE_PAGES` registry drives both the capture list and the composer `<select>`; dupe slug fails loudly.
- [ ] `CHANGELOG` gains an optional `screenshot` slug; entries without it render text-only cards.
- [ ] Release compose mode: subject + preheader + intro + per-card include-toggle/screenshot-select; **no** phantom "+Add/Delete" (cards derive from the changelog — noted); actions = Preview / Send test to me / Send to N (confirm-guarded).
- [ ] Preview upgrade: fixed chrome, **mobile toggle** (no inline `display` on `x-show`), **inbox row** (sender·subject·preheader), **plain-text tab**, **Send test to me** (direct send, spine-isolated).
- [ ] Every `<img>` has `alt`; email fully legible with images off; retina-sized; absolute URLs; branded, no BETA; `.txt`/`.html` in sync.
- [ ] Capture: `_feature_pages()` framed shots on seeded fake data → R2 `email/features/<slug>.png`; opt-in like the existing capture; no PII.
- [ ] Dark + light verified on the admin page; email verified in a real client via Send-test. Mobile reflow verified (admin form + email).
- [ ] Specs green (≥98% cov) in `plfog-web`; the **test-send-doesn't-consume-the-real-period** case pinned; `ruff format`+`check` + mypy clean.
- [ ] `VERSION` bumped; a member-friendly CHANGELOG entry added (with a `screenshot` slug — dogfood the redesign).
- [ ] **Ops (go-live):** R2 credentials available to the capture step (CI secrets or a Render one-off job). *(No CSP to widen — the app has none today; §6 note.)*
```
