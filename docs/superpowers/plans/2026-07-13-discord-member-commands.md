# Discord Member Slash Commands — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** Discord (Past Lives server) — four member-facing slash commands answered by the app's
interactions endpoint. No FOG-hub or book-CMS pages change; all links point back to existing hub/book pages.
**Related:** **Depends on** `docs/superpowers/plans/2026-07-13-discord-interactions-foundation.md` — the foundation
carries the signed interactions endpoint, the command router/registration, Member resolution, the unlinked
prompt, the ephemeral/deferred reply helpers, and the channel→guild map. This spec **reuses** those and does not
re-specify them. Build the foundation first.

---

## 1. Summary

Members can do four things from inside Discord, without leaving to the hub:

- **`/schedule-orientation`** — request an orientation for a guild (pick a posted slot or propose a custom time).
- **`/whats-on`** — see what community events and classes are coming up in the next 7 days.
- **`/balance`** — check their own tab balance and remaining limit.
- **`/info`** — pull up a guild's rules, next meeting, FAQ, links, and staff — the guild page, summarized.

Each command is a **thin handler**: it resolves the caller's `Member` (via the foundation), calls an existing
reusable model/manager method, and formats a Discord reply. No new business logic beyond ~6 lines of glue for the
custom-orientation path, which lands in a fat-model service function (not the handler).

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Which commands hard-require a linked Member | `/schedule-orientation` and `/balance` — they act on a specific person. Unlinked → the foundation's unlinked prompt. |
| `/whats-on` and `/info` for unlinked callers | **Do not** hard-require linking — they show *public* info. Resolve the Member opportunistically (for future personalization) but fall back to public content so an unlinked member still gets an answer, no dead end. |
| Ephemeral vs public | `/balance` (personal money) and `/schedule-orientation` (personal request) → **ephemeral**. `/whats-on` → **ephemeral** (keeps channels tidy; it's a lookup). `/info` → **public** (whole channel benefits from the guild summary). |
| Deferred vs immediate | Only `/schedule-orientation` defers — it sends emails + fans out lead notifications, may exceed Discord's 3s ACK. The other three are fast DB reads and reply immediately. |
| Where the custom-slot glue lives | A new `orientations.request_custom_orientation(...)` service function (fat model layer), mirroring `hub/views.py:952`, so the handler stays thin. The existing hub view keeps its inline copy for now (optional later de-dup — not in scope). |
| Guild disambiguation | `/schedule-orientation` and `/info` auto-detect the guild from the **channel→guild map** when run in a guild channel; otherwise the caller supplies a `guild` option. |

---

## 2. What already exists (reuse, don't reinvent)

Every command is assembly over verified plumbing. Anchors confirmed in the codebase:

| Need | Existing thing | Location |
|---|---|---|
| **Foundation:** endpoint, router, command registration | (dependency spec) | `docs/…/2026-07-13-discord-interactions-foundation.md` |
| **Foundation:** resolve caller → `Member` via `discord_user_id` | `resolve_member(interaction)` (foundation) | dependency spec; snowflake stored on `Member.discord_user_id` (`membership/models.py:341`), link via `link_discord` (`:645`), `is_linked`-style check (`:496`) |
| **Foundation:** unlinked prompt (ephemeral, links to link-account page) | foundation helper | dependency spec |
| **Foundation:** ephemeral reply / defer / followup helpers | foundation helpers | dependency spec |
| **Foundation:** channel→guild map | `guild_for_channel(channel_id) -> Guild \| None` | dependency spec |
| Request an orientation (emails member + notifies leadership + logs) | `orientations.request_orientation(slot, member, note="")` | `membership/orientations.py:180` |
| Bookable posted slots for a guild | `guild.orientation_slots.bookable()` / `.upcoming()` | `membership/models.py:4576`, `:4580` |
| One-off custom slot creation pattern | `guild_orientation_request_custom` view | `hub/views.py:952` |
| Orientation acceptance gate | `GuildOrientationSettings.is_accepting`, `.allow_custom_requests`, `.default_duration_minutes`, `.default_location` | `membership/models.py:4522` (+ fields used at `hub/views.py:966–981`) |
| Duplicate guards | `member.active_orientation_for(guild)`, `member.is_oriented_for(guild)` | `membership/models.py:856`, `:852` |
| Slot booking error | `OrientationError` (raised by `slot.book`) | `membership/models.py` (imported in `hub/views.py:932`) |
| Upcoming community events (incl. recurring) | `CommunityEvent.objects.upcoming()`, `candidates_for_window(frm,to)`, `occurrences_in(frm,to)` | `membership/models.py:2839`, `:2845`, `:3138` |
| Event public page URL (absolute) | `CommunityEvent.public_url` / `.absolute_url` | `membership/models.py:3281`, `:3303` |
| Bookable classes | `ClassOffering.objects.bookable()`; `ClassSession.objects.upcoming_public()` | `classes/models.py:121`, `:1085` |
| Class public page URL (absolute, book surface) | `ClassOffering.public_url` | `classes/models.py:341` |
| The caller's tab | `Tab.objects.get_or_create(member=member)` → `current_balance`, `remaining_limit`, `has_payment_method`, `can_add_entry` | `billing/models.py:339`, `:361`, `:348`, `:352` |
| Tab feature gate | `SiteConfiguration.load().tab_payments_enabled` | `core/models.py:239` |
| Guild page content | `essential_rules` (`:1093`), `about` (`:1088`), `next_meeting_at` (`:1326`) + `meeting_location`/`meeting_time`/`meeting_schedule` (`:1139–1164`), `faq_items` → `GuildFAQItem` (`:1533`), `links` → `GuildLink` (`:1596`), `staff_by_member()` (`:1397`) / `leadership_members()` (`:1417`), `discord_url`, `slug` | `membership/models.py` |
| Absolute hub URL builder | `_absolute_url(path)` (spine convention) + `settings.MEMBER_BASE_URL` | `membership/orientations.py:32`, `classes/emails.py:59`; `plfog/settings.py:74` |
| Hub link targets | `hub_guild_detail` (slug), `hub_tab_detail`, `hub_event_detail` (pk), `billing_setup_payment_method`; `classes:public_class_detail` (slug) | `hub/urls.py:15/223/…`, `billing/urls.py:6`, `classes/urls.py:144` |

**Gaps to close (small):**

1. Four command handlers + their registration payloads (name/description/options).
2. One service function: `orientations.request_custom_orientation(guild, member, starts_at, *, note="")` (the ~6-line glue from `hub/views.py:952`, extracted so both callers can share it — the handler must stay thin).
3. A small Discord-reply formatting module (embed/text builders) local to the Discord app.
4. **Autocomplete** support for `/schedule-orientation`'s slot option *if the foundation exposes an autocomplete
   hook* — see §10 open question 1. Fallback path specified below if it does not.

No new models, no migration.

## 3. Where the code lives

Mirrors the foundation's Discord app layout (the endpoint + router already live there). Assumed package
`core/discord/` (or wherever the foundation places the interactions endpoint — match it exactly):

```
core/discord/                         # (created by the foundation)
    interactions.py                   # foundation: endpoint + router + resolve_member + reply helpers + guild_for_channel
    commands/
        __init__.py                   # registry: name -> handler (foundation wires these in)
        schedule_orientation.py       # NEW — handler + command definition
        whats_on.py                   # NEW
        balance.py                    # NEW
        info.py                       # NEW
    replies.py                        # NEW — small embed/text builders shared by the handlers
    spec/
        commands/
            schedule_orientation_spec.py
            whats_on_spec.py
            balance_spec.py
            info_spec.py

membership/orientations.py            # + request_custom_orientation(...)  (fat-model glue)
```

Each handler is ≤ ~30 lines: resolve member → guard/gate → call the reusable method → build a reply. All the
domain logic stays in `membership`/`billing`/`classes`. Home app for the handlers is the Discord app (foundation-
owned); the only `membership` change is the one service function.

## 4. Data model

**No new models, no migration.** Everything reads existing rows. The only write is `/schedule-orientation`,
which creates an `OrientationBooking` (and, on the custom path, a one-off `OrientationSlot`) through the existing
`request_orientation` / new `request_custom_orientation` service — not directly in the handler.

## 5. Business logic (fat models)

The only new logic is the custom-orientation glue, lifted out of the view into the service layer so the Discord
handler (and, later, the view) call one thing:

```python
# membership/orientations.py
def request_custom_orientation(
    guild: Guild, member: Member, starts_at: datetime, *, note: str = ""
) -> OrientationBooking:
    """Create a one-off MANUAL slot at `starts_at` and request it, reusing request_orientation.

    Guards (raise domain errors, never create a dangling slot):
      - GuildOrientationSettings must exist and be .is_accepting and .allow_custom_requests
        -> OrientationError("This guild isn't taking custom orientation requests right now.")
      - starts_at must be in the future (validated by the caller/form before this point)
    Mirrors hub/views.py:952: ends_at = starts + default_duration_minutes, seats=1,
    location=default_location, source=MANUAL. On OrientationError from booking, delete the slot
    and re-raise (so a failed custom request leaves no orphan slot).
    """
```

- **Guards / duplicate protection** (shared by both paths, enforced before booking): reject when
  `member.is_oriented_for(guild)` (already oriented) or `member.active_orientation_for(guild)` is not None
  (already has an open request). These raise `OrientationError` with member-friendly text; the posted-slot path
  reuses the same checks that `slot.book` already performs, and the handler maps every `OrientationError` to a
  reply.
- **Side effects** are all inside `request_orientation` (unchanged): member confirmation email, leadership
  request email, `SiteActivity.ORIENTATION_REQUESTED`, orienter in-app rows. The handler adds nothing.
- **Domain exceptions, not generic:** `OrientationError` for every orientation failure; the handler catches it
  and renders `str(exc)` into an ephemeral reply.

## 6. UI / UX — the Discord reply experience (completeness checklist applied per command)

The "screen" here is the **Discord reply**. The bar is identical to the web checklist: a member can complete the
task with no dead end, every reply names its next action as an **absolute link**, and every empty/error state is
a friendly message, not a silent failure or a 500. Ephemeral messages are flag `64`; embeds use theme-neutral
colors (Discord embeds render the same in light/dark, so the dark-mode form-control pitfalls do not apply here —
but the "no dead end / absolute link / empty state / error state" discipline fully does).

Shared rules for all four:
- **Member resolution + unlinked handling is delegated to the foundation.** Handlers call `resolve_member`; for
  the two gated commands, `None` → return the foundation's unlinked prompt (ephemeral, with the link-account URL).
  For `/whats-on` and `/info`, `None` is fine — proceed with public content.
- **Absolute links only.** Reuse `CommunityEvent.public_url`, `ClassOffering.public_url`, and
  `_absolute_url(reverse(...))` / `settings.MEMBER_BASE_URL + reverse(...)` for hub pages. Never a bare path.
- **Every reply ends with a way forward** (a link to the relevant hub/book page), so no reply is a dead end.

---

### 6.1 `/schedule-orientation` — request an orientation

**Command definition**
- **name:** `schedule-orientation`
- **description:** "Request an orientation for a guild."
- **options:**
  - `guild` — STRING, **optional**, autocomplete. Which guild. Omitted → auto-detect from the channel via
    `guild_for_channel`. If neither yields a guild → error reply (see below). Autocomplete lists guilds whose
    `GuildOrientationSettings.is_accepting` is true.
  - `slot` — STRING, **optional**, autocomplete. A posted bookable slot; the choice **value is the slot pk**, the
    label is e.g. `"Sat Jul 19 · 2:00 PM · Studio B (3 seats left)"`. Autocomplete queries
    `guild.orientation_slots.bookable()` for the guild already chosen/detected (Discord passes the in-progress
    option values to the autocomplete interaction). Capped at Discord's 25 choices, soonest first.
  - `custom_time` — STRING, **optional**. Free-text proposed time (e.g. "Saturday 2pm") for a time not listed.
    Only honored when the guild's `allow_custom_requests` is true.
  - `note` — STRING, **optional**. Anything the orienter should know; passed straight to `request_orientation(note=…)`.

  Exactly one of `slot` / `custom_time` is expected. Neither → the reply lists bookable slots and explains how to
  pick or propose (disambiguation, below).

**Resolution / gating** — REQUIRES a linked Member (unlinked → foundation prompt). Then resolve the guild
(option → channel map). Load `GuildOrientationSettings`; if missing or not `is_accepting` → "not accepting"
error. Duplicate guards via `active_orientation_for` / `is_oriented_for`.

**Ephemeral?** Yes. **Deferred?** **Yes** — this path sends the member email + fans out leadership
notifications, so the handler `defer(ephemeral=True)` first (ACK type 5, flag 64), does the work, then edits the
deferred message with the result. (If the guards fail *before* any email — e.g. not accepting, already oriented —
prefer an immediate ephemeral error and skip the defer where the foundation allows a fast reject; otherwise the
followup carries the error just as well.)

**Reply format (success)** — ephemeral, e.g.:

> **Orientation requested — {Guild}** ✅
> {Sat Jul 19 · 2:00 PM · Studio B} *(or, custom)* "Proposed: Saturday 2pm — the guild lead will confirm a time."
> Check your email for details — it's not official until a guild lead confirms.
> **View / cancel:** {absolute `hub_guild_detail` URL}

(Mirror the hub wording at `hub/views.py:941` and `:989`.)

**States**
- **Empty / disambiguation** (no `slot`, no `custom_time`): list the guild's bookable slots (up to ~10, soonest
  first) as a tidy list, each with its date/time/location, and tell them to re-run with a slot selected — and, if
  `allow_custom_requests`, that they can pass `custom_time` instead. If there are **zero** bookable slots and
  custom is allowed: "No posted times right now — pass a `custom_time` and the lead will confirm." If zero and
  custom is **not** allowed: "No orientation times are posted yet. Check {guild page URL} for updates." (No dead end.)
