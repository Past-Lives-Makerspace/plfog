# Guild Announcement — Discord Channel Picker — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-03
**Base:** release-0.20.x (v0.20.1) — release-0.19.x / main are NOT valid bases.
**Surface:** FOG hub `pastlives.test` — the guild **Edit** page › **Announcements/Emails** tab (`templates/hub/guild_edit.html`), plus the admin **Site Settings** page (`templates/hub/admin/site_settings.html`). No book CMS surface.
**Related:**
- `docs/superpowers/plans/2026-06-25-discord-notification-routing.md` (the DB-backed `DiscordWebhookRoute` + per-event routing this composes with).
- The same-day **member-submitted guild announcements** spec — the channel picker described here is the *same control* the lead sees when they **approve** a member submission (see §6.4). Build order below assumes either can land first; they share the radio partial and the persisted `GuildAnnouncement.discord_channel` field that `notify_members()` reads.

> **Why the base matters.** The v0.20.1 guild-announcement work this feature refines — the persisted `send_email`/`post_to_discord` BooleanFields on `GuildAnnouncement`, the no-arg `notify_members()`, the `SiteSettingsForm`, and the announcement post form on the Announcements/Emails tab — exists **only on release-0.20.x**. Every symbol and line anchor below was read from `git show release-0.20.x:<path>`. Do not re-anchor against the release-0.19.x checkout — it does not have this code.

---

## 1. Summary

When a guild lead posts an announcement, they can already choose (shipped in v0.20.1) whether to *also email everyone who joined* and whether to *also post it to Discord*. This feature turns that on/off Discord toggle into a **single choice of where** it posts: **#general-chat**, **#leadership**, or the **guild's own Guild Channel** — with the guild's own channel pre-selected. A lead running (say) the Prison Outreach guild can now push a "meeting moved to Thursday" note to their own members' channel, while a lead with a leadership-only heads-up can send it to **#leadership** instead — without an admin touching webhooks each time. The email and on-site (bell) fan-out is unchanged; only the Discord destination becomes a per-post choice.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Where do #general-chat and #leadership webhooks live? | **Two makerspace-wide webhooks, set once by an admin in Site Settings** — NOT per-guild. |
| What is "Guild Channel"? | The guild's **existing per-guild webhook** (`Guild.discord_webhook_url`). No new per-guild field. |
| Control type on the post form | A **single-choice radio group** (see §6.1 for why radios, not a `pl-toggle` set). |
| Default selection | The guild's own **Guild Channel** (falls back gracefully when the guild has no webhook — §5.3). |
| Relationship to v0.20.1 | **Replaces** the v0.20.1 per-announcement "Also post to Discord" boolean (`GuildAnnouncement.post_to_discord`) with a persisted channel choice. The **separate** "Also send email" toggle (`send_email`) is untouched; email + bell fan-out unchanged. |
| Config home for the two shared webhooks | **Two new `SiteConfiguration` URL fields** on a new Site Settings **Discord** tab (NOT two `DiscordWebhookRoute` rows — see §2, "Why not `DiscordWebhookRoute`"). |
| Can a lead still choose *not* to post to Discord? | Yes — a 4th radio, **"Don't post to Discord,"** preserves the v0.20.1 off-state (old `post_to_discord=False`) so the picker fully subsumes the retired toggle. |

## 2. What already exists (reuse, don't reinvent)

All anchors verified on **release-0.20.x (v0.20.1)**.

