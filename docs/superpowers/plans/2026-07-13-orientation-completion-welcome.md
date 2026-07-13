# Orientation Completion Welcome — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — the guild's in-app notifications (bell / `/notifications/`) + the guild's Discord channel. No new screens; no admin UI.
**Related:** `docs/superpowers/plans/2026-06-21-guild-orientations.md` (the orientation lifecycle this hooks into); the notification spine (`core/events/`); `2026-07-10`/`2026-07-12` fogstorm batches that added the other guild broadcasts (`guild_announcement`, `event.*`).

---

## 1. Summary

When a member finishes their orientation with a guild, that guild's existing members get a warm, automatic welcome — an in-app notification and a post in the guild's Discord channel: *"[member] just completed their orientation! Please welcome them to [guild]."* It fires by itself the moment the orientation is marked complete (whether a lead marks it, or the nightly auto-complete job closes it out), so newcomers land in a room that already knows they're here. Nobody has to remember to post it, and there's nothing to configure.

This is a **pure notification feature** — one new event key wired onto the existing notification spine at a single fire point. No new models, no migration, no UI, no forms.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Which channels | **BOTH** in-app **AND** the guild's Discord channel — reuse the spine's dual fan-out (`_IN_APP_ON`, `_DISCORD_ON`). |
| Opt-out | **None.** Warm by default; the welcome always fires. (Directory-privacy naming is flagged in §10, not built.) |
| Audience | **The guild's existing members** — the people who welcome the newcomer. The guild is the one the orientation was for (`booking.guild`). |
| Fire point | **One place:** `complete_orientation(booking)` — covers both the manual "lead marks complete" path and the cron `auto_complete()` that loops it. |
| Email channel | **No.** In-app + Discord only. This is a light social nudge to the guild, not a transactional email — keep it out of inboxes. |
| Idempotency | `period="booking:<pk>:completed"` so a manual re-complete of the same booking never double-posts. |

---

## 2. What already exists (reuse, don't reinvent)

The whole feature is assembly. Every part below is confirmed in the codebase at the cited location.

| Need | Existing thing | Location |
|---|---|---|
| Single fire point (manual + cron) | `complete_orientation(booking)` — called directly by the lead-marks-complete view **and** by `auto_complete()` (the `auto_complete_orientations` cron command) | `membership/orientations.py:303`, cron loop at `:327` |
| Emit one event across channels | `core.events.emit.emit(...)` | `core/events/emit.py:44` |
| Context builder (guild, greeting name, guild URL) | `_context(booking)` → `guild`, `greeting_name` (= `member.display_name`), `guild_url` (absolute) | `membership/orientations.py:117` |
| The booking's guild / member / slot | `OrientationBooking.guild` (denormalized FK), `.member`, `.slot` | `membership/models.py:4742` |
| Audience = active guild members | `Recipients.GUILD_MEMBERS` → resolver `guild_members(ctx)` (active members with a `GuildMembership`; **not** directory-filtered) | `core/events/registry.py:77`, `core/events/resolvers.py:161` |
| In-app "on" + Discord "on" channel specs | `_IN_APP_ON` (`:150`), `_DISCORD_ON` (`:310`) | `core/events/registry.py` |
| Discord routes to the guild's own channel, gated | `emit → _guild_broadcast → discord.guild_webhook(guild)` — posts only when `Guild.discord_post_enabled` **and** a non-blank `discord_webhook_url`; blank → silent no-op | `core/events/emit.py:256`/`261`, `core/events/discord.py:55,74-76` |
| Curated copy catalogue (placeholders + sample + per-channel copy) | `_CURATED` — pattern to copy is `guild_announcement` | `core/events/copy.py:125`, sibling at `:323` |
| Audience description (for the catalogue) | `_AUDIENCE_DESCRIPTIONS[Recipients.GUILD_MEMBERS]` = "Every active member of the guild." — **already present, no new line needed** | `core/events/copy.py:90` |
| Copy fallback when the DB row isn't seeded | `resolved_copy()` falls back to `copy_module.default_copy_for(...)` — the code default renders even if `seed_notification_templates` hasn't run | `core/events/templates.py:60-64` |
| Idempotency ledger (per user×channel + broadcast slot) | `EventDelivery` ledger via `period=` | `core/events/emit.py` (`_record_broadcast` / per-recipient claim) |
| Precedent for a guild broadcast built from one emit | `GuildAnnouncement.notify_members` (guild object + string placeholders in one context) | `membership/models.py:1924` |

