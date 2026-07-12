# Announcement Compose Wizard — Drafts + Discord @mentions — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-12
**Surface:** FOG hub (`pastlives.test`) — a new `/announcements/compose/` page; replaces the two hand-duplicated composers (Site Settings → Announcements *plain* mode, and the guild-edit "post an announcement" form).
**Related:**
- `docs/superpowers/plans/2026-07-03-guild-announcement-discord-channel-picker.md` (the channel picker this generalizes)
- `docs/superpowers/plans/2026-07-03-member-submitted-guild-announcements.md` (the *propose → review* flow — stays separate; see §10)
- `docs/superpowers/plans/2026-06-28-announcements-emails-tab.md` (the guild Announcements/Emails tab that hosts the guild composer today)
- `docs/superpowers/plans/2026-07-04-release-email-redesign.md` (the Release-mode composer — stays separate; its live-preview + "send test to me" patterns are reused here)
- **Feature C** (fogstorm sibling — *scheduled announcement send-time* / `publish_at`): scheduling is deferred to it (§10).

---

## 1. Summary

Today there are **two hand-duplicated composers** for sending an announcement: an admin one (Site Settings → Announcements → plain mode) that blasts every activated member, and a guild one (on the guild-edit page) that posts to a guild's members. They share almost nothing but do almost the same job, and neither can be **saved and finished later**.

This feature replaces both with **one step-through wizard** at `/announcements/compose/`:

1. **Who + what** — pick the audience (site-wide, or a specific guild you can edit) and write the message (title + rich body).
2. **Email** — flip "also send as email" on to design it, with a **live preview** of the exact branded email and a "send a test to me" button.
3. **Discord** — pick which channel it echoes to (audience-appropriate options) and, opt-in and off by default, whether to ping **@everyone / @here**.

Anything can be **saved as a draft** and resumed or deleted later. Drafts are new persisted state (the member-proposal flow deliberately had none; the composer gets real drafts). @everyone/@here is genuinely new Discord plumbing — the embed payload builder emits no mention today.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| One composer or two | **One wizard** replaces the site-wide + guild composers. Event-creation "announce" options stay inline on the event form (out of scope). |
| Audience model | **Site-wide** (all activated members) **or one guild** (that guild's joined members). No arbitrary segments. |
| Drafts | **Persisted** (`AnnouncementDraft` model): save without sending, list, resume, delete. |
| @everyone/@here | **Opt-in for anyone who can post, OFF by default.** New payload plumbing (`content` + `allowed_mentions`). |
| Wizard shape | **Single hub page, Alpine `x-show` stepper** (not separate routes). One `<form>`; stepper is Alpine state (nested-form safety). |
| Send transition | **Mark-sent** (`sent_at` stamp), not delete-on-send — satisfies the "was it sent" field, keeps an audit row, and makes the resume list a trivial `sent_at IS NULL` filter. A **guild** send *also* materializes a published `GuildAnnouncement` (the durable on-page post shown on the guild page/list/slideshow); a **site** send stays ephemeral, no durable post — see §2 gap 5, §5, §7. |
| Release composer | **Untouched** — it's a different beast (sectioned cards from the changelog). Only the *plain* site composer and the guild composer are folded in. |
| Member-proposal flow | **Untouched** — the propose→review path is for members who *can't* post directly. This wizard is for people who can. |

---

## 2. What already exists (reuse, don't reinvent)

Every row below was confirmed in the codebase at the cited line.

| Need | Existing thing | Location |
|---|---|---|
| Site-wide send (build branded email, emit to all-active, suppress-Discord flag) | `_send_site_announcement` | `hub/views.py:4006` |
| Site composer POST dispatch (preview vs send) | `_handle_announcement_action` | `hub/views.py:4049` |
| Site composer form (title / body Quill / post_to_discord) | `SiteAnnouncementForm` | `hub/forms.py:1498` |
| Branded announcement email HTML (title `<h2>` + `render_rich_email_body` + `wrap_email_html`) | `_announcement_email_html` | `hub/views.py:3989` |
| Live iframe preview | `<iframe srcdoc="{{ … }}">` | `templates/hub/admin/site_settings.html:753` |
| Upgraded preview (HTML/text · desktop/mobile toggle, inbox row) + **"Send test to me"** button pattern | Release composer partial | `templates/hub/admin/_release_announcement.html` |
| Recipient count for the send confirm (site) | `_activated_member_count()` → `resolvers.resolve(Recipients.ALL_ACTIVE_MEMBERS, {})` | `hub/views.py:3977` |
| "Send a copy to only me" (direct send, bypasses the spine + its period) | `send_release_test` | `core/release_email.py:304` |
| Guild composer form (title / body / expires_at / send_email / discord_channel) | `GuildAnnouncementForm` | `hub/forms.py:1367` |
| Guild send (reads send_email / discord_channel → webhook, passes `discord_broadcast_webhook`, `suppress_email`, `suppress_guild_broadcast`) | `GuildAnnouncement.notify_members()` | `membership/models.py:1920` |
| Discord channel radio widget (disables unconfigured channels, `data-hint`) | `ChannelRadioSelect` | `hub/forms.py:1291` |
| Which channels have a webhook set (`guild=None` → GENERAL/LEADERSHIP only) | `_configured_discord_channels()` | `hub/forms.py:1327` |
| Default channel ladder (first configured, else "Don't post") | `_default_discord_channel()` | `hub/forms.py:1351` |
| Channel picker template | `_announcement_channel_picker.html` | `templates/hub/partials/_announcement_channel_picker.html` |
| Channel choices enum (GUILD / GENERAL / LEADERSHIP / NONE) | `GuildAnnouncement.DiscordChannel` | `membership/models.py:1826` |
| Channel → webhook resolver (guild / SiteConfiguration general / leadership / none) | `GuildAnnouncement.resolve_discord_webhook()` | `membership/models.py:1892` |
| Rich editor widget (Quill over hidden textarea) | `RichTextEditorWidget` | `core/widgets.py:8` |
| Rich pipeline (sanitize, HTML→email, HTML→text) | `sanitize_rich_html` / `render_rich_email_body` / `rich_html_to_text` | `core/html_sanitize.py` |
| Quill assets (include once per page) | `_components/rich_editor_assets.html` | included e.g. `templates/hub/guild_edit.html:4` |
| Single emission point (unique `period`, suppress flags, per-channel `messages` override) | `emit()` | `core/events/emit.py:43` |
| Rendered message value object (frozen) | `Message` | `core/events/channels.py:57` |
| Discord broadcast adapter (`webhook_for_event` → `post_embed`) | `DiscordAdapter.broadcast()` | `core/events/channels.py:313` |
| Broadcast fan-out + the `discord_broadcast_webhook` override + `_guild_broadcast` | `_broadcast_fan_out` / `_guild_broadcast` | `core/events/emit.py:186` / `:246` |
| Guild-edit create endpoint (the pattern to retire) | `guild_announcement_create` | `hub/views.py:1801` |
| Edit-permission gate for a guild | `_require_can_edit_guild` (view) / `membership/permissions.py` `can_edit_guild` / `Member.staffed_guilds` | `hub/views.py:1806` + `membership/permissions.py` |
| Themed input scope, channel-picker CSS, card/buttons | `.hub-form-group` / `.pl-channel-picker` / `.hub-card` / `.hub-btn--*` | `static/css/hub.css:838` / `:3470` / `:783` / `:996` |

### Genuine gaps to close (kept minimal)

1. **`AnnouncementDraft` model** + manager (§4). New; additive migration.
2. **@mention payload plumbing (§7).** `build_embed_payload()` (`core/events/discord.py:128`) emits **only** `{"embeds":[…]}` — no top-level `content`, no `allowed_mentions`. Confirmed: `grep allowed_mentions core/ hub/` returns nothing. Three tiny seams: a `discord_mention` field on the frozen `Message`, one `emit()` param that stamps it onto the DISCORD message, and `build_embed_payload` emitting `content` + `allowed_mentions` when set.
3. **Guild-less "chosen webhook" broadcast (§7).** The `discord_broadcast_webhook` override currently only actually *posts* through `_guild_broadcast`, which early-returns when `ctx["guild"]` is absent (`emit.py:268-270`). A **site-wide** announcement that picks #general-chat has no guild — so the override must post without one. Small generalization to the broadcast fan-out.
4. **Audience-scoped channel choices.** Generalize the picker so a site-wide audience offers **#general-chat / #leadership / Don't post** (no "Our Guild Channel" row), reusing `_configured_discord_channels(None, config)` which already yields only GENERAL/LEADERSHIP.
5. **Guild send must materialize a durable `GuildAnnouncement` (§5, §7) — not just emit.** A guild announcement isn't only a notification; it's a **persistent post** shown on the public guild page (`templates/hub/guild_detail.html:159`, rendered `{{ a.body|linebreaksbr }}`), in the guild-edit "Recent Announcements" list (`guild_edit.html:504`), and mirrorable in the signage slideshow (`SlideshowSlide.Kind.ANNOUNCEMENT` → a published `GuildAnnouncement`, `membership/models.py:4304`). Today `guild_announcement_create` **creates the `GuildAnnouncement` row and *then* calls `notify_members()`** (`hub/views.py:1810-1815`). So the wizard's guild send must do the same — create the published `GuildAnnouncement`, then `notify_members(discord_mention=…, email_message=…)` — **not** a bare `emit()`; otherwise every guild post sent through the wizard is invisible on the guild page, the edit list, and the slideshow (a silent regression the test-client can't catch). Two small default-off seams on that method: a `discord_mention` kwarg (the @mention) and an `email_message` kwarg (the branded email override that keeps the Step-2 preview honest — §5). The **site** audience stays emit-only — site-wide announcements have no durable post today and gain none here.

