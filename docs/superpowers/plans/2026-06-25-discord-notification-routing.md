# Discord Notification Routing — consolidate, class-published, guild dual-routing, URL fixes — Spec & Implementation Plan

**Status:** 📋 IMPLEMENTATION PLAN — **NOT YET IMPLEMENTED.** This is a planning document only; none of it is built. Do not treat any model, field, view, or behavior described here as existing in the codebase.
**Date:** 2026-06-25
**Surface:** FOG hub (`pastlives.test:8000`) — guild edit page (`templates/hub/guild_edit.html`, Meetings tab). Everything else is back-end event-spine plumbing (`core/events/*`) + ops config. No book-CMS or admin UI changes.
**Related:** Sits on the Phase-6 notification spine (`core/events/registry.py` `_NEW_EVENTS`, design §4) and the Discord broadcast channel (Decision 9, `core/events/discord.py`). Guild settings live next to `2026-06-24-guild-meeting-notes.md`. The deferred guild meetings / community-events work is "Spec B."

---

## 1. Summary

Right now the app's Discord posts are split across two channels and several events post broken (relative) links, while a guild's own Discord channel is invisible to its lead. This change does four things for the people who use it:

- **Members** get one tidy feed: every app post (releases, announcements, voting reminders, and now **new published classes**) lands in a single channel, **#fog-app-announcements**, with links that actually work.
- **Guild leads** can paste their guild's **own Discord webhook** on the guild settings page and flip a default-on "also post to our Discord" switch, so a guild announcement shows up **both** in the central channel **and** in the guild's own channel.
- A **newly published class** now broadcasts to Discord (it only rang the in-app bell before).
- Two long-standing **broken-link bugs** (guild announcements and the 48-hour voting reminder) are fixed to emit absolute URLs.