**Gaps to close (all small):**
1. Register one new event key `orientation.completed` in `core/events/registry.py`.
2. Add one `_CURATED["orientation.completed"]` copy block in `core/events/copy.py`.
3. Add ~8 lines of emit wiring at the tail of `complete_orientation()`.

That's the entire build.

---

## 3. Where the code lives

No new files. Three existing files touched, plus the version bump.

```
core/events/registry.py        # + ORIENTATION_COMPLETED constant, + EventType in _NEW_EVENTS
core/events/copy.py            # + _CURATED["orientation.completed"] copy block
membership/orientations.py     # + emit(...) at the tail of complete_orientation()
plfog/version.py               # VERSION bump + CHANGELOG entry (0.22.0 line)

# tests
core/events/spec/…             # copy/registry lockstep coverage (existing pattern)
membership/spec/orientations_spec.py   # fire-once / dedupe / no-op-Discord / audience
```

Home apps: `core` (spine registration + copy) and `membership` (the fire point + tests). Both are already in the coverage/mypy scope, so nothing new to add to config.

---

## 4. Data model

**No new models. No migration.** The event key and its copy are *catalogue* entries (Python constants seeded into DB copy rows by `seed_notification_templates`), not schema. Delivery/idempotency uses the existing `EventDelivery` ledger.

New registry entry (append to `_NEW_EVENTS` in `core/events/registry.py`, constant beside the others near `:336`):

```python
ORIENTATION_COMPLETED = "orientation.completed"  # dotted, matches the new-event vocabulary

EventType(
    key=ORIENTATION_COMPLETED,
    label="Orientation completed — welcome",
    description="A member finished their orientation; welcome them to the guild.",
    category="Orientations",
    recipient=Recipients.GUILD_MEMBERS,
    channels=(_IN_APP_ON, _DISCORD_ON),   # in-app + the guild's Discord channel; no email
    activity_kind=None,                    # SiteActivity(ORIENTATION_COMPLETED) is already logged by
                                           # complete_orientation() — emit must NOT log a second row
                                           # (same reasoning as class_published, registry.py:359)
)
```

Why `activity_kind=None`: `complete_orientation()` already calls `SiteActivity.log(SiteActivity.Kind.ORIENTATION_COMPLETED, …)` at `orientations.py:308`. Letting the event also carry an `activity_kind` would write a duplicate activity row. This mirrors the `class_published` precedent exactly.

---

## 5. Business logic (fat models)

All the logic is one `emit()` at the tail of the existing service function — the fire point already owns "an orientation just completed," so the welcome belongs there, right after the activity log.

`membership/orientations.py` — `complete_orientation(booking)`, appended after the existing `mark_completed()` + `SiteActivity.log(...)` + the (unchanged) thank-you-email block:

```python
# Warm welcome to the guild's members — always fires (no opt-out), in-app + the guild's
# own Discord channel. Copy-mode: no title/body, rendered from the seeded catalogue copy.
ctx = _context(booking)  # guild, greeting_name (= member.display_name), guild_url
emit(
    "orientation.completed",
    actor=None,                       # system event; the member is the subject, not the actor
    target=booking,
    context={
        "guild": booking.guild,       # resolver key (guild_members) + _guild_broadcast destination
        "member_name": booking.member.display_name,
        "guild_name": booking.guild.name,
        "guild_url": ctx["guild_url"],
    },
    url=ctx["guild_url"],             # the in-app bell row's click-through
    period=f"booking:{booking.pk}:completed",
)
```

Guards & behavior:
- **One context dict serves both roles.** The resolver reads `context["guild"]`; the copy renderer substitutes only the documented placeholders (`member_name`/`guild_name`/`guild_url`) and ignores the extra `guild` object key — exactly how `GuildAnnouncement.notify_members` (`models.py:1964`) mixes the two.
- **No `discord_broadcast_webhook` in context.** That key is the announcement channel-picker override. We omit it deliberately, so `_guild_broadcast` falls to `discord.guild_webhook(guild)` — the guild's *own* channel, gated on its opt-in — which is the intended destination.
- **Side effects:** one in-app bell row per existing active guild member; one Discord embed to the guild's channel (when enabled). No email. No new activity row (see §4).
- **Fires from both paths for free:** the cron `auto_complete()` (`:327`) loops `complete_orientation()`, so the welcome ships on auto-completion too — no separate wiring.