- **Error — not accepting:** "{Guild} isn't taking orientation requests right now." + guild page link.
- **Error — already oriented:** "You're already oriented for {Guild}. 🎉" + guild page link.
- **Error — already has an open request:** "You already have an orientation request in for {Guild} — the lead
  will confirm it. See {guild page URL}." (surfaces `active_orientation_for`).
- **Error — guild not specified / not found:** "Which guild? Run this in the guild's channel, or pass `guild:`."
- **Error — custom time given but not allowed / unparseable:** "{Guild} only takes posted times — pick one from
  the list." / "I couldn't read that time — try like `Saturday 2pm`." (Time parsing/validation lives in a Django
  form or the service, not the handler — see §10 open question 2.)
- **Error — booking race (`OrientationError` from `slot.book`, e.g. seat filled):** render `str(exc)` + re-list
  bookable slots so they can pick another. Custom path deletes the orphan slot before replying.

---

### 6.2 `/whats-on` — upcoming events + classes

**Command definition**
- **name:** `whats-on`
- **description:** "See community events and classes coming up in the next 7 days."
- **options:** none. (A `days` override is deferred — see §10.)

**Resolution** — no hard requirement. Resolve the Member opportunistically; unlinked is fine (public content).

**Ephemeral?** Yes (a lookup; keeps the channel clean). **Deferred?** No — pure reads, well under 3s.