| Need | Existing thing | Location |
|---|---|---|
| Announcement model | `GuildAnnouncement` (+ `send_email`, `post_to_discord` **persisted** BooleanFields) | `membership/models.py:1630` (`send_email` `:1656`, `post_to_discord` `:1660`) |
| Notify (reads `self`, **takes NO args**) | `GuildAnnouncement.notify_members(self)` → `emit("guild_announcement", …, period=f"announcement:{self.pk}")` | `membership/models.py:1678` |
| The two opt-out kwargs it already passes | `emit(..., suppress_email=not self.send_email, suppress_guild_broadcast=not self.post_to_discord)` | `membership/models.py:1714`–`:1715` |
| The event (in-app + email + Discord broadcast) | `GUILD_ANNOUNCEMENT = "guild_announcement"`, channels `(_IN_APP_ON, _EMAIL_OFF, _PUSH_OFF, _DISCORD_ON)` | `core/events/registry.py:322`, `:348` |
| `emit()` entry point + its suppress switches | `emit(..., suppress_broadcast, suppress_email, suppress_guild_broadcast)` | `core/events/emit.py:43` (kwargs `:57`–`:59`) |
| Broadcast fan-out (central post + guild dual-route) | `_broadcast_fan_out()` — central loop `for spec in event.channels` `:213`; guild gate `if event.has_channel(Channel.DISCORD) and not suppress_guild_broadcast` `:231` | `core/events/emit.py:186` |
| Per-guild scoped post at emit time | `_guild_broadcast()` reading `ctx["guild"]` → `guild_webhook(guild)` → `post_embed(...)` | `core/events/emit.py:236` (`guild_webhook` call `:259`) |
| Central Discord post → global/route webhook | `DiscordAdapter.broadcast` → `webhook_for_event(message.trigger_kind)` (**this is where the central webhook is resolved — in `channels.py`, NOT `emit.py`**) | `core/events/channels.py:313` (call `:319`); adapter `:295` |
| Event → central webhook resolution | `webhook_for_event()` (DB route → in-code override → global) | `core/events/discord.py:79` |
| Guild-webhook resolver + embed poster | `guild_webhook()`, `post_embed()` | `core/events/discord.py:55`, `:151` |
| Broadcast ledger / dedup (per-target ref) | `_record_broadcast(event_key, channel, period, target_ref=…)`; guild slot `broadcast:guild:<id>` | `core/events/emit.py:363` (guild ref `:262`) |
| Suppress the Discord broadcast entirely | `emit(..., suppress_broadcast=True)` → `_broadcast_fan_out` early-returns | `core/events/emit.py:210` |
| Guild's own webhook + opt-in flag | `Guild.discord_webhook_url`, `Guild.discord_post_enabled` | `membership/models.py:1009`, `:1018` |
| Post form + create view | `GuildAnnouncementForm` (Meta.fields `["title","body","expires_at","send_email","post_to_discord"]`), `guild_announcement_create` (calls no-arg `notify_members()`) | `hub/forms.py:1106` (Meta.fields `:1115`); `hub/views.py:1730` (notify call `:1744`) |
| Authoring UI | Announcements/Emails section of the guild edit page — `x-show="section === 'announcements'"` wrapper; the post `<form>`; the `post_to_discord` toggle include | `templates/hub/guild_edit.html:472` (form `:475`; `send_email` include `:480`; `post_to_discord` include `:481`) |
| Singleton site config + loader | `SiteConfiguration` / `SiteConfiguration.load()`; precedent visible-config field `mailchimp_api_key` | `core/models.py:100` (`load()` `:218`; `mailchimp_api_key` `:168`) |
| Site Settings admin form (model = `SiteConfiguration`) | **`SiteSettingsForm`** (Meta.fields at `:528`) — **NOT** `SiteConfigurationForm` (that name does not exist) | `hub/forms.py:518` |
| Site Settings tabbed template + save | `site_settings.html` (`x-data="{ tab }"` `:91`; `#site-settings-form` `:128`; `submitted_tab` hidden input `:130`; General-tab exclusion filter `:135`; Save wrapper `:366`; `</form>` `:369`) + its save fn `_save_site_settings` | `templates/hub/admin/site_settings.html`; `hub/views.py:3522` (view `admin_site_settings` `:3545`) |
| Admin gate | `@fog_admin_required` (via the existing Site Settings view) | `hub/view_as.py` |

**Gaps to close (kept minimal):**

1. **Two shared-webhook fields** on `SiteConfiguration` + their Site Settings editing surface (on `SiteSettingsForm`).
2. **A persisted channel choice** on `GuildAnnouncement` (`discord_channel`) + a radio control on the post form + validation that the chosen channel is actually configured.
3. **Retire the v0.20.1 `post_to_discord` per-announcement boolean** — the "Don't post to Discord" radio replaces it (remove the field via a reversible migration, drop it from `GuildAnnouncementForm.Meta.fields`, delete the toggle include at `guild_edit.html:481`). **Leave `send_email` alone.**
4. **One emit-time seam:** an explicit `discord_broadcast_webhook` context value so the announcement's single Discord post goes to the *chosen* URL instead of the v0.20.1 global-central + guild-own double-post. A small branch in the `for spec in event.channels` loop and a generalization of `_guild_broadcast` in `core/events/emit.py`; `webhook_for_event` (in `DiscordAdapter.broadcast`) is untouched.

**Why not `DiscordWebhookRoute` for the two shared webhooks?** `DiscordWebhookRoute` is keyed **per `event_key`** (unique) and answers "for event X, override the global webhook." #general-chat and #leadership are **named destinations**, not event routes — modeling them as routes would mean inventing fake event keys and abusing the write-only/blank-keep-URL semantics of `DiscordRouteForm`. Two plain `SiteConfiguration.URLField`s match the locked "configured once, in Site Settings" decision and mirror the existing precedent of a per-guild `discord_webhook_url` URLField and the visible `mailchimp_api_key` config field (`core/models.py:168`).

## 3. Where the code lives