No new domain exceptions: `emit()` fails loudly on an unregistered key (a programming error caught in tests), and Discord posting is best-effort (`post_embed` logs, never raises).

---

## 6. The message experience  ← UX-completeness, adapted to notifications (copy, audience, empty/skip states)

There is no screen or form to build. The "UI" here is the **message a reader receives**, judged by the same bar: right audience, clear copy, no dead ends, and every skip/empty path handled. Walked channel by channel:

### In-app notification (the bell + `/notifications/`)
- **Renders through** the existing notification-row + `/notifications/` page (built `v0.21.3`). No new template — the row is standard, so dark/light theming and mobile reflow are already handled by that component. Nothing new to style.
- **Copy (seeded default):**
  - Title: `Welcome {{ member_name }} to {{ guild_name }}!`
  - Body: `{{ member_name }} just completed their orientation. Say hello and give them a warm welcome to {{ guild_name }}.`
- **Click-through:** the row's `url` is the guild detail page (`hub_guild_detail`, absolute) — a live link to the guild, never a dead end. (Subject-noun-links-to-its-page, per FRONTEND.md email/notification standard.)
- **Audience shown to:** every **active member who has joined the guild** (has a `GuildMembership`). See the audience nuance below.

### Discord post (the guild's channel)
- **Renders as** a standard spine embed via `discord.post_embed`, built from the DISCORD copy (title → embed title, body → description).
- **Copy (seeded default, DISCORD channel):**
  - Title: `Welcome {{ member_name }}!`
  - Body: `{{ member_name }} just completed their {{ guild_name }} orientation — please give them a warm welcome! {{ guild_url }}`
  - (The Discord body carries `{{ guild_url }}` as a plain link because a Discord embed has no separate click target; the in-app row uses its `url` field instead, so its body stays clean.)
- **Warm, plain, no jargon** — matches the FRONTEND.md notification voice.

### Audience nuance — state it plainly, confirm it's intended
- `guild_members` resolves to members who hold a **`GuildMembership`** for the guild. **Completing an orientation does not auto-join the guild**, so:
  - The welcome reaches the guild's **existing** members — the people we *want* to greet the newcomer. **Correct.**
  - The **newcomer themselves is (usually) not in the audience** — they have no `GuildMembership` yet. **Also correct:** they're the *subject* of the welcome, not a recipient of it. (If they happen to already be a member joining a second guild's orientation, they'd be in that guild's roster and would see it — harmless.)
- This is the **intended behavior** and the spec records it so a future reader doesn't "fix" it into notifying the newcomer. Whether to *also* send the newcomer a "you're all set" note is an explicit open question (§10), not part of this build.

### Empty / skip / error states — never just the happy path
- **Guild has no other members yet** → `guild_members` returns `[]` → no in-app rows created. No error, no empty shell; the Discord post can still fire. (First orienter into a brand-new guild: nobody to notify in-app, and that's fine.)
- **Discord not configured** → guild's `discord_post_enabled` is off, or `discord_webhook_url` is blank → `guild_webhook(guild)` returns `""` → `_guild_broadcast` **silently no-ops**. The in-app welcome still fires. No error surfaced to anyone. (This is the common case for guilds that haven't wired Discord.)
- **Re-complete / cron re-run** → the `period="booking:<pk>:completed"` ledger slot dedupes: the same booking never produces a second welcome on either channel. (Manual complete then a later cron pass, or a double-click, both land once.)
- **Discord post fails at the API** → `post_embed` logs and returns falsy; it never raises, so a flaky webhook can't break `complete_orientation()` or the in-app fan-out.
- **Copy not yet seeded on a fresh deploy** → `resolved_copy()` falls back to the code default (`templates.py:60`), so the message still renders correctly before `seed_notification_templates` runs.

### Dark/light + mobile
- N/A to build — the in-app row and `/notifications/` page are existing, already-themed, already-responsive components. No new CSS, no new form control, so none of the dark-mode form-control pitfalls apply. (Explicitly: verify nothing, because nothing new is styled.)