**Data assembly** (the recurring-event gotcha handled correctly):
- Window: `frm = today`, `to = today + 7 days` (local dates).
- Events: `CommunityEvent.objects.upcoming().candidates_for_window(frm, to)`, then for each row expand
  `row.occurrences_in(frm, to)` to get **concrete** datetimes (a monthly series carries no single future
  `starts_at`, so filtering on `starts_at` alone would drop it — `occurrences_in` is the source of truth). Flatten
  to `(datetime, title, row.public_url)` and sort by datetime.
- Classes: `ClassSession.objects.upcoming_public()` filtered to the same window → `(starts_at,
  session.class_offering.title, session.class_offering.public_url)`. (Use sessions for concrete dates; a flexible/
  undated class from `ClassOffering.objects.bookable()` has no session date and is **out of the 7-day list** — see
  §10 whether to append a short "also open for signup" tail. Default: keep the 7-day list date-driven only.)

**Reply format** — one ephemeral embed (or markdown) with two short sections, each line a linked title + a
human date:

> **📅 This week at Past Lives**
> **Events**
> • [Monthly Potluck](url) — Sat Jul 19, 6:00 PM
> • [Blacksmithing Guild Meeting](url) — Tue Jul 22, 7:00 PM
> **Classes**
> • [Intro to Lampworking](url) — Wed Jul 23, 5:30 PM