Everything else is assembly.

---

## 3. Where the code lives

```
membership/
  models.py                      # + AnnouncementDraft, AnnouncementDraftManager, AnnouncementDraft.Mention
                                 # + module-level resolve_channel_webhook(channel, guild=None)  (mirrors resolve_discord_webhook)
                                 # + GuildAnnouncement.notify_members(*, discord_mention="", email_message=None)  (guild send reuses it)
  migrations/00XX_announcement_draft.py   # additive; reverse = auto (drop table)
  spec/models/announcement_draft_spec.py  # BDD

hub/
  forms.py                       # + AnnouncementComposeForm (audience-aware; wraps title/body/send_email/discord_channel/mention/expires_at)
                                 # generalize ChannelRadioSelect choices per audience via discord_channel_choices(audience)
  views.py                       # + hub_compose (GET + POST action dispatch), hub_compose_preview (HTMX),
                                 #   hub_compose_save_draft (HTMX), hub_compose_test (HTMX), hub_compose_count (HTMX),
                                 #   hub_compose_send (full-page POST), hub_compose_delete_draft (POST)
                                 # retire _send_site_announcement / _handle_announcement_action + the guild-edit inline *create* form
                                 #   (KEEP GuildAnnouncementForm — the guild "Recent Announcements" edit modal still uses it)
  urls.py                        # + the routes below
  spec/views/announcement_compose_spec.py

core/
  events/channels.py             # Message: + discord_mention: str = ""
  events/discord.py              # build_embed_payload: + content / allowed_mentions when mention set
  events/emit.py                 # emit(): + discord_mention param; message_for(DISCORD) stamps it;
                                 #   generalize the chosen-webhook broadcast to run without a guild
  spec/events/discord_mention_spec.py

templates/hub/
  announcement_compose.html      # the wizard page (one <form>, Alpine stepper)
  partials/_announcement_channel_picker.html   # reused as-is
  partials/_compose_email_preview.html         # HTMX preview target (iframe srcdoc + inbox row)
  partials/_compose_drafts_list.html           # "Your drafts" list (OOB-swappable)

static/css/hub.css                # + .pl-wizard*, .pl-drafts-list / .pl-draft-row; reuse .pl-channel-picker for mentions
plfog/version.py                  # bump VERSION + CHANGELOG (final phase)
```

Home apps: **membership** owns the draft model + webhook resolver (it already owns `GuildAnnouncement`), **hub** owns the page/forms/views (thin glue), **core** owns the spine seam. All three are inside the coverage/mypy scope.

---

## 4. Data model

### `AnnouncementDraft` (membership/models.py, beside `GuildAnnouncement`)