---

## 7. Notifications / emails / activity

The spine wiring, gathered in one place:

| Aspect | Value |
|---|---|
| Event key | `orientation.completed` (new; dotted vocabulary) |
| Trigger | `complete_orientation(booking)` (manual + cron auto-complete) |
| Audience | `Recipients.GUILD_MEMBERS` → `guild_members` resolver (active members with a `GuildMembership`; **not** directory-filtered) |
| Channels | in-app (ON) + Discord (ON) — **no email, no push** |
| Discord destination | the guild's own channel via `guild_webhook(guild)`, gated on `discord_post_enabled` + non-blank `discord_webhook_url` |
| Opt-out | none (warm by default) — the in-app channel default is ON; there's no per-member preference exposed for it |
| Idempotency `period` | `booking:<pk>:completed` (per-booking, per-lifecycle-step — distinct from the existing `:thankyou`, `:request`, `:confirm`, `:cancel` buckets on the same booking) |
| Activity row | **none from emit** (`activity_kind=None`); `complete_orientation` already logs `SiteActivity.ORIENTATION_COMPLETED` |
| Copy placeholders | `member_name`, `guild_name`, `guild_url` (lockstep with `sample_context`) |
| Absolute URLs | `guild_url` via `_context(booking)` → `_absolute_url(reverse("hub_guild_detail", …))` |
| Seed | `seed_notification_templates` recommended post-deploy; **not a hard dependency** (code-default fallback) |

Copy block to add to `_CURATED` in `core/events/copy.py` (keep `placeholders` and `sample_context` keys identical — a test asserts it):

```python
"orientation.completed": EventCopy(
    placeholders=("member_name", "guild_name", "guild_url"),
    sample_context={
        "member_name": "Robin Vale",
        "guild_name": "Metal Guild",
        "guild_url": "https://pastlives.example/guilds/metal-guild/",
    },
    channels={
        Channel.IN_APP: ChannelCopy(
            subject="Welcome {{ member_name }} to {{ guild_name }}!",
            body_text=(
                "{{ member_name }} just completed their orientation. "
                "Say hello and give them a warm welcome to {{ guild_name }}."
            ),
        ),
        Channel.DISCORD: ChannelCopy(
            subject="Welcome {{ member_name }}!",
            body_text=(
                "{{ member_name }} just completed their {{ guild_name }} orientation — "
                "please give them a warm welcome! {{ guild_url }}"
            ),
        ),
    },
),
```

(No `Channel.EMAIL` entry — the event has no email channel. `copy_for()` still resolves cleanly for any channel via its IN_APP fallback.)

---

## 8. Build order (phased; each phase ships green)

1. **Spine registration + copy.** Add the `ORIENTATION_COMPLETED` constant + `EventType` to `core/events/registry.py`; add the `_CURATED` copy block to `core/events/copy.py`. Add/extend the registry+copy lockstep spec (placeholders == sample_context; event registered; audience description resolves). Ships green with zero behavior change (nothing emits it yet).
2. **Fire point.** Append the `emit("orientation.completed", …)` to `complete_orientation()` in `membership/orientations.py`. Add the orientation specs (§9). Full suite + lint + mypy green.
3. **Housekeeping.** Bump `plfog/version.py` `VERSION` → `0.22.0` and add the member-facing CHANGELOG entry (§ below). `ruff format .` + `ruff check .`.

Post-deploy op (not code): run `seed_notification_templates` on prod so the copy is DB-editable (safe to skip — the code default renders regardless).

CHANGELOG entry (new grouped feature at the top, `version == "0.22.0"`):

> **A warm welcome when you finish orientation**
> When a member completes their orientation with a guild, that guild now gets an automatic hello — an in-app notification and a post in the guild's Discord channel — so newcomers are welcomed the moment they're through the door.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image, ≥98% coverage gate. Use `--no-cov` for the subset while iterating.