```
core/
  models.py                         # + 2 URLFields on SiteConfiguration (+ migration)
membership/
  models.py                         # + GuildAnnouncement.DiscordChannel TextChoices,
                                    #   + discord_channel CharField (persisted),
                                    #   REMOVE post_to_discord field,
                                    #   resolve_discord_webhook(), notify_members() reads self.discord_channel
  migrations/                       # RemoveField(post_to_discord) + AddField(discord_channel)  — both reversible
core/events/
  emit.py                           # honor ctx["discord_broadcast_webhook"]: skip central DISCORD iter,
                                    #   generalize _guild_broadcast to post to the chosen webhook
  discord.py / channels.py          # UNCHANGED (guild_webhook / webhook_for_event / DiscordAdapter.broadcast)
hub/
  forms.py                          # GuildAnnouncementForm: DROP post_to_discord, ADD discord_channel radio
                                    #   (ChannelRadioSelect + guild-aware __init__/clean); KEEP send_email
                                    # SiteSettingsForm: + the 2 webhook fields
  views.py                          # guild_announcement_create: unchanged call shape — still no-arg
                                    #   notify_members() (the form now saves discord_channel onto the row)
templates/hub/
  guild_edit.html                   # Announcements tab: DELETE post_to_discord include (:481),
                                    #   render the channel picker partial in its place; KEEP send_email (:480)
  partials/_announcement_channel_picker.html   # NEW — the radio group (shared with the approval flow)
  admin/site_settings.html          # NEW "Discord" tab with the 2 webhook fields (form_field.html),
                                    #   placed INSIDE #site-settings-form (before the Save wrapper / </form>)
static/css/hub.css                  # .pl-channel-picker / .pl-channel-option styling (dark + light)
plfog/version.py                    # VERSION bump + changelog (FOLD into the v0.20.1 entry — §8)
```

Home apps: `membership` (announcement model/logic), `core` (config + emit seam), `hub` (forms/views/templates). All inside the existing coverage/mypy scope.

## 4. Data model

### 4.1 `SiteConfiguration` — two shared webhooks (`core/models.py`)

| Field | Type | Note |
|---|---|---|
| `discord_general_webhook_url` | `URLField(max_length=500, blank=True, default="")` | help_text: "Discord webhook for **#general-chat**. Guild leads can post announcements here. Blank = the option is hidden from the picker." |
| `discord_leadership_webhook_url` | `URLField(max_length=500, blank=True, default="")` | help_text: "Discord webhook for **#leadership**. Blank = the option is hidden from the picker." |

- Additive migration; reverse = `RemoveField` (Django-generated, has a real reverse). No data migration.
- Read via `SiteConfiguration.load()` (existing singleton loader, `core/models.py:218`). No new manager.

### 4.2 `GuildAnnouncement` — the channel choice (`membership/models.py`)

**Correcting the base:** on v0.20.1, `send_email` (`:1656`) and `post_to_discord` (`:1660`) are **persisted `BooleanField`s**, and `notify_members(self)` (`:1678`) **takes no arguments** — it reads those two saved values and passes `suppress_email=not self.send_email` / `suppress_guild_broadcast=not self.post_to_discord` into `emit()`. So the choice is NOT a transient kwarg on this base.

Follow that persisted-field pattern for the channel choice — it is the sibling of the surviving `send_email` field, keeps `notify_members()` no-arg, and delivers §10's previously-deferred "audit where each post went" for free (the row records where it went):

**Add** `GuildAnnouncement.DiscordChannel` (`TextChoices`) and a persisted `discord_channel` `CharField`:

```python
class DiscordChannel(models.TextChoices):
    GUILD = "guild", "Our Guild Channel"
    GENERAL = "general", "#general-chat"
    LEADERSHIP = "leadership", "#leadership"
    NONE = "none", "Don't post to Discord"

discord_channel = models.CharField(
    max_length=20,
    choices=DiscordChannel.choices,
    default=DiscordChannel.GUILD,
    help_text="Which Discord channel this announcement posted to (or 'none').",
)
```

**Remove** `post_to_discord`. The "Don't post to Discord" radio (`DiscordChannel.NONE`) is the exact successor to the old `post_to_discord=False` off-state, so the boolean is redundant. **Keep `send_email` exactly as it is.**

- **Migration:** `RemoveField("post_to_discord")` + `AddField("discord_channel", default=GUILD)`. Both are Django-generated and reversible (the reverse of `RemoveField` re-adds `post_to_discord` with its default; the reverse of `AddField` drops `discord_channel`). No data migration — existing rows land on the `GUILD` default, matching the prior "post to your own channel" behavior. Do **not** use `RunPython.noop` for either reverse.

## 5. Business logic (fat models)

### 5.1 `GuildAnnouncement.resolve_discord_webhook(self) -> str`

Maps `self.discord_channel` to a webhook URL. Returns `""` for `NONE` or any channel whose webhook is unset (the emit path treats `""` as "no Discord post"). Fat-model method, fully typed, no side effects:

| `self.discord_channel` | Resolves to |
|---|---|
| `GUILD` | `self.guild.discord_webhook_url` (stripped) |
| `GENERAL` | `SiteConfiguration.load().discord_general_webhook_url` |
| `LEADERSHIP` | `SiteConfiguration.load().discord_leadership_webhook_url` |
| `NONE` | `""` |

Raise a domain `ValueError` on an unknown channel string (fail loudly — `dict[key]` discipline).

### 5.2 `GuildAnnouncement.notify_members(self) -> None`

**Stays no-arg** (matching v0.20.1). In-app + email are unchanged; the only new work is choosing the Discord destination from the persisted field. The email opt-out **must be preserved** — dropping `suppress_email=not self.send_email` would silently re-enable email on every announcement:

```python
webhook = self.resolve_discord_webhook()  # reads self.discord_channel
emit(
    "guild_announcement",
    actor=self.author, target=self,
    context={..., "guild": self.guild, "discord_broadcast_webhook": webhook},
    url=guild_url,
    period=f"announcement:{self.pk}",
    suppress_email=not self.send_email,          # UNCHANGED from v0.20.1 — keep it
    suppress_guild_broadcast=(webhook == ""),    # replaces  not self.post_to_discord
)
```

- The existing `context["guild"]` stays (email/in-app resolvers and the guild-scoped copy still use it).
- `context["discord_broadcast_webhook"]` is the new seam — **always present** for guild announcements (even `""`) so the picker owns the destination — see §5.4.
- `suppress_guild_broadcast=(webhook == "")` is the direct replacement for v0.20.1's `suppress_guild_broadcast=not self.post_to_discord`: `NONE` and any unconfigured channel resolve to `""` → the guild post is suppressed. The rest of the emit call shape is unchanged.

### 5.3 Default-selection resolution (form-side, §6.1)

Default is **Guild Channel** — but if the guild has no webhook, "Guild Channel" is disabled, so the pre-selected radio steps down: **Guild Channel → #general-chat → #leadership → Don't post** (first configured wins). This lives in `GuildAnnouncementForm.__init__` (see §6.1), which sets `self.fields["discord_channel"].initial` to the first configured channel so the picker never opens pre-selected on a disabled option. (For an *edit* of an existing row the bound `discord_channel` value wins, as usual for a ModelForm.)

### 5.4 Emit-time mapping (the one spine change) — `core/events/emit.py`

On v0.20.1, `guild_announcement` posts Discord **twice**: the central post inside the `for spec in event.channels` loop (`:213`) — which dispatches to `DiscordAdapter.broadcast` → `webhook_for_event("guild_announcement")` (the global/route webhook, in `channels.py`) — **and** the guild-own post via `_guild_broadcast()` (`:236`). The picker needs **exactly one** post, to the chosen webhook. Introduce a single explicit override, backward-compatible with every other event. **`webhook_for_event` / `DiscordAdapter.broadcast` are not touched — the seam lives entirely in `emit.py`:**

- **Central loop (`_broadcast_fan_out`, `for spec in event.channels` `:213`):** **skip the DISCORD iteration when `"discord_broadcast_webhook" in ctx`.** The chosen webhook replaces the global-central destination, so guild announcements no longer hit `webhook_for_event`. Other events never set the key, so their central post is byte-for-byte unchanged.
- **Guild post (`_guild_broadcast`, `:236`):** generalize the webhook resolution — `webhook = ctx["discord_broadcast_webhook"] if "discord_broadcast_webhook" in ctx else guild_webhook(ctx.get("guild"))` (existing behavior for any other guild-scoped caller). Post once, best-effort, claiming its own `broadcast:guild:<id>` ledger slot (`:262`, unchanged dedup — `period` already makes it per-announcement). The `if not webhook: return` guard already inside `_guild_broadcast` is the belt for a `""` webhook.
- **Gate composition:** this composes with the **existing `suppress_guild_broadcast` gate** (`if event.has_channel(Channel.DISCORD) and not suppress_guild_broadcast:` `:231`). `notify_members` passes `suppress_guild_broadcast=(webhook == "")`, so the `NONE`/unconfigured case skips `_guild_broadcast` entirely (suspenders); the central iteration is already skipped because the key is present. Net for `NONE`: zero Discord posts, while the per-recipient in-app + email fan-out still runs.

Net: guild announcements post to **one** channel (the chosen one) or none; the v0.20.1 always-on global-central post becomes an explicit **#general-chat** choice. All other events are byte-for-byte unchanged. This is model/spine-layer (`membership` + `core.events`) work; the form/template/thin-view glue in §6 is the frontend deliverable.

### 5.5 Fallback when a chosen channel isn't configured

Two layers:

1. **Form** (`clean_discord_channel`, §6.1) rejects a choice whose webhook is blank with a friendly error — the UI already disables those radios, so this only catches a stale form (admin cleared the webhook between render and submit) or a hand-crafted POST.
2. **Emit** (defense-in-depth): `resolve_discord_webhook()` returns `""` for an unconfigured channel → `suppress_guild_broadcast=True` → the announcement **still** posts on-site + email and the lead still sees "Announcement posted." Log at `INFO` so an admin can see the Discord echo was skipped.

**Silent-failure behavior is intended.** Discord is best-effort by design: if the webhook is present but Discord's echo request fails (e.g. returns 500), `post_embed` logs and never raises, `notify_members()` returns normally, and the view **still shows "Announcement posted."** — the lead is *not* told the Discord post failed. This is deliberate (Discord is a secondary echo, never a blocker; members still got the bell + email). We do not surface Discord delivery status to the lead in this feature.

## 6. UI / UX

### 6.1 Screen — Announcements/Emails tab, "Post an Announcement" card

