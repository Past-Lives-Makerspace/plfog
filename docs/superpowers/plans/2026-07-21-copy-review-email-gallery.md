# copy-review.pastlives.space → the Email Copy Gallery — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-21
**Surface:** Static GitHub Pages site (`copy-review.pastlives.space`), built in CI from the plfog repo. No FOG hub / book CMS page changes.
**Related:** `2026-07-04-release-email-redesign.md`, `2026-07-17-release-email-any-screenshot.md`; memory `reference_shot_review_tool`, `project_notification_redesign`, `project_release_email_redesign`.

---

## 1. Summary

`copy-review.pastlives.space` today publishes a contact-sheet of **page** screenshots (the seeded CMS pages) for the copy team to skim. This redesign replaces that gallery with a single, complete **Email Copy Gallery**: every email the app can send, rendered visually *exactly as it will look in the inbox* (the real branded shell, real merge fields filled with sample data), organised into clearly-labelled sections with a plain-language **trigger note** on each ("Sent when…", "Goes to…"). The point is a single approval surface: to sign off all outgoing email copy, a reviewer reads one URL.

Completeness is the whole feature. The build carries a **registry of every email** plus a **completeness guard** that fails the build (and normal CI) if a new email template is added under the email dirs without being registered — so "all email copy must be approved" is enforced going forward, not just true on the day we ship.

### Locked decisions (from Josh)

| Decision | Choice |
|---|---|
| What the site shows | Every email the app can send — nothing else. Page screenshots are gone. |
| Build model | Keep today's model exactly: render at **build time** with seeded fake data, publish static HTML. Public/unlisted, no auth — safe because only fake data is ever rendered. |
| Build entry point | A **pytest capture spec** `tests/e2e/email_gallery_spec.py` (browserless — email HTML is pure Python), gated by `BUILD_EMAIL_GALLERY`, writing to `SHOT_DIR`. Chosen over a management command because the spec's transactional `db` fixture **rolls back** — running it locally never pollutes Josh's dev DB (which holds pulled prod data). Mirrors the existing `screenshots_spec.py` precedent. |
| What gets *replaced* | Only what copy-review **publishes**. The page-capture (`describe_cms_screenshots` + `_write_index`) stops feeding the site. `describe_feature_screenshots` (the R2 feature-shot pipeline) is gated **only** by `CAPTURE_SCREENSHOTS`, which **only** copy-review.yml sets — so it is **rehomed** as its own retained step in this same change (Playwright kept), never dropped. |
| Two email families, both covered | (1) notification-spine events, rendered via the same `render_copy` + `wrap_email_html` send path; (2) structural `.txt`/`.html` template-pair emails + the two special renderers (release-update, announcement composer). |
| Events that don't email | Spine events whose EMAIL channel is ON/FORCED get a rendered card. Events with EMAIL off/absent are **listed by name** in a final "No email is sent" section (with the reason) — never silently omitted, so the index is provably complete. |
| Page chrome | Simple, clean, neutral static page — sticky sidebar TOC + one card per email + email rendered in an `<iframe srcdoc>` + plain-text behind a `<details>`. No FOG component library, no theme system needed (there are no form controls). |
| Versioning | Internal tool → bump `VERSION`, **no** member-facing CHANGELOG entry (invisible to members). Folds into whatever batch ships it. |

## 2. What already exists (reuse, don't reinvent)

Confirmed on latest `main` in the worktree.