Dates rendered in the site timezone (same tz the calendar uses). Titles link to the absolute event/class page.

**States**
- **Empty:** "Nothing scheduled in the next 7 days." (exact string from the brief) — still ephemeral, no link
  needed but may add "Browse the calendar: {hub calendar URL}" so it isn't a dead end.
- **One section empty:** show only the populated section (omit an "Events" heading with nothing under it).
- **Overflow:** if the combined list is long, cap each section (~8 items) and append "…and more — see the full
  calendar: {URL}" so nothing is silently truncated.
- **Error:** any unexpected failure → ephemeral "Couldn't load the schedule right now — try the calendar: {URL}."

---

### 6.3 `/balance` — the caller's tab balance

**Command definition**
- **name:** `balance`
- **description:** "Check your tab balance and remaining limit."
- **options:** none.

**Resolution / gating** — REQUIRES a linked Member (unlinked → foundation prompt). **Feature gate:**
`SiteConfiguration.load().tab_payments_enabled` — when off, reply (ephemeral): "Tab payments aren't enabled." and
stop (no tab lookup).

**Ephemeral?** Yes (personal money). **Deferred?** No — one `get_or_create` + property reads.

**Data:** `tab, _ = Tab.objects.get_or_create(member=member)` → `current_balance`, `remaining_limit`,
`has_payment_method` (and `can_add_entry` / `is_locked` for the messaging).