**`membership/spec/orientations_spec.py` — `describe_complete_orientation`:**
- `it_posts_the_welcome_to_existing_guild_members_on_manual_complete` — guild has 2 other active members with `GuildMembership`; complete → each gets an in-app `orientation.completed` row; assert title/body carry the member + guild names.
- `it_posts_the_welcome_when_auto_complete_closes_the_booking` — drive `auto_complete()` (confirmed booking, slot ended) → same welcome fires (proves the cron path is covered by the single fire point).
- `describe_idempotency`:
  - `it_does_not_double_post_when_completed_twice` — call `complete_orientation(booking)` twice (or manual-then-cron) → exactly one in-app delivery per member and one Discord post (the `booking:<pk>:completed` ledger slot dedupes).
- `describe_discord_gating`:
  - `it_posts_to_the_guild_channel_when_discord_is_enabled` — guild `discord_post_enabled=True` + a webhook set → assert `post_embed` called once with the guild's webhook (mock `discord.post_embed` / `guild_webhook`). Assert the embed body contains the guild URL.
  - `it_silently_skips_discord_when_no_webhook` — `discord_post_enabled=False` **or** blank `discord_webhook_url` → `post_embed` not called; the in-app welcome still fires (assert both).
- `describe_audience`:
  - `it_does_not_notify_the_newcomer_who_has_not_joined` — the completing member has no `GuildMembership` for that guild → they receive no in-app row (confirms the subject-not-recipient behavior is intended).
  - `it_notifies_a_directory_hidden_member` — a guild member with `show_in_directory=False` still gets the in-app welcome (guild_members ignores directory privacy — locks the current behavior in).
  - `it_creates_no_rows_when_the_guild_has_no_other_members` — solo guild → empty audience, no error, Discord may still post.
- `it_logs_exactly_one_completion_activity` — assert `SiteActivity` gets one `ORIENTATION_COMPLETED` row, not two (guards `activity_kind=None`).

**`core/events/spec/…` (existing copy/registry lockstep):**
- `it_registers_orientation_completed_with_guild_members_and_discord` — event resolves; recipient is `GUILD_MEMBERS`; channels include in-app + Discord.
- `it_keeps_orientation_completed_placeholders_and_sample_context_in_lockstep` — `set(placeholders) == set(sample_context)`, and every `{{ … }}` used in the copy is a documented placeholder (extends the existing catalogue lockstep test that already covers all `_CURATED` entries — likely passes automatically once the block is added; assert explicitly).

**Gotchas:**
- Seed a `MembershipPlan` before creating members with linked users (the member-creation signal skips otherwise) — see `reference_e2e_needs_membershipplan`.
- Mock `core.events.discord.post_embed` (and/or `guild_webhook`) rather than hitting a real webhook; `respx` if any HTTP leaks through.
- Don't assert on visible changelog text (the "what's new" widget echoes the CHANGELOG) — assert on delivery rows / mock calls.

## 10. Open / deferred

1. **Directory-privacy gate on the *public* Discord post.** Per the locked "no opt-out," we always name the member. But `Member.show_in_directory` (`models.py:401`) is the member's "may I be named publicly" signal, and `guild_members` deliberately ignores it (privacy governs the public roster, not your own guild's messages). The **in-app** welcome stays unconditional (it's inside the guild — like a guild announcement). If the user later wants the **public Discord** post to honor privacy, it's a one-line guard in `complete_orientation`: skip/soften the Discord copy (e.g. "A new member" instead of the name) when `not member.show_in_directory and not member.must_be_listed_in_directory` (`models.py:864` — roles that can never hide). **Deferred; flag only, don't build.**
2. **Also notify the newcomer?** Today the newcomer is the subject, not a recipient (they have no `GuildMembership`). A separate "you're all set — here's your guild" note to the member on completion is a reasonable follow-up, but it's a *different* audience and message (member-facing, possibly email). Out of scope here; raise as its own small feature if wanted. (Note: the existing per-guild thank-you email at `complete_orientation` `:310` already partly covers "message the member on completion" when a lead has configured one.)
3. **Linking the member's profile instead of the guild.** The copy links the **guild** (`hub_guild_detail`) because there is **no per-member profile page** — only the member *directory* (`hub_member_directory`, `hub/urls.py:10`). If a per-member profile page is added later, the welcome could deep-link to the newcomer (guarded on directory visibility, per #1). Deferred until such a page exists.
4. **Email channel.** Deliberately omitted (in-app + Discord only). If guilds later ask for an email digest of new members, add `_EMAIL_OFF`/opt-in to the event's channels — but that's a preference-surface change, not this build.