The channel consolidation itself is an **ops config change** (repoint two webhook values), not code — documented below so it isn't lost.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| One channel for all app posts | Everything posts to **#fog-app-announcements**. Consolidation is **config, not code**: ops repoints the app webhook env var `DISCORD_NOTIFY_WEBHOOK_URL` (settings.py:259) **and** the GitHub Actions release secret `DISCORD_WEBHOOK_URL` (`.github/workflows/discord-notify.yml:21`) at the announcements channel. **#fog-app-notifications is being deleted.** No code change. |
| `class_published` becomes a Discord broadcast | It is **site-wide** → posts to the **central/global** webhook only (no guild webhook). Add it to `_NEW_EVENTS` with `_DISCORD_ON` (keeping in-app ON, email OFF, push OFF). |
| Per-guild Discord webhook is **visible & lead-editable** | Unlike the admin per-event route (write-only secret), the guild's webhook field is a plain visible `URLField` on the guild settings page, paired with a **default-ON** toggle "also post to our Discord." |
| Guild-scoped events dual-route | For guild-scoped events (today: `guild_announcement`; later Spec B's meetings/events), the Discord broadcast posts to the **central channel ALWAYS**, AND **additionally** to the guild's own webhook when the toggle is on and a webhook is set. The two posts are **deduped independently** (distinct `EventDelivery` target refs). |
| Best-effort guild post | A guild-webhook failure must **never** block or fail the central post (mirrors the existing "blank webhook = no-op", logged-not-raised idiom). |
| Relative-URL bug | `guild_announcement` and `voting.closing_48h` must emit **absolute** URLs (MEMBER_BASE_URL-based — both are member-hub links). |

---

## 2. What already exists (reuse, don't reinvent)

All confirmed in the code at the cited lines — the build is assembly plus one genuinely new mechanism (§2 gap 1).

| Need | Existing thing | Location |
|---|---|---|
| Discord channel-spec shorthand to add to the event | `_DISCORD_ON = ChannelSpec(Channel.DISCORD, ChannelDefault.ON)` | `core/events/registry.py:307` |
| Other channel-spec shorthands (preserve class_published's in-app/email/push) | `_IN_APP_ON` (143), `_EMAIL_OFF` (145), `_PUSH_OFF` (147) | `core/events/registry.py:143-147` |
| Where Phase-6 events REPLACE seeded ones (key-match → replace) | `_NEW_EVENTS` list + `_assemble_events()` replace-by-key | `core/events/registry.py:319`, `:415-430` |
| `class_published` currently seeded (in_app ON, email OFF, push OFF, **no Discord**) | recipient seed `ALL_ACTIVE_MEMBERS`; channels built by `_channels_from_trigger` from the `class_published` trigger | `core/events/registry.py:176`; trigger at `core/triggers.py:36`; builder at `core/events/registry.py:150-166` |
| Broadcast fan-out (one post per event, not per recipient) | `_broadcast_fan_out(event, message_for, period, …)` | `core/events/emit.py:160-190` |
| Broadcast idempotency ledger (dedup on `EventDelivery`, `target_ref="broadcast"`) | `_record_broadcast(event_key, channel, period)` | `core/events/emit.py:286-297` |
| The single broadcast adapter | `DiscordAdapter.broadcast(message)` → `webhook_for_event` + `post_embed` | `core/events/channels.py:284-314` |
| Webhook resolution (DB route → in-code override → global) | `webhook_for_event(event_key)`, `global_webhook()` | `core/events/discord.py:49-85` |
| Embed build + best-effort POST (blank = no-op, never raises) | `build_embed_payload(message)` (103), `post_embed(url, message)` (126) | `core/events/discord.py:103-154` |
| Per-event DB routing model (admin-editable) | `DiscordWebhookRoute` (`event_key`, `webhook_url`, `is_enabled`, `effective_webhook`, `overrides_global`) | `core/models.py:1060-1124` |
| Admin route editor — **the write-only contrast to call out** | `edit_discord_route` view + `DiscordRouteForm` + `edit_discord.html`; the webhook field is **write-only / blank-on-load / never echoed** | `hub/notification_views.py:278-303`; `hub/notification_forms.py:76-112`; `templates/hub/admin/notifications/edit_discord.html:19-31` |
| Guild model (home for the two new fields) + the existing **display-only** `discord_url` (a public link button — NOT a webhook) | `Guild`; `discord_url` URLField | `membership/models.py:819-821` |
| Guild edit form (add the two fields here) | `GuildEditForm` (Meta.fields 62-81, labels 99-114) | `hub/forms.py:43-114` |
| Guild edit template + the Meetings tab + the shared "Save Changes" button that already covers it | tabs (7-18), Meetings panel (43-66), Calendar Integration card to insert after (59-65), Save button (89-90) | `templates/hub/guild_edit.html` |
| The shared **Save** button shows on the Meetings tab (its `x-show` excludes only announcements/staff/content) → **no new view/endpoint needed** | `<button>Save Changes</button>` under `x-show="section !== 'announcements' && section !== 'staff' && section !== 'content'"` | `templates/hub/guild_edit.html:89-90` |
| `guild_announcement` emit + context already carries `guild` (the dual-route handle) | `GuildAnnouncement.notify_members()` — `emit("guild_announcement", context={"guild": self.guild, …, "guild_url": "/guilds/<id>/"}, url="/guilds/<id>/")` — **both URLs relative (the bug)** | `membership/models.py:1098-1125` (relative URLs at 1121, 1123) |
| `class_published` emit (fix the relative URL here) | `emit("class_published", title=…, body=…, url=f"/classes/{self.slug}/", period=f"offering:{self.pk}:published")` — url RELATIVE | `classes/models.py:613-621` |
| Absolute-URL helper for **book** URLs (class pages) | `_absolute_url(path)` (BOOK_BASE_URL) + `reverse("classes:public_class_detail", kwargs={"slug": …})` precedent | `classes/emails.py:59-61`, `:448` |
| Absolute-URL helper for **member-hub** URLs (guild/voting pages) | `_absolute_url(path)` (MEMBER_BASE_URL) | `membership/orientations.py:32-34` |
| The voting reminder emit (fix the relative URL here) | `closing_48h_occurrences()` — `context={"voting_url": "/guilds/voting/"}, url="/guilds/voting/"` — both RELATIVE | `membership/voting.py:74-101` (URLs at 97, 100) |
| Curated copy catalogue + Discord-falls-back-to-EMAIL rule | `_CURATED` dict; `EventCopy.copy_for(DISCORD)` returns the EMAIL copy | `core/events/copy.py:117`, `:72-75`; `guild_announcement` exemplar at `:292-316` |
| Settings the consolidation repoints | `DISCORD_NOTIFY_WEBHOOK_URL` (259), `MEMBER_BASE_URL` (64), `BOOK_BASE_URL` (402) | `plfog/settings.py` |
| Memory invariant — every `emit()` needs a UNIQUE `period` | class_published already uses `offering:<pk>:published`; guild_announcement uses `announcement:<pk>`; voting uses the cycle period — all preserved | (as cited above) |
| Form / component library | `form_field.html` (auto-renders the boolean as a `.pl-toggle`), `toggle.html` | `templates/components/` |

### Genuine gaps to close (kept small)

1. **No multi-webhook broadcast.** `_broadcast_fan_out` / `DiscordAdapter.broadcast` post to exactly **one** webhook (`webhook_for_event`). The dual-route (central + guild) is the one real new mechanism — designed in §5.1.
2. **No guild webhook storage.** `Guild` has a display-only `discord_url` (a public channel *link*), but no *webhook* field and no post toggle. Two additive fields in §4.
3. **`class_published` has no Discord channel and no curated copy.** Add `_DISCORD_ON` via `_NEW_EVENTS` and a `_CURATED["class_published"]` entry (§5.3).
4. **Relative URLs** in three emit sites. Fixed in §5.2.

---

## 3. Where the code lives

No new Django app; everything stays inside existing `core` / `membership` / `classes` / `hub` coverage + mypy scope.

```
core/events/discord.py        # + guild_webhook(guild) helper (blank-when-disabled idiom)
core/events/emit.py           # _record_broadcast(+target_ref); _broadcast_fan_out threads ctx → guild post
core/events/registry.py       # + class_published EventType in _NEW_EVENTS (adds _DISCORD_ON)
core/events/copy.py           # + _CURATED["class_published"] (IN_APP + EMAIL copy; Discord falls back to EMAIL)
classes/models.py             # class_published emit → copy-mode + absolute class_url (§5.2/§5.3)
membership/models.py          # + Guild.discord_webhook_url, Guild.discord_post_enabled; GuildAnnouncement.notify_members → absolute guild_url
membership/migrations/00xx_guild_discord_webhook.py   # additive, reversible by default
membership/voting.py          # closing_48h_occurrences → absolute voting_url + url
hub/forms.py                  # GuildEditForm: + 2 fields in Meta.fields, labels, help_texts
templates/hub/guild_edit.html # + "Discord" hub-card on the Meetings tab (after Calendar Integration, line 65)
plfog/version.py              # version bump + member-friendly CHANGELOG (final phase, at build time)
# Tests (BDD *_spec.py, run in plfog-web Docker):
core/events/spec/…            # broadcast dual-route, dedup independence, class_published Discord + absolute URL, copy
membership/spec/…             # guild fields, absolute guild_url; voting absolute url
hub/spec/…                    # guild_edit Discord card renders + saves
```

Ops (no code): repoint `DISCORD_NOTIFY_WEBHOOK_URL` (Render/Hetzner env) and the `DISCORD_WEBHOOK_URL` GitHub secret; delete #fog-app-notifications.

---

## 4. Data model — two additive fields on `Guild` (`membership/models.py`)

Placed beside the existing `discord_url` (`:819`). Both are **visible / lead-editable** (the opposite of the admin `DiscordWebhookRoute.webhook_url`, which is a write-only secret — see §2).

| Field | Type | Notes |
|---|---|---|
| `discord_webhook_url` | `URLField(max_length=500, blank=True, default="")` | `help_text="A Discord webhook for THIS guild's own channel. Guild announcements also post here. Blank = nothing posts to your channel."` Distinct from `discord_url` (a public link button). |
| `discord_post_enabled` | `BooleanField(default=True)` | `help_text="Also post this guild's announcements to your own Discord channel (in addition to the makerspace-wide channel)."` Default ON per the locked decision. |

> **Secret-leak guard (load-bearing — name the labels concretely).** The guild page already has `discord_url`, today labeled **"Discord channel URL"** and rendered as a **public link button** on the guild's public page. The new `discord_webhook_url` is a **secret** — anyone who has it can post to the guild's channel. If a lead pastes their webhook into the public link field, the secret is published publicly. So the two fields MUST carry contrasting labels + hints (set in `GuildEditForm.Meta.labels`/`help_texts`):
> - **`discord_url`** → label **"Discord channel link (shown to members)"**, hint *"The public invite/link to your channel, shown as a button on your guild page."*
> - **`discord_webhook_url`** → label **"Announcement webhook (auto-posts here — keep private)"**, hint *"A private Discord webhook for your channel. Don't paste your public invite link here."*
>
> **Webhook shape validation.** A plain `URLField` accepts any URL, and a mis-pasted/invalid webhook fails **silently** (the broadcast is best-effort — logged, never surfaced to the lead). Add a `GuildEditForm.clean_discord_webhook_url` that, when non-blank, validates the value matches the Discord webhook shape (`https://discord.com/api/webhooks/…`) and raises a `ValidationError` otherwise. At minimum, if validation is skipped, the spec must state the silent-failure behavior so a bad webhook isn't read later as a platform bug.

- **Migration:** one additive migration (two columns, both with safe defaults). Reverse is Django's automatic `RemoveField` pair — no `RunPython`. `ruff format` the generated file and `git add` it in the same commit (migrations-need-ruff-format note).
- No new manager/queryset (the fan-out reads the fields off the in-context `Guild` instance directly). No index/constraint (these are not query keys).
- No `__str__` change.

---

## 5. Business logic (fat models / spine; views stay thin)

### 5.1 The new mechanism — multi-webhook broadcast fan-out

**Goal:** for a guild-scoped event, post the embed to the **central** webhook (unchanged) **and** to the **guild's own** webhook, deduped independently, best-effort.

**How the guild reaches the adapter — decision: via the event context.** Three options were considered:

- **Thread `guild` through `Message`** — rejected: `Message` is a frozen, render-only payload (`channels.py:56-70`); a `guild` FK on it is a layering smell and touches every adapter.
- **Small adapter signature change** (`broadcast(message, guild=…)`) — rejected as the primary: it forces the dedup ledger (which lives in `emit.py`) either into the adapter or into an awkward return-value contract.
- **Event context (chosen).** `guild_announcement` already passes `context={"guild": self.guild, …}` (`models.py:1116`). The emit spine already holds `ctx` (`emit.py:100`). Thread `ctx` into `_broadcast_fan_out` and let it do the second post + its own dedup, exactly where the central dedup already lives. Minimal, and keeps the `EventDelivery` ledger ownership in `emit.py`.

**Concrete shape:**

1. **`core/events/discord.py` — add `guild_webhook(guild) -> str`** mirroring `global_webhook()`'s blank-is-disabled idiom:
   - returns `""` unless `guild.discord_post_enabled` **and** `guild.discord_webhook_url.strip()` is non-blank; else the stripped URL.
   - keep it defensive/duck-typed (a missing attr → `""`) so a context that carries a non-Guild object never raises into the spine.

2. **`core/events/emit.py` — `_record_broadcast` gains a `target_ref` param** (default `"broadcast"`, preserving today's behavior). The unique key stays `(event_key, target_ref, channel, period)`.

3. **`_broadcast_fan_out` threads `ctx`** (update the call at `emit.py:148` to pass `ctx`). After the existing **central** post for the Discord channel, and **gated to `channel is Channel.DISCORD`** (the only broadcast channel — explicit so the generic loop stays honest):
   - `guild = ctx.get("guild")`; if `guild is not None` and `discord_module.guild_webhook(guild)` is non-blank:
     - claim `_record_broadcast(event.key, channel, period, target_ref=f"broadcast:guild:{guild.pk}")`;
     - if newly claimed, `discord_module.post_embed(discord_module.guild_webhook(guild), message_for(channel))` and record the delivery; if already claimed, record a skipped-duplicate.

**Why this satisfies the locked decisions:**

- **Central always posts** — the existing central branch is untouched; the guild post is purely additive.
- **Independent dedup** — central uses `target_ref="broadcast"`, guild uses `target_ref="broadcast:guild:<id>"`; the `EventDelivery` unique constraint makes them separate rows, so both post and a re-emit dedups each on its own (a scheduler re-run never double-posts either).
- **Best-effort** — the guild post runs *after* the central post and uses `post_embed`, which returns `False` / logs / never raises on a bad or blank webhook; a guild failure cannot block central. (Note: claim-then-post means a transient guild failure won't auto-retry within the same `period` — identical to the central post's existing semantics.)
- **`class_published` posts centrally only** — its emit carries **no** `guild` in context, so `ctx.get("guild")` is `None` → no guild branch.
- **Empty guild webhook = safe no-op** — `guild_webhook` returns `""` (toggle off OR blank URL) → the guild branch is skipped entirely.

**Build-note guards (call these out so an implementer doesn't reintroduce a bug):**

- **The guild claim is a SIBLING of the central claim, not nested inside it.** The guild post must run its own `_record_broadcast(…, target_ref="broadcast:guild:<id>")` claim independently of whether the central `_record_broadcast("broadcast")` returned newly-claimed. If the guild branch is nested inside the central success branch, a central-duplicate re-emit (e.g. a webhook added *after* the first emit, then a scheduler re-run) would silently skip the guild post forever.
- **Compute the guild webhook once.** `guild_webhook(guild)` is referenced twice (the non-blank check + the `post_embed` argument) — bind it to a local and reuse it.
- **No backfill (expected, not a bug).** A guild webhook added *after* an announcement already posted will **not** retroactively post — the announcement is a single emit and its `period`/`target_ref` slots are already claimed (the central one at least). This is the intended idempotency behavior; state it so it isn't later filed as a missing-post bug.

### 5.2 Absolute-URL fixes (model/spine layer)

Reuse the existing per-surface helpers; do **not** introduce a new URL-building convention.

- **`class_published` (`classes/models.py:613-621`)** — book URL. Build `class_url = _absolute_url(reverse("classes:public_class_detail", kwargs={"slug": self.slug}))` via `classes/emails._absolute_url` (BOOK_BASE_URL). Pass it as both the `url=` kwarg (embed/in-app click target) and the `class_url` context placeholder (§5.3). Preserve `period="offering:<pk>:published"`.
- **`guild_announcement` (`membership/models.py:1111-1125`)** — member-hub URL. Build `guild_url = _absolute_url(reverse("hub_guild_detail", args=[self.guild_id]))` via `membership/orientations._absolute_url` (MEMBER_BASE_URL) and use it for both `context["guild_url"]` and `url=`. Preserve `period="announcement:<pk>"` and the existing `context["guild"]` (the dual-route handle).
- **`voting.closing_48h` (`membership/voting.py:97,100`)** — member-hub URL. Build `voting_url = _absolute_url(reverse("hub_guild_voting"))` (MEMBER_BASE_URL) for both `context["voting_url"]` and the occurrence `url=`. Preserve the cycle `period`.

> The two member-hub fixes both want `membership/orientations._absolute_url`. Importing a `_`-prefixed helper across two modules in the same app is acceptable (it already lives in `membership`); if it grates during build, promote it to a tiny shared `membership` helper in the same PR — but that's a rename, not a new pattern. Do **not** add a second BOOK/MEMBER base-URL scheme.

### 5.3 `class_published` → Discord broadcast + curated copy

- **Registry (`core/events/registry.py`)** — add a `class_published` `EventType` to `_NEW_EVENTS` (the replace-by-key path at `:415-430` swaps the seeded one). Channels `(_IN_APP_ON, _EMAIL_OFF, _PUSH_OFF, _DISCORD_ON)` — preserve in-app ON / email OFF / push OFF, **add** Discord ON. Recipient stays `Recipients.ALL_ACTIVE_MEMBERS`; `activity_kind` stays `None` (the `CmsActivity` mirror is the activity source — see the existing note at `registry.py:220,231`). Add the matching `CLASS_PUBLISHED = "class_published"` key constant alongside the others (`:310-316`).
- **Emit → copy mode (`classes/models.py`)** — to make the curated copy actually render (copy mode triggers only when no explicit `title`/`body` is passed — `emit.py:117`), drop the explicit `title="New class published"` / `body=self.title` and instead pass `context={"class_title": self.title, "class_url": <absolute>}` plus the absolute `url=`. The in-app bell row then renders from the IN_APP copy (now DB-editable — a small improvement over the hard-coded string), and the Discord embed renders from the EMAIL copy via `copy_for(DISCORD)`.
  - *Alternative (minimum change, noted not chosen):* keep the explicit strings and only make `url` absolute; the curated entry would then be dead. Rejected because the locked decision asks for curated copy and Discord-via-EMAIL fallback.
- **Curated copy (`core/events/copy.py`)** — add `_CURATED["class_published"]` mirroring the `registration_confirmed` shape (`:118-149`): `placeholders=("class_title", "class_url")`, a `sample_context`, an **IN_APP** `ChannelCopy` (e.g. subject `"New class: {{ class_title }}"`, body `"{{ class_title }} just went live."`) and an **EMAIL** `ChannelCopy` (concise, with `{{ class_url }}`) that doubles as the Discord embed source. Keep placeholders and `sample_context` in lock-step (the seed command + a test assert every placeholder appears in both — see the note at `copy.py:113-117`).

---

## 6. UI / UX — guild-settings "Discord" card (completeness checklist applied)

**Exactly one new screen surface.** The rest of this feature is back-end. Applying the rubric concretely:

### Screen — Guild edit page, **Meetings tab**, new "Discord" card

- **Template:** `templates/hub/guild_edit.html` — a **new `<div class="hub-card">` titled "Discord"** inserted **right after the Calendar Integration card** (after line 65), still inside `x-show="section === 'meetings'"`. Copy the sibling card's exact inline scaffolding so the "no new CSS" claim holds: `<div class="hub-card" style="padding:1.5rem; margin-bottom:1.5rem;">` wrapping an `<h2 class="hub-detail-label" style="margin-top:0; margin-bottom:1rem;">Discord</h2>`, matching the Calendar Integration card directly above it.
- **Layout & container:** inline, within the existing page `<form method="post" enctype="multipart/form-data" class="hub-form">` (`:20`). This is the right container per the FRONTEND interaction table (these are 2 fields on an existing multi-field settings page, not a quick modal action).
- **Components used:** `components/form_field.html` for **both** fields — the `URLField` renders as a text input inside `.pl-form-group`; the `BooleanField` **auto-renders as a `.pl-toggle`** (never a raw checkbox). No custom HTML.
- **The controls, named explicitly:**
  - **Webhook URL field** — `{% include "components/form_field.html" with field=form.discord_webhook_url %}`, full-width (not in a 2-col grid — see layout below), with a `forms.URLInput` placeholder `https://discord.com/api/webhooks/...` (set in `GuildEditForm.Meta.widgets`). Its label + hint are the **contrasting** copy pinned in §4 ("Announcement webhook (auto-posts here — keep private)") so a lead never confuses it with the public "Discord channel link" field on the Basic Information tab — different things, same word. The secret-leak guard in §4 is the reason this copy is non-negotiable.
  - **"Also post to our Discord" toggle** — `{% include "components/form_field.html" with field=form.discord_post_enabled %}` → `.pl-toggle`, **default ON**, rendered as a **standalone full-width row beneath** the webhook field (mirroring how the adjacent Meetings card places its `meeting_is_tba` boolean below its grid at `guild_edit.html:55`).
  - **Save** — the page's existing **"Save Changes"** primary button (`:89-90`). It is **already visible on the Meetings tab** because its `x-show` excludes only `announcements`/`staff`/`content`. A full-page POST to the existing guild-edit view persists these alongside every other guild field; feedback is the page's existing **Django success message** on redirect (full-page form → messages, not a toast, per the interaction table). **No new view, URL, or endpoint.**
- **States:**
  - **Empty (no webhook set):** the field is blank and the toggle may be ON — this is the documented **safe no-op**: `guild_webhook()` returns `""` so nothing posts to the guild channel; the central post is unaffected. The hint says as much ("Blank = nothing posts to your channel").
  - **Toggle off + webhook set:** also a no-op for the guild channel (central still posts). Covered by `guild_webhook()` returning `""`.
  - **Error:** an invalid URL → Django's `URLField` validation re-renders the field with its inline error via `form_field.html` (the page stays put, no 500). No token-expiry or async failure paths on this screen.
  - **Success:** redirect + the existing guild-edit success message. **No-surprise note:** the guild-edit view redirects to the guild **detail** page (not back to the Meetings tab), so after saving, the lead lands on their public guild page and won't see the Discord settings on screen — this is the existing behavior for *every* Meetings-tab field, not introduced here; the success message confirms the save.
  - **Loading:** none — server-rendered, full-page POST (no HTMX on this card).
- **Dark + light:** theme tokens only. The `URLField` inherits input tokens because it renders inside `form_field.html`'s `.pl-form-group` wrapper (no inline `background`/`color`, no `var(--surface,#fff)` trap — FRONTEND rule 13). The toggle uses the shared `.pl-toggle` styles. **No new CSS** is needed (the card reuses `hub-card` + `pl-form-grid` like the Calendar Integration card directly above it). **Verify both themes** on `pastlives.test:8000` (login-gated — verify by reading the rendered template + served CSS).
- **Mobile:** the webhook field is full-width and the toggle a full-width row beneath it (no 2-col grid), so the card is already single-column and reflows cleanly on narrow widths; the toggle is a full tap target; spacing on the 8px grid, matching the adjacent cards.
- **Explicit N/A (rubric items that don't apply here):** there is **no list/formset** on this card, so the **"+ Add" button**, **per-row Delete**, and **empty-list** rubric items are **N/A**. There is **no destructive action**, so **`confirm_modal.html`** is **N/A**. Stated here so the UX reviewer doesn't flag a missing Add/Delete that the feature genuinely doesn't have.

---

## 7. Notifications / events / activity

This *is* a notification-routing change; summary of the event-spine deltas (no new `SiteActivity` kinds, no new email templates):

- `class_published`: now `(_IN_APP_ON, _EMAIL_OFF, _PUSH_OFF, _DISCORD_ON)`, copy-driven, absolute `class_url`. Posts to the **central** webhook only (site-wide). `activity_kind` stays `None` (CmsActivity mirror remains the activity source).
- `guild_announcement`: unchanged channels; now **dual-routes** (central + guild webhook when enabled/set) and emits an **absolute** `guild_url`/`url`.
- `voting.closing_48h`: unchanged channels; now emits **absolute** `voting_url`/`url`.
- Consolidation (#fog-app-announcements) is **ops config** — no event-registry change. The GitHub-Actions release post (`release.published` mirror) is repointed via its `DISCORD_WEBHOOK_URL` secret, also config.

---

## 8. Build order (phased; each phase ships green)

Each phase lands green (full suite + `ruff format` + `ruff check` + `mypy`), run in the `plfog-web` Docker image. Smallest, shippable first.

1. **URL fixes + `class_published` Discord + copy** *(smallest, immediately shippable)*
   - Registry: add `class_published` to `_NEW_EVENTS` with `_DISCORD_ON`; add the key constant.
   - `core/events/copy.py`: `_CURATED["class_published"]`.
   - `classes/models.py`: emit → copy-mode + absolute `class_url`.
   - `membership/models.py` + `membership/voting.py`: absolute `guild_url` / `voting_url` / `url`.
   - Specs: class_published posts to Discord with an **absolute** URL; copy placeholders in lock-step; both URL-fix sites emit absolute strings.
2. **Guild fields + settings UI**
   - `membership/models.py`: `discord_webhook_url`, `discord_post_enabled` + migration (formatted, committed together).
   - `hub/forms.py`: add both to `GuildEditForm` (`Meta.fields`, `widgets` placeholder, `labels`/help text).
   - `templates/hub/guild_edit.html`: the "Discord" card on the Meetings tab.
   - Specs: form saves both fields; the card renders both controls; the toggle defaults ON.
3. **Multi-webhook fan-out** *(the new mechanism)*
   - `core/events/discord.py`: `guild_webhook(guild)`.
   - `core/events/emit.py`: `_record_broadcast(target_ref=…)` + `_broadcast_fan_out` threads `ctx` and posts the guild branch.
   - Specs: guild announcement fans out to **central + guild** when enabled & set; **central-only** when toggle off / webhook blank; the two posts dedup **independently**; `class_published` (no guild) posts **central-only**; a guild-webhook failure does not block central.
4. **Housekeeping (at build time, in the final PR — not in this spec):** bump `plfog/version.py` `VERSION` (patch on `release-0.19.x`) + a plain-language member-facing `CHANGELOG` entry, e.g. *"All our Discord updates now land in one channel, new published classes get announced there, and guild leads can connect their guild's own Discord so announcements post to both places. We also fixed some links that pointed to the wrong place."*

> Spec only — do not build until approved.

## 9. Testing (BDD `*_spec.py`, `describe_*`/`it_*`, ≥98% coverage, run in `plfog-web` Docker)

`describe_*` for every nested block (**`context_*` is NOT collected** — `it_*` inside one silently never runs). `respx` to mock the webhook POSTs; factory-boy for data; assert on `EventDelivery` rows for dedup. Use `--no-cov` for subset runs.

**Regression tests that matter (call-outs from the prompt):**

- **`class_published` → Discord with an absolute URL.** Publishing an offering posts one central embed whose `url` starts with `BOOK_BASE_URL` (not `/classes/…`); the in-app row + Discord embed render from the curated copy; **no** guild post (no guild in context). Email channel does **not** fire (OFF).
- **Guild announcement dual-route.** With `discord_post_enabled=True` and a non-blank `discord_webhook_url`: the embed POSTs to **both** the central webhook and the guild webhook (two `respx` routes hit). With the toggle **off**, or the webhook **blank**: **central only**. (And the central post is unaffected either way.)
- **Independent dedup.** Re-emitting the same `guild_announcement` (same `period`) posts **neither** again; the central (`target_ref="broadcast"`) and guild (`target_ref="broadcast:guild:<id>"`) slots are distinct `EventDelivery` rows; claiming one does not block the other on first emit.
- **Best-effort guild failure.** A guild webhook that 500s / errors (`post_embed` → `False`, logged, no raise) does **not** prevent the central post and does **not** propagate out of `emit()`.
- **Absolute URLs.** `guild_announcement` emits `guild_url`/`url` starting with `MEMBER_BASE_URL`; `voting.closing_48h` emits `voting_url`/`url` starting with `MEMBER_BASE_URL`.

**Plus:**
- **`guild_webhook()` truth table:** enabled+URL → URL; disabled+URL → `""`; enabled+blank → `""`; non-Guild/duck-typed object → `""` (no raise).
- **Registry:** `get_event("class_published").channels` now includes `Channel.DISCORD` (and still IN_APP, not EMAIL/PUSH); replace-by-key didn't disturb other events.
- **Copy:** every placeholder in `_CURATED["class_published"]` appears in both the copy and the `sample_context` (mirror the existing copy lock-step test).
- **Form/UI:** `GuildEditForm` round-trips both new fields; `guild_edit.html` (Meetings tab) renders the webhook input + the `.pl-toggle`, toggle default ON. Gating unchanged (lead/staff can edit via the existing guild-edit gate).
- **Webhook validation (secret-leak guard):** `clean_discord_webhook_url` accepts a `https://discord.com/api/webhooks/…` URL and a blank value, but rejects a non-Discord URL with a `ValidationError`; the `discord_url` (public link) and `discord_webhook_url` (private webhook) fields carry the distinct labels pinned in §4.
- **No-op safety:** a guild with the toggle on but a blank webhook produces zero guild POSTs and a normal central POST.

**Gotchas:** mock `httpx.post` via `respx` (the spine's outbound HTTP, not `requests`). Use distinct `period` values per scenario so dedup assertions are unambiguous. For the URL assertions, set `MEMBER_BASE_URL` / `BOOK_BASE_URL` to known test values (or assert the `https://…` prefix rather than the full host).

## 10. Open / deferred

- **Channel webhook values** (the actual #fog-app-announcements webhook, each guild's webhook) — **ops config**, not code. Documented in §1/§3; deleting #fog-app-notifications is also ops.
- **Spec B — guild meetings / community events** dual-routing — out of scope here, but §5.1's `ctx["guild"]` mechanism is built to extend to any future guild-scoped event with zero adapter changes (just pass `context={"guild": …}`).
- **`voting.results_published`** absolute-URL audit — only `voting.closing_48h` is in the locked scope; if `results_published` also carries a relative URL it's a clean follow-up, not built here.
- **Google Calendar** integration — unrelated, out of scope.
- **Per-event admin routing UI for guilds** — the admin `DiscordWebhookRoute` editor (write-only secret) stays as-is; the guild webhook is a separate, deliberately visible field. No attempt to unify the two surfaces.
- **Version bump + changelog** — happens **at build time, one entry per PR** (§8 phase 4), not in this spec.