**Reply format** — ephemeral, e.g.:

> **💳 Your tab**
> Current balance: **$42.50**
> Remaining before limit: **$107.50**
> Payment method: **Visa on file** *(or)* **None on file**
> **Manage your tab:** {absolute `hub_tab_detail` URL}

**States**
- **Zero balance:** "Current balance: **$0.00** — you're all clear. ✨" + tab link (not a dead blank).
- **No payment method on file** (`has_payment_method` false): add a line "Add a card to keep using your tab:
  {absolute `billing_setup_payment_method` URL}" — the actionable next step, no dead end.
- **Tab locked** (`is_locked`): "Your tab is on hold after a failed payment — update your card: {setup URL}."
- **Feature off:** the gate reply above.
- **Error:** unexpected failure → "Couldn't load your tab right now — see {tab URL}."

---

### 6.4 `/info` — a guild, summarized

**Command definition**
- **name:** `info`
- **description:** "Show a guild's rules, next meeting, FAQ, links, and staff."
- **options:**
  - `guild` — STRING, **optional**, autocomplete (all active guilds). Omitted → auto-detect via
    `guild_for_channel`. Neither → reply listing guilds to choose from (see states).

**Resolution** — no hard requirement (public info). Resolve Member opportunistically; unlinked is fine.

**Ephemeral?** **No — public.** The whole channel benefits. **Deferred?** No — reads only.

**Data / reply format** — one Discord **embed** titled with the guild name, `url` = absolute `hub_guild_detail`,
optional `thumbnail` = banner, with sections (embed fields), each **guarded to show only when set**:
- **About** — `guild.about`, truncated to embed-field limits (Discord field value ≤ 1024 chars; truncate with a
  "…more on the guild page" tail).