- **Screen / partial:** `templates/hub/guild_edit.html:472` (the `x-show="section === 'announcements'"` block; the post `<form>` at `:475`, which uses the `announcement_form` context variable), with the picker extracted to `templates/hub/partials/_announcement_channel_picker.html` so the member-approval flow (§6.4) reuses it verbatim.
- **Layout & container:** Inline form on the page (it's already a multi-field form: title, body, hide-after, email toggle, and now the channel picker) — no modal. Stays inside the existing `<div class="hub-card">`. This `<form>` deliberately lives **outside** the main guild-edit form (its own endpoint `hub_guild_announcement_create`).
- **Components used:** `components/form_field.html` for title / body / hide-after and for the surviving v0.20.1 **"Also send email"** toggle (`form_field.html` auto-renders the `send_email` BooleanField as a `pl-toggle`, include unchanged at `:480`); the new **channel picker partial** for the radio group; `pl-btn pl-btn--primary` submit.

**Retire the v0.20.1 Discord toggle:**

- **Delete** the `announcement_form.post_to_discord` include at `guild_edit.html:481` (the "Also post to …'s Discord channel" toggle). The channel picker's **"Don't post to Discord"** radio is its successor.
- **Keep** the `announcement_form.send_email` include at `:480` untouched.

**The controls, named explicitly:**

- **Save/submit:** the existing **"Post Announcement"** button (`pl-btn pl-btn--primary`) at `:483`. It POSTs to `hub_guild_announcement_create` (full-page post, not HTMX). The view (`hub/views.py:1730`) saves the row — now including the picked `discord_channel` — and calls the **unchanged** no-arg `announcement.notify_members()`, then redirects with a Django `messages.success` — **"Announcement posted."** (matching the existing v0.20.1 pattern; full-page post → Django messages, not a toast). On invalid input it redirects back with `messages.error` and the form errors.
- **Channel picker (the new control):** the model's `discord_channel` field, rendered on the form with a **custom `RadioSelect` subclass** (see below), label **"Post to Discord channel"**, rendered by the picker partial as a vertical radio group of option cards:
  - `( ) Our Guild Channel` — pre-selected by default (§5.3).
  - `( ) #general-chat`
  - `( ) #leadership`
  - `( ) Don't post to Discord`
  - `field_hint`: "Members hear it in the app and by email regardless — this only picks the Discord channel."
- **Why radios, not a `pl-toggle` set:** the choice is **mutually exclusive** ("post to exactly one place"). A set of toggles models *independent* booleans and can't express "pick one" — a user could switch two on, and there'd be no honest meaning for that. A radio group is the correct control for one-of-N and is what `form_field.html`/`toggle.html` deliberately do **not** cover, so it's a purpose-built partial.
- **No list/formset here** — this card is a single post form, so the §1 "+ Add / Delete" editor rules don't apply. (The **Recent Announcements** list below it keeps its existing per-row Delete via `confirm_modal.html` and Edit modal — untouched.)

**Disabled / unconfigured options — mechanism (P2 #6, decided):** use a **custom `ChannelRadioSelect(forms.RadioSelect)`** that overrides `create_option()`. `GuildAnnouncementForm.__init__(guild=…)` computes a `configured_channels: set[str]` from `guild.discord_webhook_url` (→ `GUILD`) and `SiteConfiguration.load()` (→ `GENERAL` / `LEADERSHIP`); `NONE` is always configured. It passes that set to the widget. `create_option()` then, for any non-`NONE` value not in `configured_channels`, sets `option["attrs"]["disabled"] = True` and stashs the inline hint via `option["attrs"]["data-hint"] = "Not set up yet."`. The picker partial iterates the bound field's subwidgets and renders a real, greyed `<input disabled>` plus the muted hint from `data-hint` on that option. Keeping the disabled state on the widget (not a parallel template map) means the browser genuinely disables the input and the form/clean layer and the template read from one source of truth.

**Empty state (no channels configured at all):** if the guild has no webhook **and** neither shared webhook is set, the picker shows all three channel options disabled with the *"Not set up yet"* hint and **"Don't post to Discord"** pre-selected (via §5.3 step-down), plus one muted line under the group: *"No Discord channels are set up yet — ask an admin to add them in Site Settings."* The form still submits normally (email + bell only). No dead end.

**States:**
- *Empty:* covered above (no channels configured) — friendly muted note, form still usable.
- *Loading:* n/a — full-page POST, standard browser submit; no HTMX in-flight state.
- *Error:* invalid form → redirect back to the Announcements tab with `messages.error("Couldn't post the announcement — add a title and body.")`; a stale/disabled channel choice → `clean_discord_channel` error **"That Discord channel isn't set up — pick another, or choose 'Don't post to Discord.'"** shown on the field.
- *Success:* `messages.success("Announcement posted.")` on the redirected page; the new row appears in **Recent Announcements**. (Note per §5.5: this shows even if the Discord echo silently failed — intended.)

**Dark + light:** the radio group uses theme tokens only. New `.pl-channel-picker` / `.pl-channel-option` classes in `hub.css`:
- Option card background `var(--hub-surface)`, border `var(--hub-border)`, text `var(--hub-text)`, hint `var(--hub-text-muted)`.
- Native `<input type="radio">` tinted with `accent-color: var(--color-tuscan-yellow)` (works both themes).
- `:checked` option card: border `var(--color-tuscan-yellow)`, subtle `var(--hub-elevated)` fill so the selection reads on both Obsidian and Slate.
- `:disabled` option: `opacity:0.55`, `cursor:not-allowed`.
- **No inline `background`/`color`** on any input; **no `--surface`** token (it falls back to white). Verify both themes.

**Mobile:** the option cards are already full-width and stack vertically (single column) — no reflow issue, no horizontal scroll. Each card is a real, finger-sized tap target (whole label toggles its radio via `<label>` wrapping). Spacing on the 8px grid (`0.5rem` gap between options, `0.75rem` above the group).

### 6.2 Screen — Site Settings › new "Discord" tab

- **Screen / partial:** `templates/hub/admin/site_settings.html` — add a **"Discord"** tab button to the tab row (`vote-tab` pattern, after "Announcements") and a matching `x-show="tab === 'discord'"` section.
- **Placement (P2 #5 — this matters):** the Discord section must go **INSIDE `#site-settings-form`** (opened at `:128`), **before the shared Save wrapper at `:366` / before `</form>` at `:369`**. Do **NOT** place it after `</form>` — the separate announcements-composer `<form>` at `:387` is a *different* form, and fields dropped there would never be saved by the settings POST. Inside the main form, the existing `submitted_tab` hidden input (`:130`) carries the active tab and the shared Save button persists every tab's fields at once.
- **No inline `display` on the tab wrapper (P2 #8 / FRONTEND Rule 12):** do **not** copy the other tabs' `style="display:flex;…"` onto the `x-show="tab === 'discord'"` wrapper — Alpine strips an inline `display` when it reveals an `x-show` element, collapsing the flex/grid. The two `form_field.html` fields flow fine in normal block layout; if any layout is needed, use a `pl-` class, not an inline `display`.
- **Components used:** **`components/form_field.html`** for both webhook fields (proper themed inputs) — **not** the legacy raw `{% for field in form %}` loop the General tab uses. (Per MEMORY `feedback_components_over_legacy_page_patterns`: spec the component, don't copy `site_settings.html`'s raw-field markup.)
- **Fields:** `form.discord_general_webhook_url`, `form.discord_leadership_webhook_url`, added to **`SiteSettingsForm.Meta.fields`** (`hub/forms.py:528`). Each gets a `field_hint` explaining where to paste a Discord webhook URL (Server Settings → Integrations → Webhooks → Copy URL). Both optional.
- **Exclude them from the General tab loop:** the General tab's exclusion filter (`site_settings.html:135`, `{% for field in form %}{% if field.name != … %}`) must also exclude `discord_general_webhook_url` and `discord_leadership_webhook_url` so they render **only** on the Discord tab, not doubled onto General.
- **Save/submit:** the existing Site Settings **Save** button (bottom of the shared form at `:366`–`:367`, hidden only on the Announcements tab via `x-show="tab !== 'announcements'"`) — it already saves every tab's fields at once (`_save_site_settings`, `hub/views.py:3522`) and redirects with the standard save message. No new endpoint.
- **States:**
  - *Empty:* both fields blank on first load — the hints tell the admin what they're for; a muted line at the top of the tab: *"Set these so guild leads can post announcements to #general-chat and #leadership. Leave blank to hide that option from leads."*
  - *Error:* a malformed URL → the field's inline error from Django's `URLField` validation ("Enter a valid URL.").
  - *Success:* standard Site Settings save message.
- **Dark + light:** `form_field.html` already scopes inputs to themed tokens; nothing hand-styled. Verify both themes.
- **Mobile:** two stacked full-width fields — the settings form is already `max-width:760px` and reflows; no table.
- **Gate:** admin-only, inherited from the existing `@fog_admin_required` Site Settings view (`admin_site_settings`, `hub/views.py:3545`). Guild leads never see this tab.

### 6.3 Interplay with the guild's own `discord_post_enabled`

`Guild.discord_post_enabled` (`membership/models.py:1018`) is the guild-level "also post to our own channel by default" flag consulted by `guild_webhook()`. With a per-post picker, the **per-post choice wins** — picking "Our Guild Channel" resolves straight to `guild.discord_webhook_url` via `resolve_discord_webhook()` (the emit seam hands `_guild_broadcast` the chosen webhook directly, bypassing `guild_webhook()`'s flag check), so the "Guild Channel" radio is available whenever that URL is set. The flag continues to exist for any non-picker guild-scoped path and is left untouched. Retiring the guild-level flag is deferred (§10) to keep this change surgical. (This is separate from the per-announcement `post_to_discord` field, which this feature **does** retire — see §4.2.)

### 6.4 Composition — the same picker on member-submission approval

The same-day **member-submitted announcements** spec adds a lead-facing **Approve** action for member drafts. That approval is where the announcement first notifies members, so it carries the **same channel picker** (the `_announcement_channel_picker.html` partial). Because `discord_channel` is a **persisted field** and `notify_members()` is no-arg, the approval flow simply **sets `announcement.discord_channel` from the cleaned choice, saves, and calls `announcement.notify_members()`** — the same no-arg call the lead's own post uses. No second copy of the resolution logic — both flows share `resolve_discord_webhook` + `notify_members` + the picker partial (default resolved the same way via §5.3). If that spec lands first, this one only adds the picker to the *lead's own* post form; if this lands first, that spec reuses the partial and the persisted field already in place.

## 7. Notifications / emails / activity

No new event, template, or trigger — this reshapes the destination of the existing `guild_announcement` Discord broadcast. The in-app bell row, the opt-out email, and the `guild_announcement` `SiteActivity` are all unchanged. The Discord embed copy (subject/body) is the existing DB-editable `guild_announcement` Discord copy; it now lands in the chosen channel. Nothing in `core/events/copy.py`, `webhook_for_event`, `DiscordAdapter.broadcast`, or the email shell changes.

## 8. Build order (phased; each phase ships green)

1. **Config + model (model layer).** Add the two `SiteConfiguration` URLFields (+ migration). Add `GuildAnnouncement.DiscordChannel` + the persisted `discord_channel` field; **remove `post_to_discord`** (RemoveField + AddField migration, both reversible). Add `resolve_discord_webhook()`. Specs for the resolver's four branches. Full suite + lint + mypy green.
2. **Emit seam.** Honor `ctx["discord_broadcast_webhook"]` in `_broadcast_fan_out` (skip central DISCORD iteration) / `_guild_broadcast` (post to the chosen webhook); update `notify_members()` to read `self.discord_channel`, keep `suppress_email=not self.send_email`, and set `suppress_guild_broadcast=(webhook == "")`. Specs: chosen webhook posts once; global-central skipped for guild announcements; other events unaffected; `NONE`/unconfigured → no Discord but in-app+email still deliver; email opt-out still honored; dedup on re-emit. Green.
3. **Lead-facing UI.** `GuildAnnouncementForm` drops `post_to_discord`, gains the `discord_channel` radio (`ChannelRadioSelect`) + guild-aware `__init__`/`clean`; picker partial + `hub.css`; delete the `post_to_discord` include at `guild_edit.html:481` and render the picker there instead (keep `send_email` at `:480`). Verify both themes + mobile. Green.
4. **Admin config UI.** "Discord" tab in Site Settings with the two fields via `form_field.html`, placed inside `#site-settings-form` before the Save wrapper; exclude them from the General loop; add to `SiteSettingsForm.Meta.fields`. Green.
5. **Housekeeping.** Bump `plfog/version.py` VERSION; curate the CHANGELOG (see note). Green.

> Spec only — do not build until approved.

**Changelog note:** the v0.20.1 entry **"Guild pages: more polish and clearer announcements"** already carries the member-facing announcement bullet ("you can now choose whether to also email everyone who's joined and whether to also post it to your guild's Discord channel"). This feature is an **intra-cycle refinement of that unshipped work**, so **edit that existing bullet** — reword its Discord half to *"…and choose exactly where it posts on Discord: your guild's channel, #general-chat, or #leadership"* — re-stamp the entry's `version`/`date` to the new VERSION and move it to the top. Do **not** add a second entry (the retirement of the old toggle and the emit-seam changes are invisible plumbing on unshipped 0.20.x work — no separate line).

## 9. Testing

BDD `*_spec.py` under each app's `spec/`, `describe_*`/`it_*`, factory-boy, ≥98% coverage, run in the `plfog-web` Docker image (`--no-cov` for subsets).

- **`membership` — `GuildAnnouncement`:**
  - `resolve_discord_webhook`: each of GUILD / GENERAL / LEADERSHIP returns the right configured URL from `self.discord_channel`; each returns `""` when its source is blank; `NONE` returns `""`; an unknown persisted value raises `ValueError`.
  - `notify_members()` (no-arg): passes the resolved webhook in `context["discord_broadcast_webhook"]`; keeps `suppress_email=not self.send_email`; sets `suppress_guild_broadcast=True` iff webhook is `""`; `period` still keyed to pk (re-call dedups). A row defaults to `discord_channel == GUILD`.
  - Migration: `post_to_discord` removed; `discord_channel` present with default `GUILD`; both directions reversible.
- **`core.events.emit`:**
  - With `ctx["discord_broadcast_webhook"]` set → exactly one Discord post, to that URL; the central `for spec` DISCORD iteration (→ `webhook_for_event`) is **not** run.
  - Key absent → central behavior unchanged (a `site_announcement`-style event still posts to the global webhook via `DiscordAdapter.broadcast`).
  - Webhook `""` + `suppress_guild_broadcast=True` → no Discord post, but in-app + email still deliver.
  - Re-emit with same `period` → deduped (no double post) via the `broadcast:guild:<id>` ledger slot. Mock HTTP with `respx`.
- **`hub` — `GuildAnnouncementForm`:**
  - `post_to_discord` no longer in `Meta.fields`; `discord_channel` present with the `ChannelRadioSelect` widget; `send_email` still present.
  - Default selection resolves per §5.3 across the configured/unconfigured matrix; unconfigured channels render `disabled` with the `data-hint`; `clean_discord_channel` rejects a blank-webhook choice with the friendly message; a valid choice passes.
  - `guild_announcement_create` saves the chosen `discord_channel` onto the row and calls no-arg `notify_members`, redirecting with the success message; invalid form → error message, no notify.
- **`hub` — Site Settings:**
  - The two webhook fields save via the existing settings POST (`SiteSettingsForm`); malformed URL surfaces the field error; the fields are excluded from the General tab render and present on the Discord tab.
- **Template states:** picker renders the disabled/hint state and the all-unconfigured empty state (assert on rendered HTML, per MEMORY `reference_nested_form_save_bug` — a test-client POST won't catch structure, so parse the HTML for the disabled radios and the empty-state note). Assert the Discord tab section is inside `#site-settings-form` (before `</form>`), not after it.

No tz/date-window gotchas (no scheduling here).

## 10. Open / deferred

- ~~**Persisting the chosen channel** on `GuildAnnouncement`~~ — **now done** in this spec (§4.2): `discord_channel` is a persisted `CharField`, giving the "audit where each post went" for free and keeping `notify_members()` no-arg like its `send_email` sibling.
- **Retiring `Guild.discord_post_enabled`** (the guild-*level* default flag) now that per-post posting is a choice — deferred; left untouched (§6.3) to keep this change surgical. (The per-*announcement* `post_to_discord` field **is** retired here — §4.2.)
- **Write-only treatment of the two shared webhook URLs** (never echo the stored value back, like `DiscordRouteForm`) — deferred; we follow the existing visible-URLField precedent of the per-guild `discord_webhook_url` and `mailchimp_api_key` for consistency. Revisit if these are treated as secrets.
- **Per-guild #leadership routing** (a leadership channel scoped to one guild) — out of scope; leadership is a single makerspace-wide channel by the locked decision.

## Out of scope

- Any change to the **email** or **in-app bell** fan-out (explicitly unchanged), and the surviving `send_email` toggle.
- New Discord embed copy, a new event key, or notification-preference changes; `webhook_for_event` / `DiscordAdapter.broadcast` are untouched.
- Threading, reactions, or replies in Discord; posting to arbitrary/ad-hoc channels beyond the three.
- Editing the two shared webhooks anywhere but Site Settings (no per-guild override of #general-chat / #leadership).
- The member-submission *creation/moderation* flow itself — this spec only guarantees the picker is reused at **approval** time (§6.4).

## Done checklist

- [ ] `SiteConfiguration` has `discord_general_webhook_url` + `discord_leadership_webhook_url` (migration + reverse); read via `load()`.
- [ ] Site Settings **Discord** tab edits both via `form_field.html`, placed **inside** `#site-settings-form` (before the Save wrapper / `</form>`); fields excluded from the General loop; saved by the existing settings POST; admin-gated; no inline `display` on the `tab === 'discord'` wrapper.
- [ ] `GuildAnnouncement.DiscordChannel` + persisted `discord_channel` field added; **`post_to_discord` removed** (reversible migration); `send_email` untouched; `resolve_discord_webhook()` implemented (fat model).
- [ ] `notify_members()` stays no-arg, reads `self.discord_channel`, **keeps `suppress_email=not self.send_email`**, sets `suppress_guild_broadcast=(webhook == "")`, and puts `discord_broadcast_webhook` in context.
- [ ] Emit posts the Discord embed **once** to the chosen webhook (skip central DISCORD iteration when the ctx key is set; generalized `_guild_broadcast`); other events unchanged; `NONE`/unconfigured → no Discord, bell+email still deliver.
- [ ] Post form shows the **radio** channel picker (Guild Channel default, #general-chat, #leadership, Don't post) via `ChannelRadioSelect`; unconfigured options disabled with a hint; all-unconfigured empty state present. The `post_to_discord` toggle include (`guild_edit.html:481`) is **deleted**; the `send_email` include (`:480`) is kept.
- [ ] "Post Announcement" saves `discord_channel` + redirects with the success message; `clean_discord_channel` rejects a stale/unconfigured choice with a friendly error; a silent Discord failure still shows "Announcement posted." (intended, §5.5).
- [ ] `.pl-channel-picker` / `.pl-channel-option` styled with theme tokens only — verified on **dark and light**; no inline input `background`/`color`; no `--surface`.
- [ ] Mobile: options stack, full-width tap targets, 8px-grid spacing, no horizontal scroll.
- [ ] Picker partial + no-arg `notify_members` + persisted `discord_channel` reused by the member-submission **approval** flow (§6.4).
- [ ] Specs green (≥98% coverage) in `plfog-web`; `ruff format` + `ruff check` clean; mypy clean.
- [ ] `VERSION` bumped; the v0.20.1 "clearer announcements" changelog bullet **edited** (not duplicated), re-stamped, moved to top.