| Field | Type | Note |
|---|---|---|
| `author` | FK → `settings.AUTH_USER_MODEL`, `on_delete=CASCADE`, `related_name="announcement_drafts"` | Whose draft it is; drives `for_user()` and the send actor. |
| `audience` | `CharField(choices=Audience.choices, default=SITE)` | `SITE` (all activated) or `GUILD`. |
| `guild` | FK → `Guild`, `null=True, blank=True, on_delete=CASCADE` | Set **iff** `audience=GUILD`. |
| `title` | `CharField(max_length=300)` | Subject / headline. **Required even to save a draft** (so the drafts-list row has a readable label; the body may still be blank while drafting). |
| `body` | `TextField(blank=True, default="")` | Sanitized rich HTML (`sanitize_rich_html` on save). Blank allowed while drafting; required to *send*. |
| `send_email` | `BooleanField(default=True)` | "Also send as email." |
| `discord_channel` | `CharField(max_length=20, choices=GuildAnnouncement.DiscordChannel.choices, default=NONE)` | Reuses the existing enum. `GUILD` only valid when `audience=GUILD`. Default **NONE** (opt-in to Discord — the site composer today defaults Discord *on*, but a saved draft shouldn't silently fan out; the wizard pre-selects the default ladder in the UI). |
| `mention` | `CharField(max_length=10, choices=Mention.choices, default=NONE)` | `NONE` / `HERE` / `EVERYONE`. |
| `expires_at` | `DateField(null=True, blank=True)` | "Hide after" — carried onto the materialized `GuildAnnouncement.expires_at` on a **guild** send (preserves the expiry the current `GuildAnnouncementForm` offers, which would otherwise be a regression). Blank = never expires; ignored for a site send. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | Drives list ordering. |
| `sent_at` | `DateTimeField(null=True, blank=True)` | **Set on send.** `NULL` = a resumable draft; non-null = an immutable sent record. |

```python
class Audience(models.TextChoices):
    SITE = "site", "Everyone (site-wide)"
    GUILD = "guild", "A specific guild"

class Mention(models.TextChoices):
    NONE = "none", "No ping"
    HERE = "here", "@here (online members)"
    EVERYONE = "everyone", "@everyone"
```

`help_text` on every field (per CLAUDE.md). `__str__` → `f"{self.title} — {self.get_audience_display()} ({'sent' if self.sent_at else 'draft'})"`.

**Meta**
- `ordering = ["-updated_at"]`
- `indexes = [models.Index(fields=["author", "sent_at"], name="idx_%(class)s_author_sent")]` — the resume-list query.
- `constraints`: a `CheckConstraint` that `audience != "guild"` OR `guild IS NOT NULL` (a guild draft must name a guild) — fail loudly, `name="ck_%(class)s_guild_audience"`.

### Manager — `AnnouncementDraftManager`

- `for_user(user) -> QuerySet` → `filter(author=user, sent_at__isnull=True)` (the resume list; newest first via Meta ordering). `select_related("guild")` to avoid N+1 in the list.

### Module-level `resolve_channel_webhook(channel: str, guild: Guild | None = None) -> str`

Mirrors `GuildAnnouncement.resolve_discord_webhook` (`membership/models.py:1892`) but audience-agnostic: `GUILD` → `guild.discord_webhook_url`, `GENERAL`/`LEADERSHIP` → `SiteConfiguration` webhooks, `NONE` → `""`, unknown → `ValueError`. `GuildAnnouncement.resolve_discord_webhook` can delegate to it so there's one resolver.

### Migration

One additive migration: create table + index + check constraint. Reverse = Django's auto table-drop (no data migration, so no custom reverse needed). The `Message.discord_mention` change is a dataclass field, **not** a DB migration.

---

## 5. Business logic (fat models)

Views stay thin; the wizard's logic is model/service methods.

### `AnnouncementDraft.save_from_form(form, author) -> AnnouncementDraft` (manager/classmethod)

Upsert helper the save-draft view calls: creates or updates the row from cleaned form data, sanitizing `body` via `sanitize_rich_html`. Guards: a `GUILD` audience without a `guild` raises `ValidationError` (also enforced by the form and the DB constraint).

### `AnnouncementDraft.build_email_message(base_url) -> Message`

Returns the per-channel EMAIL `Message` (branded HTML + text), reusing the exact pipeline `_announcement_email_html` uses today (`render_rich_email_body` + `wrap_email_html`, escaped title `<h2>`). The current helper lives in `hub/views.py:3989`; move it to a small `membership` helper (or `core`) so both the model and any legacy caller share one implementation. Text part = `rich_html_to_text(body)` (matches `_send_site_announcement`, `hub/views.py:4020`). **One builder for both audiences** — `base_url` is `site_url` for a site send and the `guild_url` for a guild send; the very same call renders the Step-2 preview *and* the override handed to the site `emit()` / the guild `notify_members(email_message=…)`, so the preview is always byte-faithful to what sends.

### `AnnouncementDraft.send() -> int`  ← the transition

The single "fat" method. Guards, then **branches on audience** — a site send is ephemeral (emit only, as today); a guild send **materializes a durable post** — and returns the recipient count:

1. **Guard:** raise `AlreadySentError` if `sent_at` is set; raise `ValidationError` if `body` sanitizes empty ("Add a message before sending.") or if `audience=GUILD` without a `guild`.
2. Map mention → the ping string: `""` / `"@here"` / `"@everyone"`. (Defensive: the mention only matters when a webhook resolves — a lingering `@everyone` with "Don't post to Discord" is a harmless no-op, since the blank webhook suppresses the broadcast.) Build the absolute base URL with `_absolute_url` (no `request` needed, so this stays a model method) — `site_url = _absolute_url("/")` for a site send, `guild_url = _absolute_url(reverse("hub_guild_detail", args=[self.guild.slug]))` for a guild send (the same URL `notify_members` builds, `membership/models.py:1943`).
3. **Site audience** (ephemeral, matches today) → build the branded EMAIL `Message` (`build_email_message`), resolve the chosen #general/#leadership webhook via `resolve_channel_webhook(self.discord_channel)` (no guild), and `emit("site_announcement", …)` with `discord_broadcast_webhook`, `suppress_broadcast=(webhook=="")`, `suppress_email=not send_email`, `discord_mention=mention_str`, and the timestamp-unique `period` below (§7). Recipient count = `result.recipient_count`.
4. **Guild audience** → **materialize a published `GuildAnnouncement`**, exactly as `guild_announcement_create` does (`hub/views.py:1810-1815`): create it with `guild=self.guild`, `author=self.author`, `title=self.title`, `body=rich_html_to_text(self.body)`, `expires_at=self.expires_at`, `send_email=self.send_email`, `discord_channel=self.discord_channel` (its default `moderation_state=PUBLISHED` makes it live immediately), then call `announcement.notify_members(discord_mention=mention_str, email_message=self.build_email_message(guild_url))`. This reuses the tested guild fan-out — webhook resolution, suppress flags, the per-announcement `announcement:{pk}` period — and, crucially, makes the post appear on the guild page, the "Recent Announcements" list, and the slideshow. Recipient count = `self.recipient_count()` (`notify_members` returns `None`).
   - **Why store plain text on the post?** The guild page + slideshow render the body as **plain text** (`{{ a.body|linebreaksbr }}`, `guild_detail.html:159`), so `rich_html_to_text(self.body)` keeps every existing guild-page/slideshow render byte-identical and touches **no display template** — the rich formatting still shows in the branded *email* (same tradeoff the site composer already makes: rich email, plain bell/Discord). The draft's own `body` keeps the rich HTML, so a resumed or duplicated draft never loses its formatting.
   - **Why pass `email_message` into `notify_members`?** Today `notify_members` renders its email from **copy-mode** (the seeded `guild_announcement` copy), *not* a branded HTML override — so without this, the guild email that actually sends would **not** match the wizard's Step-2 "exact branded email" preview (the preview would lie). Passing the same `build_email_message(...)` the preview renders makes both audiences send — and preview — the one branded shell.
5. Stamp `sent_at = timezone.now()`, `save(update_fields=["sent_at", "updated_at"])`.
6. Return the recipient count.

`notify_members` gains exactly **two** optional keyword params — `def notify_members(self, *, discord_mention: str = "", email_message: Message | None = None) -> None`. `discord_mention` threads into its existing `emit(...)`; `email_message`, when given, is passed as `messages={Channel.EMAIL: email_message}` (else `None` → today's copy-mode email). Every current caller (the create view, the proposal-approve path) passes neither and is byte-unaffected (`membership/models.py:1945`).

Deterministic-but-unique `period` (site path): `f"announce:{self.pk}:{timezone.now():%Y%m%d%H%M%S%f}"` — mark-sent already blocks a re-send, but a timestamp-unique period matches the existing composers (`hub/views.py:4042`, `:4109`) and stays safe if that guard is ever relaxed. (The guild path keeps `notify_members`' own `announcement:{GuildAnnouncement.pk}` period — a fresh pk each send, so it never collides.)

### `AnnouncementDraft.recipient_count() -> int` (property/method for the confirm dialog)

- `SITE` → `_activated_member_count()`'s audience (`resolvers.resolve(Recipients.ALL_ACTIVE_MEMBERS, {})`).
- `GUILD` → the guild-announcement resolver audience for `{"guild": self.guild}` (the same audience `notify_members` fans out to). Both via `resolvers.resolve(...)`, mirroring `hub/views.py:3977`.

### Permissions (who may pick which audience)

Not a new permission system — reuse the existing gates:
- **Site-wide** audience is offered only to fog admins (the same gate as today's `admin_site_settings`, `hub/views.py:4183`).
- **Guild** audience is offered for guilds the user can edit: `Member.staffed_guilds` + led guilds (admins: all active guilds). The send view re-checks `can_edit_guild` / the admin gate server-side before `send()` — never trust the posted `audience`/`guild`. A member with neither → they only see the (separate) *propose* flow, unchanged.

Domain exceptions: `AlreadySentError(Exception)` for a double-send; `ValidationError` for empty body / missing guild.

---

## 6. UI / UX  ← completeness checklist applied concretely

### Screen: `templates/hub/announcement_compose.html` (dedicated page, `/announcements/compose/`)

Dedicated page (not a modal) — it's a 6+-field, multi-step task (FRONTEND.md interaction table: 4+ fields → dedicated page). Wrapped in `<div class="hub-card">`. **One `<form method="post">`** for the whole wizard; the stepper is Alpine state only — no nested `<form>` (nested-form orphaning is a known bug, see MEMORY `reference_nested_form_save_bug`).

**Page skeleton**

```html
<div class="hub-card pl-wizard"
     x-data="{ step: {{ start_step }},
               audience: '{{ audience_value }}',        {# 'site' or 'guild:<pk>' — the Step-1 <select> value #}
               alsoEmail: {{ form.send_email.value|yesno:'true,false' }},
               mention: '{{ form.mention.value }}',
               recipientCount: {{ initial_recipient_count }} }"
     @compose-count.window="recipientCount = $event.detail.count">   {# server pushes the live count on audience change #}
  <nav class="pl-wizard-nav" aria-label="Compose progress"> …1 · 2 · 3… </nav>   {# progress indicator; move with Back/Next, not by clicking #}

  <form method="post" action="{% url 'hub_compose_send' %}" x-ref="wizardForm">
    {% csrf_token %}
    <input type="hidden" name="draft_pk" value="{{ draft.pk|default:'' }}">
    {# NO hidden `audience` input — the Step-1 <select name="audience"> is the single source. A step
       hidden by x-show is display:none but STILL submits its value, so a second input would double-post. #}

    <section class="pl-wizard-step" x-show="step === 1"> … Step 1 … </section>
    <section class="pl-wizard-step" x-show="step === 2"> … Step 2 … </section>
    <section class="pl-wizard-step" x-show="step === 3"> … Step 3 … </section>

    <div class="pl-wizard-actions"> … Back / Next / Save draft / Send … </div>
  </form>
</div>

{% include "hub/partials/_compose_drafts_list.html" %}
```

> **Rule 12:** `.pl-wizard-step` carries `display:block` in `hub.css`; the inline attribute is only `x-show` — never `style="display:…"` on an `x-show` element (Alpine strips it on reveal and the step collapses).
>
> **The submit-button trap.** Every wizard button **except the final Send** is `type="button"` — a bare `<button>` inside a `<form>` defaults to `type="submit"`, so an un-typed Back / Next / Save draft / Refresh preview / Send-test would fire the form's real submit (= send the announcement). Only the one **Send** control is `type="submit"`; everything else is `type="button"` (and the HTMX buttons carry their own `hx-post`, which overrides the form `action` for that request).

**Components used:** `form_field.html` (title, and the audience `<select>`), `toggle.html` ("also send as email", the send_email boolean), the existing `_announcement_channel_picker.html` (the Discord channel radios; the separate @mention radios are hand-rolled but reuse its `.pl-channel-picker`/`.pl-channel-option` styling), `confirm_modal.html` (**draft delete only** — the Send gate is a native confirm, not this component, because it can't carry the wizard form; see Step 3), `trigger_toast()` for HTMX feedback. Quill via `RichTextEditorWidget` with `{% include "_components/rich_editor_assets.html" %}` once near the top.

---

#### Step 1 — Audience + message

- **Audience** — a single `form_field.html` `<select name="audience">` inside `.hub-form-group` (theme-correct; `select option { background; color }` covered by the hub-form-group scope). **One combined value per option** — `site` for **Everyone (site-wide)** (rendered only for fog admins) and `guild:<pk>` for each entry in an `<optgroup>` of **your guilds**. `AnnouncementComposeForm.clean` splits that value into `audience` (`SITE`/`GUILD`) + `guild` (the pk), so the model's two fields + the check constraint stay the source of truth while the UI stays one control. Bound to `x-model="audience"`, and it carries `hx-get="{% url 'hub_compose_count' %}" hx-trigger="change" hx-include="[name=audience]" hx-swap="none"` so switching audience refreshes the live recipient count (below) even before any preview/save; the Step-3 channel options re-scope server-side on the next preview/save. Empty-permission users never reach this page (the view 403s / redirects them to the propose flow).
- **Title** — `form_field.html` with `form.title` (label "Subject").
- **Message** — `form_field.html` with `form.body` (the Quill editor). Wrapped by the field group so the hidden textarea + `.pl-rte` mount inherit input tokens.
- **"Hide after" (guild only)** — `form_field.html` with `form.expires_at`, a native `<input type="date">`, shown only for a guild audience (`x-show="audience.startsWith('guild')"`, its display in a CSS class per Rule 12; ignored server-side for a site send). It preserves the current guild composer's expiry. Being a native date input, it needs the **Rule 14** dark-mode help: invert the picker icon (`filter: invert(1)`, reset under `[data-theme="light"]`) and open it from the whole field (`@click="try { $event.currentTarget.showPicker() } catch (e) {}"`).
- **Controls:** a **"Next: email →"** button (`hub-btn hub-btn--primary`, `@click="step = 2"`, `type="button"` so it doesn't submit).
- **Validation:** title required; body required *to send* (may be blank in a draft). `AnnouncementComposeForm.clean_body` → `sanitize_rich_html`, "Add a message before sending." on an empty send (mirrors `SiteAnnouncementForm.clean_body`, `hub/forms.py:1520`). A guild audience with no `guild` → "Choose a guild for this announcement."

#### Step 2 — Email design + live preview

- **"Also send as email"** toggle (`toggle.html`, `form.send_email`, `x-model="alsoEmail"`, default ON). When OFF, the preview region is hidden (`x-show="alsoEmail"`) and members get only the in-app bell (+ Discord if chosen). Its wrapper display lives in a CSS class, not inline (Rule 12).
- **Live preview** (`x-show="alsoEmail"`, region class `.pl-wizard-preview`):
  - An **iframe** `srcdoc` target inside `#compose-preview`, reusing the branded-wrap → the *exact* email (`_compose_email_preview.html`, modeled on `_release_announcement.html`'s inbox row + `<iframe class="pl-email-preview__iframe">`). **`#compose-preview` ships with a placeholder** ("Building your preview…") so the pane is never blank between entering Step 2 and the first swap landing (loading state — see *States*).
  - A **"Refresh preview"** button (`type="button"`): `hx-post="{% url 'hub_compose_preview' %}"`, `hx-include="closest form"`, `hx-target="#compose-preview"`, `hx-swap="innerHTML"`, plus an `.htmx-indicator` spinner on the button while in flight. The server sanitizes + brands the current title/body and returns the iframe partial (Quill HTML can't be branded client-side — the server owns it). Auto-fires once on entering Step 2 (`x-init`/`@click` triggering the button) so the pane fills without a manual click.
  - A **"Send a test to me"** button (`type="button"`): `hx-post="{% url 'hub_compose_test' %}"`, `hx-include="closest form"`, `hx-swap="none"` → a direct `core.email.send` to the author (reusing `send_release_test`'s bypass-the-spine pattern, `core/release_email.py:304`), then `trigger_toast("Test sent to <your email>.")`. Never touches `EventDelivery`/`period`, so it can't consume the real send's slot. (The author is always a logged-in admin/editor with a usable email; if `request.user.email` is somehow blank the view returns an error toast rather than a silent no-op.)
- **Controls:** **"← Back"** (`type="button"`, `@click="step = 1"`) and **"Next: Discord →"** (`type="button"`, `@click="step = 3"`).

#### Step 3 — Discord

- **Channel picker** — `_announcement_channel_picker.html` with `form.discord_channel`, wrapped in `#compose-discord-picker` (the OOB target the audience-change refresh re-scopes into). Audience-appropriate choices (§7): site-wide → **#general-chat / #leadership / Don't post to Discord**; guild → **Our Guild Channel / #general-chat / #leadership / Don't post**. Unconfigured channels render as disabled `<input disabled>` with "Not set up yet." (`ChannelRadioSelect`, unchanged). Default = the configured ladder (`_default_discord_channel`).
- **Ping @everyone / @here** — a second radio group rendered with the **same** `.pl-channel-picker` / `.pl-channel-option` card styling (reuse, no new picker class), `x-model="mention"`, options **No ping (default)** / **@here (online members)** / **@everyone (all members)**. A warning hint is revealed only when a ping is selected: `<p class="pl-field-hint pl-field-hint--warn" x-show="mention !== 'none'">This pings everyone in the channel — use it sparingly.</p>`. Its display comes from the class, per Rule 12; the warn color is a **theme token** (`--color-tuscan-yellow` accent), defined for **both** themes — never a hardcoded hex. Disabled/hidden entirely when the chosen channel is "Don't post to Discord" (`x-show` on a non-NONE channel) — a ping with nowhere to post is meaningless. **Scope caveat (see §10, needs Josh's call):** picking `#general-chat` + `@everyone` from a single guild's announcement pings the *whole server* — the locked decision allows it for anyone who can post, but whether the shared #general/#leadership pings should be admin-only is an open policy question.
- **Reaches N line** — above the Send button, a live `<p>Reaches <strong x-text="recipientCount"></strong> member(s).</p>` bound to the reactive `recipientCount` (seeded server-side for the initial audience, refreshed by the `hub_compose_count` call on every audience change — works whether or not "also email" is on, so the count is never stale or blank). The server recomputes the true count at send time and is authoritative; this line is the user-facing courtesy.
- **Controls (the primary actions live here, and in `.pl-wizard-actions` on every step):**
  - **Save draft** (`type="button"`) — `hx-post="{% url 'hub_compose_save_draft' %}"`, `hx-include="closest form"`, `hx-swap="none"`. Upserts by `draft_pk`; on a **valid** form returns `trigger_toast("Draft saved.")` **plus** an OOB swap of the hidden `draft_pk` (so the next save updates the same row, no duplicates) and of `#compose-drafts` (the list). On an **invalid** form (e.g. a blank title — title is required even to save) it returns an **error toast** ("Add a subject before saving.") and swaps nothing — never a silent success. Button `hub-btn hub-btn--ghost`.
  - **Send** — the form's real submit and the **only** `type="submit"` control (`hub-btn hub-btn--primary`). It is **not** `confirm_modal.html`: that component POSTs its own little form to a URL, which would submit an *empty* body (no title/message/channel) and lose the whole wizard. Instead, gate it inline in the spirit of the release composer's `onclick="return confirm(...)"` (`_release_announcement.html:64`), but read the **live** Alpine count via `@click="if (!confirm(`Send to ${recipientCount} member(s)? This can't be undone.`)) $event.preventDefault()"` — `$event.preventDefault()` on the click cancels the submit when the user backs out (a plain `onclick="return confirm(...)"` can't see the reactive `recipientCount`). Full-page POST → `hub_compose_send` re-checks the permission for the posted audience server-side (never trust the client), calls `send()`, and redirects to `/announcements/compose/` with a Django success message "Announcement sent to N member(s)." (full-page posts use Django messages, per FRONTEND.md; HTMX mutations use toasts). `confirm_modal.html` is used **only** for the draft *delete* below (a genuine single-URL POST).

#### Screen: `_compose_drafts_list.html` — "Your drafts"

- A `.hub-card` section (`id="compose-drafts"`) listing `AnnouncementDraft.objects.for_user(request.user)`.
- **Header row:** the "Your drafts" heading + a **"+ New announcement"** button (`hub-btn hub-btn--sm`, → `/announcements/compose/`) — the explicit "start fresh" affordance so a user resuming draft `<pk>` (or sitting on any list) can always begin a blank one without hand-editing the URL. (This is the list-editor "+ Add" the checklist demands, adapted: a wizard is one row at a time, so "add" = start a new compose.)
- Per row (`.pl-draft-row`): title, audience label (site / guild name), "updated 2h ago", a **Resume** link (`hub-btn hub-btn--sm`, → `/announcements/compose/<pk>/`), and a **Delete** button.
- **Delete** — `hub-btn hub-btn--danger hub-btn--sm` opening a **per-row** `confirm_modal.html` (unique `confirm_id="del-draft-{{ d.pk }}"`, like the guild-edit `del-ann-<pk>` pattern; "Delete this draft? This can't be undone." → POST `hub_compose_delete_draft`), margin-spaced so it clears the row above (button standards; MEMORY `feedback_button_standards`). On success: `trigger_toast("Draft deleted.")` + OOB-swap the refreshed list.
- **Resume robustness:** `hub_compose` for a `<draft_pk>` fetches `get_object_or_404(AnnouncementDraft, pk=draft_pk, author=request.user, sent_at__isnull=True)` — you can never resume someone else's draft or an already-sent (immutable) row; a stale/foreign pk 404s rather than loading another member's content.

**Routes**

```python
path("announcements/compose/", views.hub_compose, name="hub_compose"),
path("announcements/compose/<int:draft_pk>/", views.hub_compose, name="hub_compose_resume"),
path("announcements/compose/preview/", views.hub_compose_preview, name="hub_compose_preview"),
path("announcements/compose/count/", views.hub_compose_count, name="hub_compose_count"),
path("announcements/compose/test/", views.hub_compose_test, name="hub_compose_test"),
path("announcements/compose/save/", views.hub_compose_save_draft, name="hub_compose_save_draft"),
path("announcements/compose/send/", views.hub_compose_send, name="hub_compose_send"),
path("announcements/compose/<int:draft_pk>/delete/", views.hub_compose_delete_draft, name="hub_compose_delete_draft"),
```

`hub_compose_count` is a small HTMX GET fired when the audience changes: it reads the posted `audience`, re-checks the caller may address it, and does two things in one round-trip — (1) sets the live count via an `HX-Trigger` header `{"compose-count": {"count": N}}` that the page's `@compose-count.window` listener writes into `recipientCount`, and (2) returns the **re-scoped Step-3 channel picker** as an `hx-swap-oob` partial (target `#compose-discord-picker`), so switching site↔guild immediately drops/adds the "Our Guild Channel" row instead of leaving a stale option that would only fail `clean_discord_channel` at send. (Even so, the send view re-validates the channel against the audience — the client is never trusted.)

Entry points that replace the old composers:
- The guild-edit **"Post an Announcement"** *create* form (`guild_edit.html:489`) is removed and replaced by a **"Compose announcement →"** button linking to the wizard **pre-scoped**: `?audience=guild:<pk>` (one combined param, matching the Step-1 select value). The guild-edit **"Recent Announcements"** list (with its own edit/delete of already-published posts) and the **guild follow-up emails** form on that tab **stay** — the wizard is compose-and-send, not an edit surface for live posts.
- The Site Settings → Announcements **plain-mode** composer (`_send_site_announcement` / `_handle_announcement_action`, `site_settings.html:704-756`) is removed and replaced by a link to the wizard. **Release mode** (`_release_announcement.html`), the **member-proposal review queue**, and the guild **published list** all stay put and untouched.

### States

- **Empty** (no drafts): the "Your drafts" card shows "No saved drafts yet — save one to pick it up later." Not a bare region.
- **Loading:** `#compose-preview` renders a "Building your preview…" placeholder that the first (auto-fired) swap replaces, so the pane is never blank; the preview refresh and test-send show an `.htmx-indicator` spinner on their buttons while in flight (HTMX toggles `hx-indicator`). The audience-count refresh is a headers-only 204, so it has no visible in-flight state (the number simply updates).
- **Error:**
  - Per-step validation re-renders the wizard on the offending step with the field errors (`form_field.html` shows them). Empty body on send → "Add a message before sending."; guild audience without a guild → "Choose a guild for this announcement."
  - A **bad / unconfigured Discord channel** can't be picked — the picker disables it and `clean_discord_channel` rejects it with the existing `_CHANNEL_UNCONFIGURED_ERROR` (`hub/forms.py:1364`). A blank webhook at send simply posts nothing (best-effort; `post_embed` logs and returns False — never a 500).
  - Posting an audience you can't send to → server-side 403 (permission re-check), never a silent send.
- **Success:** send → Django message + redirect ("sent to N member(s)"); save-draft / test / delete → toast. No dead ends — every step has Back, and Save draft/Send are always reachable from the actions bar.

### Dark + light

- Every control is theme-tokened: title/audience `<select>` and the Quill mount sit inside `.hub-form-group` (input tokens `--hub-input-bg` / `--hub-input-border` / `--text`); `select option { background; color }` is covered by that scope. Quill + the iframe preview are already proven on `site_settings.html` / `guild_edit.html` (both themes).
- The channel picker + mention radios reuse `.pl-channel-picker` / `.pl-channel-option` (`hub.css:3470`), which already styles both themes.
- The preview iframe keeps `background:#fff` (it's rendering the branded *email*, which is dark-on-its-own-card) inside a `--hub-border` frame — matching the existing composer.
- **One native `date` input** — the guild-only "Hide after" (`expires_at`); it gets the Rule 14 dark-mode treatment (invert the picker icon, reset under `[data-theme="light"]`, `showPicker()` on the whole field). No `time` inputs (scheduling is deferred, §10). New `.pl-wizard*` classes define colors from tokens only. The spec bar: **verify both themes** on the stepper, toggles, radios, the date field, and preview.

### Mobile

- Stepper nav wraps; steps are full-width and stack. `.pl-wizard-actions` is `flex-wrap` so Back/Next/Save/Send never overflow.
- The preview lives in a contained `.pl-wizard-preview` region: fixed height + `overflow:auto`, iframe `width:100%` — it scrolls inside its box, the page body never scrolls horizontally.
- The drafts list rows stack (title/meta over the Resume/Delete buttons) below ~480px. Tap targets are real buttons. 8px-grid spacing throughout.

---

## 7. Notifications / emails / Discord

Reuse the two existing spine events — the wizard only **builds the message, picks suppress flags, and threads the mention**. No new event registration.

### Site-wide audience → `site_announcement`

Mirrors `_send_site_announcement` (`hub/views.py:4006`):

```python
emit(
    "site_announcement",
    actor=self.author,
    context={"member_name": "there", "announcement_title": self.title,
             "announcement_body": body_text, "site_url": site_url,
             "discord_broadcast_webhook": chosen_webhook},   # NEW: routes the site echo to #general/#leadership
    url=site_url,
    period=f"announce:{self.pk}:{now:%Y%m%d%H%M%S%f}",
    messages={Channel.EMAIL: self.build_email_message(site_url)},
    suppress_broadcast=(chosen_webhook == ""),   # "Don't post" → no Discord
    suppress_email=not self.send_email,
    discord_mention=mention_str,                  # NEW
)
```

### Guild audience → materialize a `GuildAnnouncement`, then `notify_members(discord_mention=…)`

The guild path does **not** re-implement the guild emit — it **creates the published `GuildAnnouncement`** (§5 step 4) and calls its `notify_members(discord_mention=mention_str, email_message=self.build_email_message(guild_url))`, so the durable post lands on the guild page/list/slideshow **and** the notification fans out through the one tested path. `notify_members` already sets `context["guild"]` + `context["discord_broadcast_webhook"]`, `suppress_guild_broadcast=(webhook == "")`, `suppress_email=not send_email`, and the per-announcement `period` (`membership/models.py:1945`). The two changes to it (both default-off, invisible to every existing caller): `discord_mention` threaded into `emit()`, and `email_message` passed as `messages={Channel.EMAIL: …}` so the wizard's branded email overrides the default copy-mode guild email — matching the Step-2 preview.

### The mention plumbing (three seams)

1. **`Message` (`core/events/channels.py:57`)** — add `discord_mention: str = ""` to the frozen dataclass. Every non-Discord adapter ignores it (they never read it), so existing callers are byte-unaffected.
2. **`emit()` (`core/events/emit.py:43`)** — add `discord_mention: str = ""`. Inside `message_for`, when `channel is Channel.DISCORD and discord_mention`, return `dataclasses.replace(base, discord_mention=discord_mention)`. Because **both** the central broadcast (`emit.py:231`) and the guild/chosen-webhook post (`_guild_broadcast`, `emit.py:279`) go through the single `message_for(channel)` funnel, one stamp covers both paths.
3. **`build_embed_payload()` (`core/events/discord.py:128`)** — when `message.discord_mention`:
   ```python
   payload["content"] = message.discord_mention              # literal "@here" / "@everyone"
   payload["allowed_mentions"] = {"parse": ["everyone"]}     # the single flag gating BOTH @everyone and @here
   ```
   When blank → payload is byte-identical to today (no `content`, no `allowed_mentions`). The standalone GH release script (`.github/scripts/discord_release_notify.py`) builds its own payload and is unaffected.

### Guild-less chosen-webhook broadcast (the site-wide fix)

The `discord_broadcast_webhook` override makes the central post `continue` (`emit.py:227-229`), then the actual post happens in `_guild_broadcast`, which **early-returns without a guild** (`emit.py:268-270`). For a site-wide announcement (no guild) the chosen #general/#leadership webhook would therefore never post. Generalize: when `ctx["discord_broadcast_webhook"]` is present, post to it regardless of guild presence, keying the ledger `target_ref` to `broadcast:guild:<id>` when a guild exists, else a stable `broadcast:chosen`. A blank chosen webhook (NONE) still posts nothing. This keeps every existing guild caller byte-identical and lights up the site-wide channel choice.

### Copy correctness

The EMAIL channel is always a pre-rendered branded override (`build_email_message`), so it's cream-on-dark with gold links via `wrap_email_html` — not the bare copy fragment. The in-app bell + Discord embed still render from the seeded `site_announcement` / `guild_announcement` copy (unchanged). Subject/body single timezone (Portland) via `_absolute_url` + local `now`. `.txt` (text part) and `.html` stay in sync inside `build_email_message`. Every send goes through `emit()` with a unique `period` so it actually delivers and dedupes (MEMORY `reference_emit_period_required`). Broadcasts reach only **activated** members (the resolvers are gated; MEMORY `reference_notification_activation_gate`) — the recipient count on the confirm reflects exactly that.

`SiteActivity`: `site_announcement` / `guild_announcement` already declare their activity kinds; `emit()` logs them. No new kinds.

---

## 8. Build order (phased; each phase ships green)

1. **Model + drafts (no send yet).** `AnnouncementDraft` + manager + `resolve_channel_webhook` + migration. `AnnouncementComposeForm` (audience-aware choices). `hub_compose` GET/render, `hub_compose_save_draft`, `hub_compose_delete_draft`, the drafts list partial. Wizard page renders all three steps; Save draft / Resume / Delete work end-to-end. Preview + test + send stubbed or hidden. *Green: model specs + save/resume/delete view specs.*
2. **Wizard send + email preview (reusing today's spine calls).** `build_email_message`, `AnnouncementDraft.send()` (site = emit; **guild = materialize a published `GuildAnnouncement` + `notify_members(email_message=…)`** — add the `email_message` kwarg to `notify_members` now), `recipient_count()`, `hub_compose_count`, `hub_compose_preview` (HTMX iframe), `hub_compose_test` (direct send), `hub_compose_send` (+ the native-confirm submit). At this point Discord still routes exactly as the two old composers do (global webhook for site, picker for guild) — no mentions, no site-wide channel choice yet. Retire `_send_site_announcement` / `_handle_announcement_action` + the guild-edit inline *create* form; repoint their entry points at the wizard (keep `GuildAnnouncementForm` for the edit modal). *Green: send transitions (guild send creates a published `GuildAnnouncement` visible on the guild page), suppress flags, count, preview/test view specs.*
3. **@mention + site-wide channel-choice plumbing (core spine).** `Message.discord_mention`; `emit()` `discord_mention` param + `message_for` stamp; `build_embed_payload` `content`/`allowed_mentions`; the guild-less chosen-webhook broadcast generalization; the `discord_mention` kwarg on `notify_members` (threaded to `emit()`); the Step-3 mention radios + audience-scoped picker choices wired in. *Green: `discord_mention_spec` (payload with/without mention; `@here` vs `@everyone`; blank = byte-identical), site-wide chosen-webhook post spec, guild-send-with-mention spec.*
4. **Housekeeping.** Both-theme + mobile pass on the stepper/preview/pickers; empty/loading/error states; delete the retired templates/CSS; docs. **Final step: bump `plfog/version.py` VERSION + one grouped, member-friendly CHANGELOG entry** ("Compose announcements in one place — pick who it's for, design the email with a live preview, choose the Discord channel, and save a draft to finish later.") — new net-new feature ⇒ new top entry (note only; do not bump during spec).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected; MEMORY `reference_pytest_describe_context_not_collected`), factory-boy, ≥98% gate, run in the `plfog-web` image (`--no-cov` for subsets).

- **`membership/spec/models/announcement_draft_spec.py`**
  - `for_user` returns only the author's **unsent** drafts, newest first; excludes others' and sent ones.
  - `save_from_form` sanitizes body; a guild audience without a guild raises; the DB check constraint rejects it too.
  - `send()`: emits the right event per audience; sets `sent_at`; second `send()` raises `AlreadySentError`; empty body raises `ValidationError`; a guild send with no guild raises; `suppress_email` follows `send_email`; `suppress_broadcast`/`suppress_guild_broadcast` follow the "Don't post" choice; unique `period`.
  - **`send()` guild materialization**: a guild send **creates a published `GuildAnnouncement`** (appears in `guild.announcements.published()`, i.e. it would render on the guild page/edit list/slideshow); its `body` is the **plain-text** flattening of the draft's rich HTML (no tags); `expires_at`, `send_email`, `discord_channel` carry over; the branded `email_message` reaches `emit` as the EMAIL override; a site send creates **no** `GuildAnnouncement`.
  - `recipient_count()`: site = all-active audience; guild = the guild's joined-member audience.
  - `resolve_channel_webhook`: each channel → its webhook; NONE → ""; unknown → `ValueError`.
- **`core/spec/events/discord_mention_spec.py`** (respx-mock the webhook POST)
  - `build_embed_payload` with `discord_mention=""` → no `content`/`allowed_mentions` (byte-identical to today).
  - `@here` / `@everyone` → `content` set + `allowed_mentions={"parse":["everyone"]}`.
  - `emit(..., discord_mention="@everyone")` posts a payload carrying the mention on **both** the central (site) and the chosen-webhook (guild) paths.
  - Site-wide `discord_broadcast_webhook` with **no guild** actually posts (regression for the `_guild_broadcast` early-return).
- **`hub/spec/views/announcement_compose_spec.py`**
  - GET renders all three steps + the drafts list; resume loads a draft's values; resuming **another member's** (or an already-sent) draft pk **404s** (never loads foreign content).
  - Permission gating: a non-admin can't pick site-wide (server-side 403 on send); a non-editor can't send to a guild; an editor can send to their guild; the site-wide `<option>` is absent from the audience select for a non-admin.
  - Save-draft upserts (no duplicate on a second save with the same `draft_pk`); a **valid** form returns a toast + OOB; an **invalid** form (blank title) returns an **error** toast and creates/updates **no** row.
  - `hub_compose_count` returns a 204 with an `HX-Trigger` `compose-count` payload carrying the correct N for the posted audience (site all-active vs a guild's members), and re-checks the caller may address that audience.
  - Test-send hits `core.email.send` once, not the spine (no `EventDelivery` row).
  - Delete goes through confirm → removes the row → toast.
  - **Template/state**: since the test client can't catch nested-form orphaning, assert the rendered page has exactly one `<form>` wrapping the steps and that **no wizard control except Send is `type="submit"`** (HTML-parse assertions; MEMORY `reference_nested_form_save_bug`); assert the empty-drafts copy; assert the "+ New announcement" link and each row's Resume/Delete; assert the "Reaches N" line reflects the recipient count.

Gotchas: activation gate means send-count specs must seed **activated** members (`last_login` set) or expect zero (MEMORY `reference_notification_activation_gate`); member-gated view specs need a `MembershipPlan` seeded before login (MEMORY `reference_e2e_needs_membershipplan`); ruff-format the new migration and `git add` it with the code (MEMORY `reference_migrations_need_ruff_format`).

## 10. Open / deferred

- **Per-channel independent copy** — out of scope. As today, the plain-text (bell/Discord) and email bodies both derive from the one rich body (`rich_html_to_text` for text). One message, three renderings.
- **Arbitrary audience segments** — out of scope. Site-wide vs one guild only; no "members of guilds X and Y," no role filters.
- **Scheduled send-time (`publish_at`)** — deferred to sibling **Feature C**. `AnnouncementDraft` is the natural home for a future `scheduled_for` field + a cron that calls `send()`; the mark-sent transition and unique `period` already make a scheduled send safe. Note it as the follow-up rather than building it here.
- **Member-proposal flow stays separate** — `2026-07-03-member-submitted-guild-announcements.md`'s propose→review path is for members who *can't* post directly; this wizard is only for people who can (admins + guild editors). Not merged.
- **Release-mode composer stays separate** — `2026-07-04-release-email-redesign.md`'s sectioned card composer is a distinct surface; only the *plain* site composer folds in.
- **Per-guild Discord routing (`DiscordWebhookRoute`)** — the wizard uses today's channel→webhook resolution; if the Discord-routing consolidation ships, `resolve_channel_webhook` is where it plugs in. No dependency either way.

### Open questions for Josh

1. **@everyone/@here scope in the *shared* channels.** The locked decision is "opt-in for anyone who can post, off by default." That's clean for a guild pinging *its own* channel. But a guild editor can also pick `#general-chat`/`#leadership` for their guild announcement — so as written, any guild lead could fire an `@everyone` at the whole server from a single-guild post. Confirm the locked decision is meant to extend to the shared channels too, or restrict `@everyone`/`@here` on `#general`/`#leadership` to fog admins (guild-own-channel pings stay open to editors). Cheap to gate in `AnnouncementComposeForm.clean` if you want the tighter rule.
2. **Guild-announcement formatting stays plain on-page.** Unifying the composer onto the Quill editor means rich formatting now shows only in the *email* — the guild page + slideshow keep rendering the plain-text body (`|linebreaksbr`), same as today's plain guild posts. If you'd rather guild posts render rich on the page too, that's a separate follow-up (a rich render + sanitize on `guild_detail.html` / the slideshow), deliberately **not** bundled here so no existing display template changes.
3. **No "sent announcements" history surface.** Sent drafts stay as immutable `sent_at` rows (audit) but there's no UI listing *sent* site-wide announcements (guild ones are visible as `GuildAnnouncement`s on the guild page). Matches today (the old site composer kept no history). Add a "Sent" tab later only if members ask.
```