- **Essential rules** — `guild.essential_rules` (already the short/printable version — ideal for an embed).
- **Next meeting** — `guild.next_meeting_at` (a date, or None → "TBA") + `meeting_time` + `meeting_location`
  (fallback to the free-text `meeting_schedule` when the structured fields are unset).
- **FAQ** — top N `guild.faq_items` (`GuildFAQItem`) as **Q → short A** (answers truncated; full answers +
  documents live on the page). N ≈ 3 to stay within embed limits.
- **Links** — `guild.links` (`GuildLink`) as a bullet list of `[label](url)`, plus `discord_url` if set.
- **Staff** — from `staff_by_member()` (each person once, with their titles) or `leadership_members()` for a
  simple lead-first name list.
- **Footer / CTA:** "Full guild page → {absolute hub_guild_detail URL}" (embeds' `url` covers the title link; the
  explicit line guarantees a tappable next step).

**States**
- **Guild not specified / not found:** "Which guild? Run this in a guild channel, or pass `guild:`. Guilds:
  {short comma list or the guilds-index URL}." (No dead end.)
- **Guild missing rules:** omit the Rules field; if *both* rules and about are empty, show "This guild hasn't
  filled in its page yet — {guild page URL}."
- **Guild missing FAQ:** omit the FAQ field entirely (no empty "FAQ" heading).
- **No links / no staff:** omit those fields; never render an empty section.
- **Inactive guild:** if `guild.is_active` is false, treat as not-found for members ("That guild isn't active.").
- **Error:** unexpected failure → "Couldn't load {guild} right now — see {guild page URL}."

---

**Mobile / rendering note:** Discord itself handles reflow across desktop/mobile; the only discipline that
carries over is **keep replies short** — embeds with a few fields and truncated bodies read cleanly on a phone.
Long bodies (about/answers) are truncated with a "…more on the page" link rather than dumped.

## 7. Notifications / emails / activity

`/schedule-orientation` fires the **existing** orientation side effects through `request_orientation`
(unchanged): the member's confirmation email, the leadership request email, `SiteActivity.ORIENTATION_REQUESTED`,
and the orienter in-app rows — all via `emit_with_email_shell` with its existing per-booking `period` (already
dedupes; see `membership/orientations.py:172/229`). This spec adds **no** new emails, triggers, or activity
kinds; the Discord reply is the only new "notification," and it is the interaction response, not an `emit`.
The other three commands are read-only and emit nothing.

## 8. Build order (phased; each phase ships green)

> Assumes the foundation spec is built and merged first (endpoint, router, `resolve_member`, unlinked prompt,
> reply/defer helpers, `guild_for_channel`, command registration).

1. **Service glue + replies module.** Add `orientations.request_custom_orientation(...)` (+ its guards/tests) and
   `core/discord/replies.py` builders. Ships green with unit specs; no Discord wiring yet.
2. **`/balance`** — simplest gated read. Handler + registration + specs. (Feature-gate + no-card states.)
3. **`/whats-on`** — the window/occurrence assembly + empty state. Handler + specs.
4. **`/info`** — embed builder, channel→guild detection, per-field guards + empty states. Handler + specs.
5. **`/schedule-orientation`** — deferred flow, both paths, all error/duplicate states, slot autocomplete (or the
   §10 fallback). Handler + specs.
6. **Register commands + housekeeping.** Upsert the four command definitions with Discord (via the foundation's
   registration mechanism / management command). Bump `plfog/version.py` VERSION + a single member-facing
   CHANGELOG entry (grouped as one "Discord commands" feature, folded in with the foundation entry if it sits in
   the same unreleased line).

Each phase runs the full suite + `ruff format`/`ruff check` + `mypy` green before the next.

## 9. Testing

BDD `*_spec.py` in `core/discord/spec/commands/`, `describe_*`/`it_*` (never `context_*`), factory-boy, run in the
`plfog-web` Docker image, ≥98% coverage. Discord interaction payloads are built as fixtures (a small helper that
mints a fake interaction dict with a given `discord_user_id`, channel id, and option values); external Discord
HTTP (followups, autocomplete) is mocked with `respx`. Cases per command:

- **`request_custom_orientation` (service):** creates a MANUAL slot with the right `ends_at`/`seats`/`location`
  and requests it; not-accepting / custom-not-allowed / missing-settings raise `OrientationError`; a booking
  failure deletes the orphan slot and re-raises; duplicate (`active_orientation_for`/`is_oriented_for`) rejects.
- **`/schedule-orientation` handler:** unlinked → foundation prompt; guild from option vs channel map vs neither;
  posted-slot happy path calls `request_orientation` with the right slot; custom path calls
  `request_custom_orientation`; each error/empty/duplicate state renders the right ephemeral text; **defers**
  before doing email work; `OrientationError` surfaced. (Autocomplete handler tested if built — returns ≤25
  bookable slots for the in-progress guild, soonest first.)
- **`/whats-on`:** window math with a fixed `freeze_time`; a **monthly** `CommunityEvent` with a past anchor still
  appears (guards the `occurrences_in` gotcha — a plain `starts_at` filter would drop it); classes from
  `upcoming_public` inside the window; each title links to the correct absolute URL; empty state string exact;
  overflow cap; one-section-empty. **tz/date-window gotcha:** assert local-date boundaries (`frm`/`to`) and site
  timezone rendering.
- **`/balance`:** feature-gate off → the gate reply, no tab lookup; balance/remaining/has-card formatting;
  zero-balance, no-card (setup link), locked; `get_or_create` creates a tab for a member who never had one.
- **`/info`:** guild via option / channel / neither (list); each field present-vs-absent guard (rules, FAQ, links,
  staff, about); TBA next-meeting; truncation of long about/answers; inactive guild → not-found; public (non-
  ephemeral) flag; title `url` is the absolute guild page.

## 10. Open / deferred

1. **Slot-choice rendering in Discord (blocking a design detail, not the build).** Discord `choices` are static at
   command-registration time and **cannot** reflect live DB slots, so posted slots need **autocomplete**, which is a
   separate interaction type (`APPLICATION_COMMAND_AUTOCOMPLETE`). **Does the foundation route autocomplete
   interactions?** If yes, `/schedule-orientation`'s `slot` option uses it (query `bookable()` for the in-progress
   guild). If no, fall back to the **disambiguation reply** (§6.1): the handler lists bookable slots and the member
   re-runs with a choice, or always uses `custom_time`. Recommend the foundation expose an autocomplete hook.
2. **Custom-time parsing.** Where does free-text `custom_time` → `datetime` happen, and how forgiving? Options: a
   dedicated Django form/service (validate future, business-hours) reused from the hub's `OrientationCustomRequestForm`
   pattern, or restrict custom entry to a structured `date`+`time` pair of options. Leaning on the form so validation
   stays out of the handler and error text is consistent with the hub.
3. **`/whats-on` look-ahead window.** Locked at **7 days** per the brief. Deferred: a `days` option (e.g. 1–30) and
   whether to append flexible/undated `bookable()` classes as a short "also open for signup" tail (currently the list
   is strictly date-driven).
4. **`/whats-on` and `/info` channel scoping / personalization.** Deferred: scope `/whats-on` to the channel's guild
   when run in a guild channel (via the same map `/info` uses), and personalize to `for_member(member)` guilds when
   linked. Kept out of v1 for simplicity — v1 shows site-wide/all public content.
5. **Guild option input shape.** `guild` as autocomplete string (slug/pk value) vs a fixed choice list (guild set is
   small and changes rarely, so static choices *are* viable and dodge the autocomplete dependency — but go stale until
   re-registered). Decide alongside open question 1.
6. **Rate limiting / abuse.** Not specified here; assume the foundation's endpoint-level throttling covers it. Flag if
   `/schedule-orientation` needs its own cooldown beyond the duplicate-request guard.

> Spec only — do not build until approved.