| Need | Existing thing | Location |
|---|---|---|
| The workflow to edit (build → upload-pages-artifact → deploy) | `copy-review.yml` (`push: main` + `workflow_dispatch`, Pages perms, concurrency) | `.github/workflows/copy-review.yml` |
| Precedent: env-gated capture spec that seeds data + writes `SHOT_DIR` + an `index.html` | `describe_cms_screenshots` / `_write_index` / `_seed` | `tests/e2e/screenshots_spec.py:62,332,374` |
| The R2 feature-shot pipeline to **leave alone** (gated only by `CAPTURE_SCREENSHOTS`; only copy-review.yml sets it → rehome, don't drop — B1) | `describe_feature_screenshots` | `tests/e2e/screenshots_spec.py:431` |
| Every registered spine event (~45) + its channels/defaults/category/recipient | `all_events()`, `EventType.has_channel`, `.channel(...).default` | `core/events/registry.py:688,133,140` |
| Seeded/curated copy + per-event **sample context** | `default_copy_for`, `sample_context_for`, `_CURATED`, `_generic_copy` | `core/events/copy.py:1081,1076,125,1036` |
| One-line **audience** description per event | `audience_description(event)` | `core/events/copy.py:113` |
| The exact send-path render (subject + text + HTML) | `render_copy` → `RenderedCopy` | `core/events/rendering.py:95` |
| Wrap a copy fragment in the branded shell (returns a plain `str`, attribute-safe for `srcdoc`) | `wrap_email_html` | `core/events/templates.py:73` |
| Resolve DB copy → seeded default (unrendered) | `resolved_copy(event_key, channel)` | `core/events/templates.py:54` |
| Render a structural shell email exactly as it ships (text + html) | `email_shell_message(...).body` / `.html_body` | `core/events/senders.py:40` |
| The branded email base + notification shell | `_base.html`, `notification_shell.html` | `templates/membership/emails/` |
| `srcdoc` iframe embedding pattern (attribute-escaped full HTML) | `_compose_email_preview.html`, `preview_copy` | `templates/hub/partials/_compose_email_preview.html:14`, `hub/notification_views.py:228` |
| Special renderer: release-update email → `(html, text)` | `render_release_email(version, subject, preheader, intro, cards, lines)` | `core/release_email.py:276` |
| Special renderer: announcement composer email → full HTML | `build_announcement_email_html(title, body)` | `membership/models.py:2693` |
| Factories for every sample context | `classes/factories.py`, `tests/membership/factories.py`, `tests/billing/factories.py`, `core/factories.py` | (see §2 inventory) |
| Coverage/mypy already **exclude** `*/tests/*` | `[tool.coverage.run] omit`, `[tool.mypy] exclude` | `pyproject.toml:53,38` |

### Genuine gaps to close

1. A **gallery registry** module: one entry per email → `(category, name, renderer, context-builder, trigger note)`, plus the spine-event derivation and the **completeness guard** (discovered template pairs must all be registered).
2. A **build** module: render every registered email to `(subject, html, text)` and write the static site (sidebar TOC + cards + `srcdoc` iframes). Fails loudly if any email raises.
3. The **capture spec** + a non-gated **completeness spec** (runs in normal CI).
4. The **workflow** swap + a **local preview** script.

No new app models, migrations, views, or member-facing templates.

## 3. Where the code lives

Everything new lives under `tests/` (so it is outside the coverage source and mypy scope — same as `screenshots_spec.py`'s helpers today) plus one workflow and one script.

```
tests/e2e/email_gallery/
    __init__.py
    registry.py     # GalleryEmail dataclass; STRUCTURAL_EMAILS list; spine-event derivation;
                    # _TRIGGER_NOTES overrides; SECTION order + category→section map;
                    # discover_template_pairs() + registered_template_pairs() (completeness guard)
    context.py      # per-email sample-context builders (factory-based); returns what each renderer needs
    build.py        # render_one(email) -> RenderedEmail; build_site(out_dir) writes index.html + assets; raises on failure
tests/e2e/email_gallery_spec.py                 # describe_email_gallery / it_builds_the_gallery(db)  [gated on BUILD_EMAIL_GALLERY]
tests/core/email_gallery_completeness_spec.py   # describe_email_gallery_completeness  [NORMAL suite — not gated, enforces approval]
scripts/build-email-gallery.sh                  # local one-command preview
.github/workflows/copy-review.yml               # capture step swapped to build the email gallery
plfog/version.py                                # VERSION bump (no changelog entry)
```

Why `tests/e2e/` for the registry/build code: `pyproject.toml` omits `*/tests/*` from coverage and mypy excludes `tests/`, so this test-support code carries no coverage/type gate (exactly how the current `screenshots_spec` helpers escape it). The **completeness** spec lives under `tests/core/` (not `tests/e2e/`) so it is collected by the default `-m 'not e2e'` run — a new unregistered email fails normal CI.

## 4. Data model

None. No Django models, no migrations. The only "model" is an in-repo dataclass in `registry.py`:

```python
@dataclass(frozen=True)
class GalleryEmail:
    key: str                       # stable id (event key or template basename); used for the anchor + dedup
    name: str                      # human title shown on the card
    section: str                   # one of SECTIONS (sidebar group)
    renderer: Renderer             # enum: SPINE_COPY | SHELL_TEMPLATE | WELCOME | RELEASE | ANNOUNCEMENT | ALLAUTH | INLINE_STRING
    trigger_note: str              # plain-language "Sent when… / Goes to…"
    edit_pointer: str              # M5: where a reviewer edits this copy (see §6)
    # renderer-specific hints:
    event_keys: frozenset[str] = frozenset()   # ALL spine event keys whose EMAIL this entry renders (dedup; may be >1)
    text_template: str | None = None
    html_template: str | None = None
    template_prefix: str | None = None   # allauth (account/email/<prefix>)
    context_builder: str | None = None   # name of the callable in context.py
```

`SECTIONS` (sidebar order): **Classes · Teaching · Guilds & Orientations · Billing · Membership & Account · Voting · Events · Announcements & Release · System/Auth**, then the closing **"No email is sent"** note-section.

## 5. Business logic — the registry, renderers, and completeness guard

### 5.1 Family 1 — notification-spine events (derived, not hand-listed)

Generated from the registry so the long tail is automatic:

```
# _STRUCTURAL_EVENT_KEYS = union of every entry.event_keys in STRUCTURAL_EMAILS (§5.2)
for event in all_events():
    if event.key in _STRUCTURAL_EVENT_KEYS:       # M2: test dedup FIRST — its email ships as a
        continue                                  # structural template, rendered by that entry
    email = event.channel(Channel.EMAIL)          # ChannelSpec | None
    if email is None or email.default is ChannelDefault.OFF:
        no_email.append(event)                    # → "No email is sent" section (with reason)
        continue
    render as SPINE_COPY (below)
```

> **M2 landmine:** the dedup check MUST come before the EMAIL-off classification. `tab_charge_failed` has EMAIL default **OFF** for the member yet its email ships to admins via the `charge_failed_admin` structural template (`billing/notifications.py:99`). Ordered as above it renders as the structural admin card; ordered the other way it would wrongly fall into "No email is sent." Same shape for any member-OFF event that still emails a staff/admin audience through a shell template.

**SPINE_COPY render** — the same path `emit()` uses, so the card is faithful:
`resolved_copy(event.key, Channel.EMAIL)` → `render_copy(subject, body_text, body_html, context=sample_context_for(event.key))` → `wrap_email_html(rendered.body_html)`. Card subject = rendered subject; plain-text = rendered `body_text`; audience = `audience_description(event)`; channels badge = `event.channel_list()`.

**Trigger note:** `f"Sent when {event.description…} Goes to: {audience_description(event)}."`, overridable by a hand-written `_TRIGGER_NOTES[event.key]` where the registry description is thin (e.g. the voting/`event.*` lifecycle keys).

> **Correctness landmine (must be handled):** many spine events were migrated to `emit_with_email_shell`, so the email that actually ships is the **structural template**, and the event's *curated EMAIL copy* in `copy.py` is only the in-app/Discord body. For those keys the gallery MUST render the **structural** entry (family 2), not the DB copy — hence the `_STRUCTURAL_EVENT_KEYS` dedup skip above. Getting this wrong would publish copy that never ships.

**`_STRUCTURAL_EVENT_KEYS` — the complete, verified set (one entry may own more than one key; `review_decision` owns two):**

```
registration_confirmed            → confirmation                    (classes/emails.py:118)
instructor_new_registration       → instructor_new_registration     (classes/emails.py:238)
class_review_requested            → review_request                  (classes/emails.py:311)
class_review_requested            → review_submitted_instructor     (classes/emails.py:351, same key, 2nd template)
class_validation_requested        → admin_validation_request        (classes/emails.py:430)
instructor_class_approved         → review_decision                 (classes/emails.py:516, key #1)
instructor_changes_requested      → review_decision                 (classes/emails.py:516, key #2 — M1)
waitlist_confirmed                → waitlist_joined                 (classes/emails.py:551)
waitlist_spot_available           → waitlist_spot_opened            (classes/emails.py:594)
class_reminder                    → reminder                        (classes/emails.py:648,656)
orientation_requested             → orientation_lead_request        (membership/orientations.py:296)
orientation_update                → orientation_request/_confirmed/_declined/_cancelled/_thankyou (orientations.py:160,392)
guild_joined                      → guild_welcome                   (membership/orientations.py:500)
discord_guilds_imported           → discord_guilds_imported         (membership/discord_sync.py:266)
tab_charged                       → receipt                         (billing/notifications.py:55)
tab_charge_failed                 → charge_failed_admin             (billing/notifications.py:100)
site_announcement                 → announcement (special) + release_update (special)  (release_email.py:339, models.py:2854)
```

`class_review_requested` and `orientation_update`/`orientation_requested` each fan out to more than one template — the completeness guard registers **all** template basenames; the dedup set is the **union** of every `event_keys`. The `welcome` (instructor→students) card and the allauth/`find_account` cards own **no** spine key (they're not on the emit spine — inline sends / allauth), so they never collide with family 1.

### 5.2 Family 2 — structural template-pair + special-renderer emails (explicit registry)

`STRUCTURAL_EMAILS` is a hand-authored list. Each entry names its renderer, its context builder, and its trigger note. Renderers:

- **SHELL_TEMPLATE** — render exactly as the send path does: `email_shell_message(event_key=…, subject=…, text_template=…, html_template=…, template_context=<built>).html_body` (and `.body` for the text side). Covers all classes/orientation/billing/discord template pairs.
- **WELCOME** — the instructor class-welcome email is **not** on the emit spine; render via `classes.emails._welcome_email_bodies(offering, greeting_name, ...)` (its `render_to_string` pair) so the card matches the real send.
- **RELEASE** — `render_release_email(version, subject=…, preheader=…, intro=…, cards=[sample Cards], lines=…)` → use the returned `html`/`text`.
- **ANNOUNCEMENT** — `build_announcement_email_html(title, body)` with a sample title + sample rich-text body.
- **ALLAUTH** — render `templates/account/email/<prefix>_message.html` / `_message.txt` and `<prefix>_subject.txt` via `render_to_string` with the adapter's context keys (`code`, `email`, `login_url`, `find_account_url`, `signup_url`).
- **INLINE_STRING** — for app-authored emails that build their body as a Python f-string and send via the `core.email.send` choke-point (no template file, no shell). The entry carries a small render lambda that reproduces the exact subject + text body the send site builds, from a sample context. **Covers the "Find my account" login-link email** (`FindAccountForm.send_login_email`, `core/forms.py:47-76`; subject "Your Past Lives Account", `trigger_kind="core.find_account"`) — a live, un-templated email absent from the first draft (**B2**). Card section: System/Auth. Note this email is plain-text only (no HTML body), so its card shows the text body directly and omits the iframe.

### 5.3 Sample-context builders (`context.py`)

One builder per email (or per small family), each returning the exact `template_context` the renderer needs, built from factories inside the `db` transaction. Seed values follow `screenshots_spec._seed` (Robin Vale / "Intro to Lost-Wax Casting" / Ceramics Guild) so copy reads against realistic content. Absolute URLs use the existing `_absolute_url()` helpers so links aren't bare paths. The scout-confirmed context keys per template (drive each builder):

| Email (template basename) | Renderer / send site | `template_context` keys the builder must supply |
|---|---|---|
| `confirmation` | `send_registration_confirmation` (`classes/emails.py:82`) | registration, offering, upcoming_sessions, self_serve_url, class_url, amount_paid_cents, amount_paid_dollars, footer |
| `welcome` (instructor→students) | `_welcome_email_bodies` (`:134`) | offering (with a representative author-written `welcome_email_subject` + `welcome_email_body`, `welcome_email_ready=True`), greeting_name, upcoming_sessions, self_serve_url, class_url — the builder MUST set a sample `welcome_email_body`, e.g. subject *"Welcome to Lost-Wax Casting — a few things before we start"* / body *"So glad you're joining us! Please wear closed-toe shoes and bring an apron. We provide all wax, tools, and metal. Doors open 15 minutes early — come find me at the casting bench. — Robin"* so the card shows real instructor voice, not a blank body (**M4**) |
| `instructor_new_registration` | `emit_instructor_new_registration` (`:191`) | registration, offering, class_url, manage_url, amount_paid, spots_filled, capacity |
| `review_request` | `_emit_review_request` (`:280`) | offering, approval, review_url, role_label |
| `review_submitted_instructor` | `_emit_instructor_review_explainer` (`:330`) | offering, approvals, instructor_url |
| `admin_validation_request` | `send_admin_validation_request` (`:404`) | offering, approval, review_url, guild_lead_name, instructor_name |
| `review_decision` | `send_class_review_decision` (`:445`) | offering, approval, edit_url, public_url, fully_approved, pending_rows |
| `waitlist_joined` | `send_waitlist_joined_confirmation` (`:531`) | registration, offering, position, self_serve_url, class_url |
| `waitlist_spot_opened` | `send_waitlist_spot_opened` (`:567`) | registration, offering, register_url, class_url, claim_window_hours |
| `reminder` | `build_class_reminder_occurrence` (`:610`) | registration, session, offering, self_serve_url, class_url |
| `orientation_request` / `_confirmed` / `_declined` / `_cancelled` | `membership/orientations.py:_context` (`:113`) | booking, slot, guild, greeting_name, guild_url, cancel_url |
| `orientation_lead_request` | `_emit_lead_request` (`:276`) | …`_context` + respond_url, confirm_url, decline_url |
| `orientation_thankyou` | `complete_orientation` (`:380`) | …`_context` + body |
| `guild_welcome` | `member_joined_guild` (`:477`) | guild, greeting_name, body, guild_url |
| `discord_guilds_imported` | `_send_import_confirmation` (`discord_sync.py:251`) | greeting_name, guilds (list of `{name,url}`), manage_url, complete |
| `receipt` | `send_receipt` (`billing/notifications.py:29`) | member, charge, entries, charged_at, billing_history_url |
| `charge_failed_admin` | `notify_admin_charge_failed` (`:74`) | member, charge, dashboard_url |
| `release_update` (special) | `render_release_email` (`core/release_email.py:276`) | version, subject, preheader, intro, `cards=[Card(...)]`, lines |
| `announcement` (special) | `build_announcement_email_html` (`membership/models.py:2693`) | title, body (sanitized rich-text HTML) |
| allauth `login_code` | `account/email/login_code_*` | code |
| allauth `unknown_account` | `account/email/unknown_account_*` | email, find_account_url, signup_url |
| allauth `account_already_exists` | `account/email/account_already_exists_*` | email, login_url |
| `find_account` (inline) | `FindAccountForm.send_login_email` (`core/forms.py:66`) | member (Robin Vale, ACTIVE, with `primary_email`), login_url — renderer reproduces the f-string subject + text body (**B2**) |

Prefer reusing the real send helper's own context assembly where it's cheaply callable with a built object (keeps the card byte-identical to production); fall back to building the `template_context` dict directly from the keys above.

### 5.4 Completeness guard (the enforcement)

- `discover_template_pairs()` — scan `templates/{classes/emails, membership/emails, billing/email, account/email}` for **standalone** emails: an `.html` **not** starting with `_` that has a sibling `.txt` of the same basename (allauth: treat `<prefix>_message.{html,txt}` as one pair keyed `<prefix>`). This rule naturally excludes every include/shell: `_base.html`, `_footer.*`, `_button/_hero/_feature_card/_screenshot/_slot.html`, `_release_shell.html` (underscore) and `notification_shell.html` / `base_message.txt` (no standalone `.txt`+`.html` pair).
- `registered_template_pairs()` — the set of template-pair keys the registry covers (the 23 SHELL_TEMPLATE/WELCOME/ALLAUTH entries; the two special renderers are exempt — they have no discoverable pair).
- **Guard 1 (template pairs):** `discover_template_pairs() - registered_template_pairs()` must be **empty**. A non-empty diff → `build_site` raises `EmailGalleryIncomplete(missing)` **and** the completeness spec fails. Adding `templates/classes/emails/new_thing.{txt,html}` without registering it breaks the build.

Expected discovered set (23): classes → `admin_validation_request, confirmation, instructor_new_registration, reminder, review_decision, review_request, review_submitted_instructor, waitlist_joined, waitlist_spot_opened, welcome`; membership → `discord_guilds_imported, guild_welcome, orientation_request, orientation_confirmed, orientation_declined, orientation_cancelled, orientation_lead_request, orientation_thankyou`; billing → `charge_failed_admin, receipt`; account → `login_code, unknown_account, account_already_exists`.

- **Guard 2 (inline-string sends — B3):** the template-pair guard does **not** catch emails sent as inline f-strings straight through the `core.email.send` choke-point (no template file to discover). So a second lint enumerates them honestly. `discover_inline_sends()` greps the app tree for `core_email.send(` / `core.email.send(` calls and collects each `trigger_kind="…"` literal; the guard asserts every collected `trigger_kind` is either **registered** as a gallery entry or in an explicit `_INLINE_EMAIL_ALLOWLIST` (each with a one-line reason). A new inline send with an unlisted `trigger_kind` fails the completeness spec — the enforcement now covers inline sends too, not just template files. The **complete, verified** current set of direct choke-point sends:

  | `trigger_kind` | Site | Disposition |
  |---|---|---|
  | `core.find_account` | `core/forms.py:66` | **Registered** — `find_account` INLINE_STRING card |
  | `classes.welcome_email` | `classes/emails.py:166` | **Registered** — `welcome` WELCOME card (this is the real send) |
  | `classes.welcome_email_test` | `classes/emails.py:181` | **Allowlist** — identical body to `welcome`, `[Test]` subject prefix; nothing new to approve |
  | `classes.instructor_message` | `classes/forms.py:913` | **Allowlist** — instructor free-text "email the class" blast; author-written, no app copy/shell (see §10) |
  | `classes.admin_message` | `classes/forms.py:984` | **Allowlist** — admin free-text "email the class" blast; same rationale (see §10) |

  (Emails sent through `emit()` / `emit_with_email_shell()` are not inline sends — they're covered by families 1 & 2. The lint targets only the direct `core.email.send` call sites.)

## 6. UI / UX — the static gallery page

One self-contained `index.html` (inline `<style>` + a tiny inline `<script>`; no external assets — GitHub Pages, no CDN). No forms, no theme system: the *emails* carry their own dark branded shell inside iframes; the page chrome is a single clean neutral light layout. Because there are no `<select>`/`<input>`/`<textarea>` on the page, the recurring dark-mode form-control class of bug does not apply here (call this out so no one adds a themed control later).

- **Layout & container:** two-column — a **sticky left sidebar** table-of-contents listing the `SECTIONS` (each an anchor link to that section, with a count), and a scrolling main column of section headings + cards. Max content width ~1100px, matching the current `_write_index` styling.
- **Section heading:** the section name + a one-line description of the group.
- **Card anatomy (per email):**
  - **Name** (the human title) + a stable `id` anchor.
  - **Subject line** — the exact rendered subject, in an "inbox row" styled like `_compose_email_preview.html` (`From: Past Lives · <subject>`).
  - **Audience** — who receives it (`audience_description` for spine; hand-written for structural).
  - **Trigger note** — plain language: "**Sent when** … **Goes to** …".
  - **Channels** badge — for spine emails, the event's channel list (in-app / email / Discord); structural emails are email-only.
  - **Where to edit (M5)** — a muted footer line telling a reviewer where this copy is changed: spine (SPINE_COPY) cards → *"Edit in Site Settings → Notifications"*; structural / WELCOME / RELEASE / ANNOUNCEMENT / ALLAUTH / INLINE_STRING cards → *"Template in code (`<path>`)"* naming the actual template file(s) or module (e.g. `templates/classes/emails/confirmation.{txt,html}`, `core/release_email.py`, `core/forms.py`). Carried on each `GalleryEmail` as `edit_pointer`.
  - **The rendered email** in an `<iframe srcdoc="{{ html }}" sandbox="allow-same-origin">` — the full branded document, attribute-escaped exactly as `_compose_email_preview.html` does it (this is why `wrap_email_html` returns a plain `str`, per `templates.py:80`).
  - **Plain-text variant** behind a `<details><summary>Plain-text version</summary><pre>…</pre></details>`, collapsed by default.
- **Controls / behaviour:** static page — the only interaction is the sidebar anchor links and the `<details>` toggles. No save/submit (nothing is edited here). A tiny inline `onload` script sets each iframe's height to its content `scrollHeight` (same-origin `srcdoc`, so `contentDocument` is readable); if scripting is off, iframes fall back to a fixed `max-height` with internal scroll (CSS), so no email is ever clipped to nothing.
- **States:**
  - **Empty section** — a section with zero emails renders a muted "No emails in this group." line (shouldn't happen, but never a bare gap).
  - **"No email is sent" note-section** — the closing section lists every event with EMAIL off/absent by **name + reason** ("in-app + Discord only", "opt-in (off by default)"), so the index is provably exhaustive.
  - **Render failure = build failure, never a broken card** — if any registered email raises while rendering (bad context, template error, KeyError), `build_site` **re-raises** and the CI job fails red; the site is not republished with a broken/blank card. (Contrast the old `describe_cms_screenshots`, which swallowed per-page errors — for copy *approval* a silent hole is worse than a red build.) The build prints a per-email `ok/FAILED` line first so the failing email is obvious in the log.
  - **Header meta (M6)** — "N emails across M sections · built &lt;date&gt; · v&lt;VERSION&gt; · &lt;short git SHA&gt; · seeded sample data only." `VERSION` from `plfog.version.VERSION`; the short SHA from `git rev-parse --short HEAD` (in CI, `GITHUB_SHA[:7]` when the git call isn't available), so a reviewer can tell exactly which build/commit a given approval was made against.
- **Dark / light:** N/A for page chrome (single neutral theme, no controls). Each email renders in its own shell; the reviewer sees the true dark branded email. State this explicitly so no themed page control is introduced.
- **Mobile:** the sidebar collapses to a horizontal scrolling nav (or a plain top list) under ~700px; cards are full-width; iframes are `width:100%`; the `<pre>` plain-text wraps/scrolls within its card. 8px spacing grid. No horizontal page scroll.

## 7. Notifications / emails / activity

None sent. This feature *renders* emails for review; it never delivers anything and adds no triggers, activity rows, or spine events. (It reads `all_events()` and the copy modules; it does not `emit()`.)

## 8. Build order (phased; each phase ships green)

1. **Registry + discovery + completeness guard** (`registry.py`) with the `GalleryEmail` dataclass, `SECTIONS`, `STRUCTURAL_EMAILS`, the spine derivation, `_TRIGGER_NOTES`, `discover_template_pairs()`, `registered_template_pairs()`. Land the **non-gated completeness spec** (`tests/core/email_gallery_completeness_spec.py`) — green in normal CI. (This alone makes "every template pair is registered" a CI invariant.)
2. **Context builders** (`context.py`) + **render** (`build.py: render_one`) — render each family's HTML/text. Unit-cover a representative email per renderer in the gated spec.
3. **Site writer** (`build.py: build_site`) + the **gated capture spec** (`tests/e2e/email_gallery_spec.py`): seed via `db`, render all, write `index.html`, assert every registered email rendered (no swallow). Verify locally (§ below).
4. **Workflow swap** (`copy-review.yml`): capture step now builds the email gallery into `site/`; drop the page-capture from the published path; leave `describe_feature_screenshots` untouched. **Local preview script** (`scripts/build-email-gallery.sh`).
5. **Housekeeping:** bump `plfog/version.py` `VERSION`. **No CHANGELOG entry** (internal tool, invisible to members) — per CLAUDE.md, fold silently into the shipping batch.

> Spec only — do not build until approved.

### Workflow diff (`.github/workflows/copy-review.yml`)

> **B1 — do NOT drop Playwright wholesale.** `describe_feature_screenshots` (the release-email R2 feature-shot capture) is gated **only** by `CAPTURE_SCREENSHOTS`, and `copy-review.yml` is the **only** workflow that sets it (`playwright.yml` runs `pytest -m e2e` *without* it, so the whole `screenshots_spec` module is `skipif`-skipped there; `release.yml` only tags). Removing the `CAPTURE_SCREENSHOTS` run + the browser install would **permanently stop** the R2 feature-shot pipeline. So this change **rehomes** that capture as its own step in the same job rather than deleting it.

The build job keeps its structure (checkout → configure-pages → python → deps → **browser** → capture → CNAME → upload → deploy) and gains a second capture. Move env to per-step:

- Update the header comment: it builds the **email copy gallery** (was: CMS page screenshots) and still captures release-email feature shots.
- **Keep** the **"Install Playwright browser"** step (needed by the feature-shot capture below).
- **New step — "Build email copy gallery"** (browserless; this is what publishes):
  `env: { BUILD_EMAIL_GALLERY: "1", SHOT_DIR: site }` → `pytest -m e2e --no-cov -q tests/e2e/email_gallery_spec.py`. Writes `site/`.
- **Retained step — "Capture release-email feature shots (R2)"** (replaces the old CMS-gallery run):
  `env: { CAPTURE_SCREENSHOTS: "1" }` (plus the existing R2 secrets the pipeline already relies on) → `pytest -m e2e --no-cov -q tests/e2e/screenshots_spec.py::describe_feature_screenshots`. The `::describe_feature_screenshots` node-id **scopes the run to just that block** so `describe_cms_screenshots` (which shares the `CAPTURE_SCREENSHOTS` module gate) does **not** also fire — the CMS page gallery stops being built/published, exactly as intended, while the R2 feature-shot capture keeps running unchanged. Optionally mark this step `continue-on-error: true` so a feature-shot hiccup never blocks the copy-review publish.
- `CNAME` write / `upload-pages-artifact` (path `site`) / `deploy` job / `concurrency` / triggers — **unchanged**. Only `site/` (the email gallery) is uploaded to Pages; the R2 shots go to object storage, not the artifact.

**Alternative (equally acceptable):** split `describe_feature_screenshots` into its own `feature-screenshots.yml` (`push: main` + `workflow_dispatch`, `CAPTURE_SCREENSHOTS=1`, Playwright, R2 secrets, no Pages) and make `copy-review.yml` browserless + email-gallery-only. Either way the R2 capture is explicitly preserved in this same change — pick one during build; the in-job step keeps it to a single workflow.

`describe_cms_screenshots` / `_write_index` stay in the repo (still runnable via `scripts/capture-cms-screenshots.sh` for anyone who wants page shots) but no longer feed the published site. `describe_feature_screenshots` itself is **not modified**.

### Local preview (one command)

`scripts/build-email-gallery.sh` (mirrors `capture-cms-screenshots.sh`):

```sh
export BUILD_EMAIL_GALLERY=1
export SHOT_DIR="${SHOT_DIR:-email-gallery}"
pytest -m e2e --no-cov -s tests/e2e/email_gallery_spec.py "$@"
echo "Built. Preview it:"
echo "  python -m http.server 8000 -d ${SHOT_DIR}   # then open http://localhost:8000"
```

Runs in the `plfog-web` Docker image or the repo `.venv` (browserless — needs only Django + factories + pytest, no Chromium). The `db` fixture uses a **throwaway test DB** and rolls back, so it never touches Josh's dev SQLite. Because iframes use inline `srcdoc`, `email-gallery/index.html` also opens directly over `file://`, but the `http.server` line is the clean way to preview exactly what CI publishes. (A static file server on `localhost` is fine here — it is not the Django app, so `ALLOWED_HOSTS`/cookie-domain rules don't apply.)

## 9. Testing

BDD specs, `describe_*`/`it_*`, run in the `plfog-web` image.

- **`tests/core/email_gallery_completeness_spec.py`** (normal suite, **not** gated, no DB):
  - `it_registers_every_discovered_template_pair` — `discover_template_pairs() - registered_template_pairs() == set()`. This is the guard that fails CI when a new email template is added un-registered.
  - `it_discovers_the_expected_23_pairs` — pins the discovered set (guards the discovery rule against accidentally sweeping in a shell/partial).
  - `it_excludes_shells_and_partials` — assert `_base`, `_footer`, `_release_shell`, `notification_shell`, `base_message` are **not** discovered.
  - `it_registers_every_inline_string_send` (**B3**) — `discover_inline_sends()` returns the `trigger_kind` set of every direct `core.email.send` call; assert each is registered **or** in `_INLINE_EMAIL_ALLOWLIST`. Pins the five current sites so a new inline send fails CI.
  - `it_covers_every_email_section` — every `GalleryEmail.section` ∈ `SECTIONS`; every spine event is either carded or in the no-email list (nothing falls through).
  - `it_dedups_review_decision_two_keys` (**M1**) — assert `instructor_class_approved` **and** `instructor_changes_requested` both resolve to the single `review_decision` entry (a set-valued `event_keys`, not one).
  - `it_renders_charge_failed_admin_despite_member_off` (**M2**) — `tab_charge_failed` (member EMAIL default OFF) is carded via `charge_failed_admin`, not listed under "No email is sent".
- **`tests/e2e/email_gallery_spec.py`** (gated on `BUILD_EMAIL_GALLERY`; the build itself is the test):
  - `it_builds_the_gallery(db)` — seed, `build_site(SHOT_DIR)`, assert `index.html` exists, contains a card anchor for every registered email, and that **every** email rendered (the writer raises on any failure, so a green run proves 100% rendered — no silent holes).
  - `describe_render_one`: one `it_*` per renderer type (SPINE_COPY, SHELL_TEMPLATE, WELCOME, RELEASE, ANNOUNCEMENT, ALLAUTH) asserting a non-empty subject + branded HTML (contains the shell's `Past Lives` / card markup) and no `[missing: …]` marker leaked into a rendered body.
  - `it_dedups_shell_backed_events` — an event whose email ships via a structural template (e.g. `registration_confirmed`) appears **once**, as the structural card, not twice.
- **Gotchas:** builders create objects with future-dated sessions (like `_seed`) so date copy reads sensibly; timezone rendering matches the send path (subject + body one tz); the spec uses the transactional `db` fixture (not the browser fixtures) so it never needs Chromium or `live_server`.

## 10. Open / deferred

- **Opt-in (EMAIL default OFF) copy** — per the locked decision these are **listed** in the no-email section, not carded. A member who opts in *can* receive that copy, so if the copy team later wants to review it too, flipping OFF events from "listed" to "carded" is a one-line change in the family-1 derivation. Flagged, not built.
- **Discord / in-app / push copy** — out of scope. This gallery is **email** copy only; the Discord embed / bell-row copy for the same events is reviewed elsewhere (the hub notifications admin). Could be a sibling gallery later.
- **Per-recipient variants** — one representative sample context per email; we don't fan out every branch (e.g. `review_decision`'s approved-vs-changes-requested outcomes, series vs single class). Notably the **admin-CC variant of the new-registration notice** (`send_admin_registration_notification`, `classes/emails.py:238`) reuses the `instructor_new_registration` template with an admin audience — the gallery renders the one `instructor_new_registration` card and does **not** add a separate admin-CC card (**M4**). If reviewers want the variants, add extra `GalleryEmail` rows with different context builders — deferred until asked.
- **Free-text "email the class" blasts** (**M3**) — the instructor and admin class-message forms (`classes/forms.py:913` `classes.instructor_message`, `:984` `classes.admin_message`) send an author-typed subject + body straight through `core.email.send` with **no app-authored copy and no branded shell**. There is nothing standing to approve (the content is written fresh each time by a human), so they are **out of scope** for the gallery — allowlisted in Guard 2, not carded.
- **Packaged allauth templates** (**M4**) — only the overridden templates under `templates/account/email/` (login-code, unknown-account, account-already-exists) are in scope. allauth's built-in email templates for flows this app never uses (password reset, email-verification-by-password signup, etc.) are **not** rendered — the passwordless login-code flow never fires them, so there's no live copy to approve.
- **Screenshot/thumbnail rendering of the emails** — not needed; the live `srcdoc` iframe *is* the rendering. No Playwright, no R2.
- **Auth / access** — unchanged: stays public/unlisted, sample data only, built in CI against a fresh test DB. No real member data ever enters a build (hard rule, restated).
- **Retiring `describe_cms_screenshots`** — left in place (dead for the published site, live for the local script). Deleting it entirely is a separate cleanup, deferred so this change stays a pure "swap what's published."

> Spec only — do not build until approved.
