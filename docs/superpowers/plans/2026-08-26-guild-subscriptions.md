# Guild Subscriptions (Kill "Join This Guild") — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-26
**Surface:** FOG hub `pastlives.test` — a new first-login interstitial (`/welcome/guild-updates/`), User Settings (`/settings/?tab=guilds` + `?tab=notifications`), Member Directory (`/members/`), guild detail pages (`/guilds/<slug>/`, both members-hub and public-guilds surfaces), the home onboarding card, the welcome tour, and Discord command copy.
**Related:** `2026-07-03-official-guild-membership.md` (the My Guilds tab this reframes), `2026-07-13-guild-announcement-recipient-list.md` (lead-facing reach count), the notification spine (`core/events/`), the Discord two-way membership sync (`membership/discord_sync.py`).

---

## 1. Summary

"Joining" a guild has always been a confusing half-commitment — members hesitated to click Join because it read like signing up for duties, when all it ever did was subscribe them to that guild's announcements (plus roster + Discord role). This feature renames the concept to what it actually is: **choosing which guilds you want updates from.** On first login, a member picks their guilds from a simple multi-select and can change the picks anytime in Settings. The "Join This Guild" button disappears from guild pages, and the member directory stops displaying anyone's guilds — a subscription is a notification preference, not a public affiliation.

Under the hood nothing structural changes: the same `GuildMembership` rows drive announcements, rosters, member counts, Discord roles, and meeting-proposal rights. This is a **reframe** — new front door, new words, zero data migration.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Underlying model | **Reframe, don't replace.** Keep `GuildMembership` rows; the semantic becomes "subscribed to this guild's updates." Zero data migration of existing rows. Discord role sync, rosters, member counts, and meeting-proposal rights keep keying off the same rows. |
| Join button | **Removed from guild pages entirely**, along with the guilds-surface "Leave this guild" button. Subscription management lives in the first-login prompt + the Settings Guilds tab (the existing My Guilds grid is the base). |
| Directory | **Guild badges AND the guild filter dropdown removed.** Subscriptions are a notification preference now, not a public affiliation — keeping a guild *filter* would leak exactly the data the badges no longer show (filter by guild, read the names). Both go. |
| First-login prompt | Required-ish one-time step for members with **zero subscriptions**: shown after login, skippable ("I'll Pick Later"), and **never shown again** once answered or skipped (persisted on the `Member`). |
| Onboarding checklist | The "Join your guilds" step becomes **"Choose your guild updates"**, pointing at the Settings Guilds tab, and completes on *answering the prompt* (even with zero picks) or holding ≥1 subscription. |
| Discord | **Two-way sync stays.** Reacting in Discord still creates `source=discord` rows (subscribing via Discord); subscribing in-app still assigns Discord roles. Command/reply copy updated from "join" to "follow/get updates" language where cheap; the `/join-guild` command *name* is not renamed (see §10). |
| Terminology | Member-facing noun is **"guild updates"**; verb is **"get updates from"** (with "follow" as a secondary verb). "Join," "member of," and "official" disappear from member-facing guild-subscription copy. |
| Welcome email on subscribe | The `guild_joined` fan-out (guild welcome email when configured + lead in-app notice + activity row) **still fires** on each new subscription — the welcome is the guild saying hi to a new follower, and leads still want to know their audience grew. Copy is reworded to follow-language (§7). |

## 2. What already exists (reuse, don't reinvent)

This is overwhelmingly assembly + copy surgery. Verified in the codebase:

| Need | Existing thing | Location |
|---|---|---|
| The subscription row (unique per guild+member, `source` app/discord) | `GuildMembership` + `GuildMembershipManager.record_app_join` / `record_discord_join` (anti-oscillation: app rows are never demoted) | `membership/models.py:4157-4224` |
| Subscribe/unsubscribe over HTMX with toast (the Settings toggle) | `guild_membership_set` view (204 + toast, fires `member_joined_guild` on created/upgraded, syncs Discord role) | `hub/views.py:1865`; URL `hub_guild_membership_set` `hub/urls.py:333` |
| The per-guild grid data | `build_my_guilds_rows(member)` → `GuildToggleRow(guild, joined, meeting_hint)` | `hub/guild_membership.py`; consumed in `user_settings` (`hub/views.py:1689`, context `my_guilds_rows`) |
| The Guilds settings tab (button, panel, `?tab=guilds` deep link, whitelist) | `user_settings` + `_my_guilds.html` partial + `pl-guild-toggle-row` rows | `hub/views.py:1653` (whitelist already contains `"guilds"`), `templates/hub/user_settings.html:28,651-655`, `templates/hub/partials/_my_guilds.html` |
| Announcement audience (the single seam) | `guild_members` resolver — ACTIVE members with a `GuildMembership` row, not privacy-filtered | `core/events/resolvers.py:242-263`; event `guild_announcement` `core/events/registry.py:495-503` |
| Channel preferences (email/in-app/Discord per event) | Flat `NotificationPreference` (user × event_key × channel) + the settings matrix | `core/models.py:1116-1149`, `core/events/settings_matrix.py` (`build_matrix:343`, `save_matrix:395`), `templates/hub/partials/_notification_matrix.html` |
| Post-login routing hook | `DefaultAccountAdapter.get_login_redirect_url` (members surface → `hub_home`) | `plfog/adapters.py:155` |
| One-time-answer persistence precedent | `Member.onboarding_dismissed_at` + `dismiss_onboarding()` | `membership/models.py:525,746` |
| Onboarding checklist step + gate | `Member._has_joined_guild:661`, guilds step `:703-710`, `is_onboarded:671` | `membership/models.py` |
| Welcome tour copy that says "join" | `member-welcome` tour, Guilds step + Get Started step | `core/tours.py:89-152` (bodies at ~`:116` and ~`:140`) |
| Directory guild surfaces to remove | Filter param + `guilds` context + prefetch (`hub/views.py:288-290,308,326`); dropdown + badges (`templates/hub/member_directory.html:13-19,168-176`) | as listed |
| Guild-detail member-count chip that deep-links the directory guild filter | `guild_detail.html:110` (`?guild={{ guild.pk }}`) | must lose the link (count stays) |
| Join/Leave UI to remove | "Get Involved" card 3-way state `templates/hub/guild_detail.html:288-311`; views `guild_join:1830` / `guild_leave:1849`; URLs `hub/urls.py:141-142` | all removed |
| Join fan-out (welcome email, lead notice, activity) | `orientations.member_joined_guild` (`emit_with_email_shell("guild_joined", …)`, period-deduped `guild:{pk}:join:{member}`) | `membership/orientations.py:485-514` |
| Discord command copy | `/join-guild` (name, description, replies, channel welcome) | `hub/discord_commands.py` (there is **no** `/myguilds`; the scout's mention of it was wrong). `/members` guild line + guild **filter**: `membership/discord_commands.py:641` and `:574` (filter, prefetch `:585`) — disposed of in §10 |
| Guild pulse feed (MUST change — currently leaks follower names) | `_guild_pulse` synthesizes "{name} joined the guild" lines **in view code** from raw `guild.memberships` — no `directory_visible` filter — and renders them to every logged-in member on guild detail | `hub/views.py:407-425` (join line at `:414`); disposal in §6.5 |
| Consumers that keep working untouched (rows stay) | home dashboard `hub/home.py:168-178`, `Member.joined_guilds` `membership/models.py:981-984`, `Guild.roster_members:1785`, meeting proposals `membership/permissions.py:174-202`, Discord sync `membership/discord_sync.py:127-196`, lead reach count `Guild.announcement_recipients:1969-1979` | no behavior change |
| Public guild directory (member counts + hero stat) | `guild_directory` — `member_total=Count("memberships")` per guild card + the aggregate members hero stat | `hub/views.py:440-447`; aggregate counts only, no names — no change needed, listed for completeness |

### Genuine gaps to close (kept minimal)

1. **One `Member` field** — `guild_updates_prompt_answered_at` — so the prompt is truly one-time.
2. **The interstitial page** — view, form, URL, template — plus a 4-line adapter change to route eligible members there after login.
3. **Fat-model subscribe/unsubscribe methods** on `Member`, so the picker, the settings toggle, and any future caller share one code path.
4. **Copy surgery** everywhere "join" appears in member-facing guild-subscription language, and the two directory removals.

## 3. Where the code lives

```
membership/
  models.py                  # + guild_updates_prompt_answered_at field; + Member.needs_guild_updates_prompt,
                             #   subscribe_to_guild, unsubscribe_from_guild, answer_guild_updates_prompt;
                             #   onboarding step rewire (_has_chosen_guild_updates); reworded lead-notice copy hook
  migrations/0xxx_…py        # AddField (auto-reversible)
  orientations.py            # member_joined_guild in-app copy: "now follows" wording
  discord_commands.py        # /members card: no change (see §10)
plfog/adapters.py            # get_login_redirect_url: route eligible members to the prompt
hub/
  views.py                   # + guild_updates_prompt view; guild_membership_set toast copy + answered-stamp;
                             #   user_settings ?tab=guilds answered-stamp; _guild_pulse: remove the join lines;
                             #   REMOVE guild_join, guild_leave; member_directory: remove guild filter/context/prefetch
  urls.py                    # + welcome/guild-updates/ (hub_guild_updates_prompt); REMOVE hub_guild_join/hub_guild_leave
  forms.py                   # + GuildUpdatesPromptForm (validates picked guild pks)
core/
  events/registry.py         # guild_announcement + guild_joined descriptions → follow language
  events/discord_commands.py # guild_disambiguation_reply "You're in: …" → follow language
  tours.py                   # member-welcome copy tweaks
templates/hub/
  guild_updates_prompt.html  (new)   # the interstitial
  partials/_my_guilds.html   # relabel to "Guild Updates", cross-link to Notifications tab
  member_directory.html      # remove guild dropdown + per-card badges
  guild_detail.html          # remove Join/Leave + confirm modal; replacement copy; unlink member-count chip
static/css/hub.css           # + .pl-guild-prompt page layout (reuses .pl-guild-toggle-row rows)
tests/                       # hub/guild_updates_prompt_spec.py (new); updates to my_guilds_spec,
                             #   member_directory_guilds_spec, guild_detail specs, membership onboarding specs,
                             #   adapters spec, discord command copy specs
```

Home apps: `membership` (model layer), `hub` (views/templates), `core` (copy in registry/tours). No new app, all inside the existing coverage/mypy scope.

## 4. Data model

**One new field, no changes to `GuildMembership`, zero data migration of existing rows.**

On `Member` (`membership/models.py`):

| Field | Type | Notes |
|---|---|---|
| `guild_updates_prompt_answered_at` | `DateTimeField(null=True, blank=True)` | `help_text="When the member answered or skipped the first-login guild updates prompt. Null means they have never been asked."` Mirrors the `onboarding_dismissed_at` precedent. |

Migration: a single `AddField`, automatically reversible (drop the column). No data migration — existing members with subscriptions are excluded from the prompt by the *eligibility check* (§5), not by backfilling the stamp, so we don't have to guess when they "answered."

`GuildMembership` keeps its name, fields, manager, and constraint untouched. Renaming the model/related names to "subscription" would churn dozens of call sites and migrations for zero member-visible gain (see §10).

## 5. Business logic (fat models)

All on `Member`; views stay thin.

- **`needs_guild_updates_prompt: bool`** (property) — `self.guild_updates_prompt_answered_at is None and not self.guild_memberships.exists()`. A member who already holds any subscription (legacy join, Discord reaction) has effectively answered and is never prompted; a pre-existing member with zero subscriptions **is** prompted once on their next login — that is the point of the feature. **Edge, closed by the stamp-on-interaction rule below:** a legacy member who unsubscribes from their *last* guild via the settings toggle would otherwise become prompt-eligible again (stamp null + zero rows) and hit the interstitial on next login right after deliberately choosing none — the toggle interaction stamps them, so they don't (§9 pins this).
- **`mark_guild_updates_answered() -> None`** — stamp `guild_updates_prompt_answered_at = timezone.now()` **only if currently null**, `save(update_fields=…)`; no-op otherwise. The shared "this member has made a choice" recorder, called from three places: `answer_guild_updates_prompt` (below), every `guild_membership_set` POST (any subscribe **or** unsubscribe from the settings toggle is an answer), and a `user_settings` GET whose validated `tab` param is `guilds` (the checklist step, the prompt's Skip fallback, and every cross-link reach the tab via `?tab=guilds` deep links, so landing there counts as having seen and chosen). This is a deliberate idempotent write-on-GET — a seen-it stamp in the read-receipt family, not a state mutation a crawler could damage (login-gated, one-way, no-op after the first hit). **Known residual gap, accepted:** switching to the Guilds tab via the client-side Alpine tab button sends no request and doesn't stamp; every path the checklist/onboarding story depends on uses the deep link, and any toggle flip stamps regardless. This closes the onboarding dead end: a member who wants updates from zero guilds completes the "Choose your guild updates" step simply by following its link.
- **`subscribe_to_guild(guild) -> bool`** — the one subscribe path: `GuildMembership.objects.record_app_join(guild, self)`; on `created or upgraded`, fire `orientations.member_joined_guild(guild, self)`; always `discord_roles.on_membership_changed(guild, self, joined=True)` (idempotent role self-heal, same as today's views). Returns whether this was a new/upgraded subscription. Side effects are exactly today's join side effects — no new ones.
- **`unsubscribe_from_guild(guild) -> None`** — `GuildMembership.objects.filter(guild=guild, member=self).delete()` + `discord_roles.on_membership_changed(guild, self, joined=False)`. No other side effects (matches today's leave).
- **`answer_guild_updates_prompt(guilds) -> int`** — loop `subscribe_to_guild` over the picked guilds (may be empty — that IS the Skip case), stamp `guild_updates_prompt_answered_at = timezone.now()`, `save(update_fields=…)`, return the count subscribed. Idempotent to re-call (subscribe is get_or_create-based; the stamp just refreshes).
- **Onboarding rewire** — rename `_has_joined_guild` to **`_has_chosen_guild_updates`**: `self.guild_updates_prompt_answered_at is not None or self.joined_guilds.exists()`. `is_onboarded` and the checklist step use it; the step's label becomes "Choose your guild updates" (URL stays `?tab=guilds`). Rationale: subscribing to *nothing* is now a legitimate, deliberate answer — a member must not be permanently un-onboardable for making it.
- **Refactor existing callers onto the new methods:** `guild_membership_set` (`hub/views.py:1865`) calls `subscribe_to_guild`/`unsubscribe_from_guild` instead of inlining the three lines, **and calls `mark_guild_updates_answered()` on every hit**. `user_settings` calls it when the validated tab is `guilds` and a member is present. `hub/discord_commands.py` `_join_guild` keeps its own sequence (it interleaves the public channel welcome with `created`) — copy-only changes there.
- **`_guild_pulse` (`hub/views.py:407-425`): remove the join lines.** The pulse currently synthesizes `{display_name} joined the guild` entries in view code (`:414`) from **raw** `guild.memberships` — no `directory_visible` filter — and shows them to every logged-in member on the guild page. That broadcasts each new follower by name: the exact affiliation leak §6.4 removes the directory filter over, and worse than the roster (which IS privacy-filtered). Filter-plus-reword was considered and rejected — even privacy-filtered, a "now follows" line still publishes a notification preference as social content. **Remove the membership loop entirely**; the pulse keeps its announcement and new-class lines and the same merge/sort/limit.
- **Adapter** (`plfog/adapters.py:155`): in `get_login_redirect_url`, members surface only — look up the member (same `Member.objects.filter(user=…)` pattern `_get_member` uses); if `member is not None and member.needs_guild_updates_prompt`, return `reverse("hub_guild_updates_prompt")`, else `hub_home` as today. **Known, accepted gap:** allauth only consults `get_login_redirect_url` when no `?next=` is present, so a member arriving via a deep link (e.g. an email CTA) skips the prompt that session and gets it on their next plain login. The onboarding checklist step is the backstop. No middleware gate — a gate on every request is disproportionate for a skippable prompt (YAGNI).
- **View `guild_updates_prompt`** (`hub/views.py`, `@login_required`): GET — if not eligible (`member is None` or not `needs_guild_updates_prompt`), redirect to `hub_home` (the page is one-time; a bookmark never resurrects it); else render. If **zero active guilds exist**, stamp-and-redirect immediately (never trap a member on an empty picker). POST — `GuildUpdatesPromptForm`; on the Skip button, call `answer_guild_updates_prompt([])`; on Save, `answer_guild_updates_prompt(form.cleaned_data["guilds"])`; redirect to `hub_home` with a Django success/info message (full-page form → messages, not toast).
- **Form `GuildUpdatesPromptForm`** (`hub/forms.py`): `guilds = ModelMultipleChoiceField(queryset=Guild.objects.filter(is_active=True), required=False, widget=MultipleHiddenInput-equivalent)` — validation only (an inactive/bogus pk fails with "Pick guilds from the list."); the template renders the rows itself (service-built grid, same as the matrix and My Guilds — this is the established exemption to form_field.html for hand-rendered toggle grids).
- **Directory view** (`member_directory`, `hub/views.py:270-326`): delete the `guild_filter` param handling, the `guild_memberships__guild` prefetch, and the `guilds`/`guild_filter` context keys. A stale bookmarked `?guild=NN` URL simply ignores the param and shows the full directory — no error, no filter.
- **Views removed:** `guild_join`, `guild_leave` + URLs `hub_guild_join`/`hub_guild_leave` (`hub/urls.py:141-142`). Nothing else posts to them once the template branches go (verified: the Discord command has its own inline flow).

## 6. UI / UX

Seven screens. Copy is member-facing ELI14: plain, short. **No em dashes or hyphens-as-dashes in any copy-ready string below.**

### 6.1 First-login prompt — `templates/hub/guild_updates_prompt.html` (new)

- **Layout & container:** a **dedicated page** (multi-select over ~14 guilds is far past the 1-3 field modal bar; FRONTEND interaction table → dedicated page). Extends `hub/base.html` so the member sees the real hub chrome on day one — the prompt is a welcome, not a wall. One centered `hub-card` (max-width ~640px via a new `.pl-guild-prompt` class in `hub.css`).
- **Header (Title Case):** `Which Guilds Do You Want Updates From?`
  Explainer under it (muted): `Guilds are the craft groups that run each studio. Pick the ones you want announcements from. Following a guild also puts you on its roster and gives you its Discord role. You can change your picks anytime in Settings.`
- **Components used:** `hub-card`; the existing `.pl-guild-toggle-list` / `.pl-guild-toggle-row` rows (guild name + one-line `meeting_hint` truncated at 60 chars, `pl-toggle pl-toggle--sm` on the right) — same rows the member will meet later in Settings, so the two surfaces teach each other. Rows are built from `build_my_guilds_rows(member)` (all toggles start off for the zero-subscription audience this page serves). Toggles here are **plain checkboxes in the form** (`name="guilds" value="{{ row.guild.pk }}"`) — no HTMX, one batch Save, because a first-run picker must feel like one decision, not fourteen instant mutations firing welcome emails while the member is still reading.
- **The controls, named explicitly:**
  - **Save** — `pl-btn pl-btn--primary`, the **last element in the form** (Rule 21), labeled exactly `Save`. Submits the whole selection; view redirects to `hub_home` with `messages.success`: `You'll get updates from 3 guilds. Change your picks anytime in Settings.` (count pluralized; zero picked via Save reads `You didn't pick any guilds. You can choose some anytime in Settings.` as an info message).
  - **I'll Pick Later** — `pl-btn pl-btn--secondary`, a submit button `name="skip" value="1"` in the same form (skipping mutates state, so it must POST, not be a GET link). Redirects to `hub_home` with `messages.info`: `No problem. You can pick guilds anytime in Settings.` Sits beside Save in a button row with `1.5rem` top margin clearing the last guild row (Rule 18). **Skip semantics, stated:** Skip discards any boxes already checked — the server ignores `guilds` when `skip` is present — and the prompt never returns, so a checked-then-skipped selection is gone for good. To keep that from ever surprising anyone, the page carries a small Alpine scope (`x-data="{ picked: 0 }"`, each checkbox `@change` adjusting the count) and the Skip button is **`:disabled="picked > 0"`**: the moment you've checked a guild, Save is the only way forward (uncheck everything to re-enable Skip). Label stays `I'll Pick Later`.
  - No "+ Add" / per-row Delete — the guild set is admin-fixed; the member only flips rows. Named so a reviewer doesn't flag the list-editor checklist; it doesn't apply.
- **States:**
  - **Normal:** all rows off, Save + I'll Pick Later both enabled.
  - **Some guilds checked:** I'll Pick Later disabled (see Skip semantics above); Save is the primary path.
  - **Empty (zero active guilds):** never rendered — the view stamps and redirects to home (§5). Named here so the trap is visibly closed.
  - **Loading:** standard full-page POST; both buttons are plain submits (browser handles double-submit adequately for an idempotent get_or_create path; no spinner needed).
  - **Error:** a tampered/inactive guild pk fails form validation → page re-renders with the message `Pick guilds from the list.` above the rows and the member's checks preserved. Real members can't reach this state from the UI.
  - **Not eligible / revisit:** GET redirects to `hub_home` silently. **Unlinked account** (`member is None`): never routed here by the adapter; a direct GET redirects home with the standard `Your account is not linked to a membership.` info message.
  - **No dead ends:** Skip is always one tap away; the page never blocks logout or navigation (sidebar is present).
- **Dark + light:** only themed components (`hub-card`, `pl-toggle`, `pl-btn`, muted text tokens). No raw inputs/selects/textareas, so the white-box class of bug can't occur; no date/time pickers. `.pl-guild-prompt` sets layout only (max-width, margin), no colors. **Verify both Obsidian and Slate**, including toggle on/off and the muted hint line.
- **Mobile:** rows are the existing flex rows (name wraps left, toggle pinned right), single column, no table, no fixed widths. Button row wraps to stacked full-width buttons under ~480px. 8px grid throughout.

### 6.2 Settings — Guilds tab (`templates/hub/partials/_my_guilds.html`, reframed copy)

The existing grid **is** the per-guild subscription control; mechanics (per-toggle HTMX auto-save, 204 + toast, error revert, in-flight row dim) are already built and stay byte-identical. Changes are copy + one cross-link:

- **Heading:** `My Guilds` → `Guild Updates`. (The tab button stays `Guilds` and the tab id stays `guilds` — `?tab=guilds` deep links already live in the drive email, the onboarding checklist, and guild pages; breaking them buys nothing.)
- **Explainer:** `Choose which guilds you get announcements from. Flip one on to follow it, off to stop. Changes save instantly. Following a guild also puts you on its roster and gives you its Discord role.`
- **Cross-link (the WHICH × HOW mental model, stated on both surfaces):** a muted line under the grid: `How updates reach you (email, in app, Discord) is set on the Notifications tab.` where "Notifications tab" is a link that flips the Alpine tab (`@click="tab = 'notifications'"` style anchor, matching the existing tab-switcher; fallback `href="?tab=notifications"`).
- **Toasts (in `guild_membership_set`):**
  - on: `You'll get updates from {guild.name}.` (success)
  - off: `You won't get updates from {guild.name} anymore. Turn them back on anytime.` (info)
- **Answered-stamp (invisible but load-bearing):** landing on this tab via a `?tab=guilds` deep link, or flipping any toggle, stamps `guild_updates_prompt_answered_at` (§5) — no visible UI, but it's what lets the onboarding step and the prompt treat "visited and chose nothing" as an answer.
- **States:** unchanged from the shipped feature — empty-guilds message, unlinked-account message, in-flight dim, error revert + error toast — re-verified after the copy edit, not redesigned.
- **Dark + light / mobile:** unchanged, re-verify both themes after edits.

**Deliberately NOT built:** per-guild `NotificationPreference` scoping (a guild × channel matrix). The `GuildMembership` row is the per-guild switch (WHICH guilds); the flat `guild_announcement` matrix row is the channel switch (HOW they arrive). Two orthogonal single-purpose controls, cross-linked. Inventing per-object preference rows would fork the resolver seam and the settings matrix for a need nobody has expressed.

### 6.3 Settings — Notifications tab (`_notification_matrix.html` via registry copy)

- The matrix row for `guild_announcement` (`core/events/registry.py:495-503`) gets its description changed to: `A guild you follow posted an announcement. Pick which guilds in your hub Settings.` — the second sentence is the cross-link back (the matrix renders descriptions as row subtext, so this lands with zero template work). The wording is deliberately **surface-neutral**: this same description renders on the no-login token email-prefs page (`hub/views.py:1532`), where there is no Guilds tab to point at — "in your hub Settings" is true from both surfaces. Label stays `Guild announcement`.
- The `guild_joined` event's registry label/description likewise moves to follow language (e.g. description `Someone new is following your guild.` — it's the lead-facing notice). Anchor by event key, not line number.
- No structural change to the matrix, `save_matrix`, or the token no-login variant (`hub/views.py:1532`) — they render registry copy and inherit the fix.
- **States / themes / mobile:** unchanged surfaces; verify the new description wraps cleanly in the matrix row on mobile and in both themes (it's plain text in an existing cell — low risk, still check).

### 6.4 Member Directory (`templates/hub/member_directory.html` + view)

- **Remove the per-card guild badges** (`:168-176`, the `directory-card__guilds` block) and **remove the Guild filter dropdown** (`:13-19`). The Skill filter, commissions filter, and search keep their layout; `.pl-directory-filters` simply has one fewer `pl-filter-field` (flex row, nothing else moves).
- **Why both, stated for the record:** if badges went but the filter stayed, anyone could enumerate a guild's followers by filtering and reading names — the filter *is* the leak. Subscriptions are a notification preference, not a public affiliation.
- **View:** drop the `guild` GET param handling, the `guild_memberships__guild` prefetch, and the `guilds`/`guild_filter` context (§5). Stale `?guild=` URLs degrade to the unfiltered directory.
- **States:** the directory's existing empty/search states are untouched; there is no new state. The page header's description already says members control their own visibility from Settings — unchanged.
- **Dark + light / mobile:** removals only; re-verify the filter row still wraps correctly on mobile with three fields instead of four.
- Note: guild **rosters on guild pages** (`Guild.roster_members`, directory-privacy filtered) are intentionally untouched — the roster is the guild-scoped surface and keeps working off the same rows (locked decision). The directory is the site-wide surface being de-affiliated.

### 6.5 Guild detail page (`templates/hub/guild_detail.html`, both surfaces)

- **"Get Involved" card (`:288-311`):** remove the Join form, the guilds-surface "Leave this guild" button, and its `confirm_modal` include. The card keeps its other actions (Join an Orientation, Teach a Class, email the lead, guilds-surface shop link). New top-of-card state, per auth + subscription:
  - **Subscribed member:** keep the existing checkmark line, reworded: `You get this guild's updates` + the existing `Manage in Settings` secondary button (`?tab=guilds`). (Kept: it's a manage-elsewhere pointer, not a join button.)
  - **Signed-in, not subscribed:** a muted **text line, deliberately not a button** (an in-place "Get updates" button is explicitly out — no join-style affordance on guild pages): `Want announcements from this guild? Choose your guild updates in Settings.` with "Settings" linking `{% url 'hub_user_settings' %}?tab=guilds`.
  - **Anonymous (public guilds surface):** the "Log in to join" button becomes `Log in to the members hub` (`pl-btn pl-btn--primary`, same `?next=` carry) — it's a login button, not a subscribe button, so it stays a button.
  - **Unlinked account:** falls into no branch (member is None and authenticated) — show nothing extra; the card's other actions remain. Named so the template's `{% elif %}` chain is written to handle it, not fall through oddly.
- **Member-count stat chip (`:110`):** the count **stays**, the link to the guild-filtered directory **goes** (that filter no longer exists). Both surfaces now render the plain `hub-badge` span, identical to the guilds-surface branch today. Remove the now-dead `title` attribute with it.
- **Guild pulse (rendered on this page):** the "{name} joined the guild" lines disappear per §5 — the pulse shows only announcements and new classes. No template change needed (the template renders whatever `_guild_pulse` returns); the pulse's existing empty state ("nothing yet" or the section simply shrinking) already handles a guild whose only recent activity was joins — verify a joins-only guild renders the pulse section gracefully rather than an awkward gap.
- **States:** the card has no forms left beyond orientation/mail links — no new empty/loading/error states introduced; every removed control's state goes with it.
- **Dark + light / mobile:** existing card, existing tokens; verify the new muted line + link render in both themes and that the card's vertical rhythm holds after the removals (buttons keep their `0.5rem` gap column).

### 6.6 Home onboarding card (`membership/models.py` checklist)

- Step label: `Join your guilds` → `Choose your guild updates`; URL unchanged (`?tab=guilds`); done-state now `_has_chosen_guild_updates` (§5), so answering the prompt (even with zero picks) checks it off. Crucially, the step's own link now **completes the step**: the `?tab=guilds` deep link stamps the answered field on arrival (§5), so a member who deliberately wants zero guilds finishes this step from the exact page it points at — no dead end, and the `?next=` prompt-bypass backstop actually backstops. Hint stays empty.
- The card's own layout, dismiss behavior, and other steps are untouched. States/themes/mobile: inherited from the existing card; no new work beyond the label.

### 6.7 Welcome tour (`core/tours.py:89-152`)

- Guilds step body → `Guilds are the craft groups that run each studio. Follow the ones you want updates from, then book an orientation to get working in the space.`
- Get Started step body → `A short checklist to finish setting up including your profile, photo, and guild updates. It disappears on its own when you're done.`
- Pure copy; the tour engine, targets, and ordering are untouched.

## 7. Notifications / emails / activity

**No new events, no new emails, no new activity kinds, no resolver changes.** The `guild_members` resolver (`core/events/resolvers.py:242-263`) remains the single seam turning rows into announcement recipients — untouched.

Copy-only changes on existing fan-outs:

- `member_joined_guild` (`membership/orientations.py:485-514`): the lead-facing in-app notice becomes title `New follower` body `{member} now follows {guild}.` The guild welcome email keeps sending exactly as configured (lead-authored body, `emit_with_email_shell`, period `guild:{pk}:join:{member}` dedupe intact — a skip-then-subscribe or toggle-spam still can't double-send). The static welcome-email template framing (`membership/emails/guild_welcome.{html,txt}` — keep `.txt` and `.html` in sync) is swept for "joined" phrasing in the same pass.
- Registry descriptions for `guild_announcement` and `guild_joined` (§6.3).
- Lead-facing reach line on the guild-edit Announcements tab (`templates/hub/guild_edit.html:582`): the actual string is `{{ announcement_recipient_count }} member(s) is/are on your list automatically.` — reword `member` to `follower` (`N follower(s) is/are on your list automatically.`), and change the collapsible's `Show members` / `Hide members` button labels to `Show followers` / `Hide followers`. Template copy only; `Guild.announcement_recipients` untouched.
- **Accepted behavior, named:** a first-login member picking N guilds triggers N subscribe fan-outs — up to N welcome emails (only guilds that configured one), N lead notices, N best-effort Discord role calls. This is identical to a member clicking Join N times today; volumes (~14 guilds max) are trivial and the role calls are already best-effort.

`SiteActivity.GUILD_JOINED` keeps its kind key (data continuity). The one member-facing "joined the guild" display string was never in a template — it is synthesized in `_guild_pulse` view code (`hub/views.py:414`) and is **removed outright** per §5, not reworded. Two "joined" survivors are **accepted as-is** so the Phase 6 implementer doesn't chase them: the `GUILD_JOINED` `TextChoices` label `"Joined a guild"` (`core/models.py:999` — staff-facing activity feed, not member copy), and historical notification/activity **bodies already stored in the DB**, which naturally keep their original wording (we never rewrite stored rows).

## 8. Build order (phased; each phase ships green — full suite + ruff + `manage.py check`; local mypy is known-broken, CI runs the real check)

1. **Model layer.** `guild_updates_prompt_answered_at` field + migration; `needs_guild_updates_prompt`, `mark_guild_updates_answered`, `subscribe_to_guild`, `unsubscribe_from_guild`, `answer_guild_updates_prompt`; onboarding rewire to `_has_chosen_guild_updates` + step relabel; refactor `guild_membership_set` onto the new methods **and add its answered-stamp**, plus the `user_settings` `?tab=guilds` stamp (behavior otherwise identical, toasts still old copy). Specs for all of it, including the leave-last-guild edge.
2. **First-login prompt.** `GuildUpdatesPromptForm`, `guild_updates_prompt` view + URL, `guild_updates_prompt.html`, `.pl-guild-prompt` CSS, adapter redirect. Specs: eligibility routing, save/skip/tamper/zero-guilds/unlinked paths, template states. Verify both themes + mobile.
3. **Settings reframe.** `_my_guilds.html` relabel + cross-link; new toasts in `guild_membership_set`; registry description updates (`guild_announcement`, `guild_joined`); notifications-tab cross-link sentence. Update `my_guilds_spec` and matrix-copy assertions (beware `Changelog renders everywhere` — pick assertion strings that can't collide with changelog text on hub pages).
4. **Directory de-affiliation.** Remove filter + badges + view param/context/prefetch; rewrite `member_directory_guilds_spec.py` to assert absence (no dropdown, no badges, `?guild=` ignored).
5. **Guild page.** Remove Join/Leave branches + confirm modal; add the three replacement states; unlink the member-count chip; **remove the join lines from `_guild_pulse`** (`hub/views.py:414`) and update its specs; delete `guild_join`/`guild_leave` views, URLs, and their specs; keep `is_member_of_guild` context.
6. **Peripheral copy sweep.** Tours; `/join-guild` description/replies (`Follow a Past Lives guild to get its updates.` etc.) and channel-welcome phrasing; `guild_disambiguation_reply` (`core/events/discord_commands.py:197`) `You're in: …` → `You follow: …`; `member_joined_guild` notice + welcome-email template phrasing; Announcements-tab reach line + Show/Hide followers labels. Grep-driven, with membership-phrasing terms a bare "join" grep misses: `grep -rniE "join|you're in|member of" templates/ hub/ membership/ core/` scoped to guild-subscription copy.
7. **Housekeeping.** Bump `plfog/version.py` `VERSION` `1.6.1` → `1.7.0` (cross-cutting member-facing change → minor). **One** member-friendly changelog entry (below). The Discord announce fires automatically when VERSION changes on main — curate the entry before merging.

Changelog entry (single entry for the whole feature; no dashes; version `1.7.0`):

> **Guild updates, your way.** Guilds are now something you follow, not join. The first time you sign in we ask which guilds you want updates from, and you can change your picks anytime in Settings under Guilds. The member directory no longer shows anyone's guilds. Who you follow is your notification choice, not a public label.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — it silently collects nothing), factory-boy, 100% coverage gate, run in the canonical env (pytest with minimal `DATABASE_URL`, never sourcing `.env`). Capture pytest's own exit code, never a pipe's.

**`tests/membership/` (model layer):**
- `describe_needs_guild_updates_prompt` — `it_is_true_with_no_stamp_and_no_subscriptions`; `it_is_false_once_stamped`; `it_is_false_when_any_subscription_exists` (including a `source=discord` row — a Discord reactor is never prompted); `it_is_false_again_after_unsubscribing_everything_post_answer` (stamp wins).
- `describe_subscribe_to_guild` — creates an app row; fires `member_joined_guild` on created **and** on discord→app upgrade; does not re-fire when already app-sourced; always calls `discord_roles.on_membership_changed` (mock the Discord client, never the models).
- `describe_unsubscribe_from_guild` — deletes the row, fires the role removal, idempotent when absent.
- `describe_answer_guild_updates_prompt` — subscribes each pick, stamps, returns count; empty list stamps with zero rows; re-answering doesn't duplicate rows (unique constraint honored via get_or_create).
- `describe_mark_guild_updates_answered` — stamps when null; never overwrites an existing stamp; **the leave-last-guild edge:** a legacy member (stamp null, one row) who unsubscribes their last guild via `guild_membership_set` ends up stamped, so `needs_guild_updates_prompt` stays False and the interstitial never resurrects on their next login.
- `describe_onboarding` — guilds step done when stamped-with-zero OR ≥1 row; label reads "Choose your guild updates"; `is_onboarded` follows; a GET of `/settings/?tab=guilds` completes the step for a zero-subscription member (the dead-end regression test).
- Pin the dedupe: skip, later subscribe → welcome email sends once; unsubscribe/resubscribe → not resent (`period` guard).

**`tests/hub/guild_updates_prompt_spec.py` (new):**
- View: eligible GET renders rows (one per active guild, toggles unchecked); ineligible GET redirects home; anonymous → login; unlinked → home + info message; zero-active-guilds GET stamps + redirects.
- POST Save with picks → rows created, stamp set, redirect home, success message with count; Save with none → info message; Skip → stamp, no rows, info message; tampered inactive-guild pk → form error `Pick guilds from the list.`, nothing stamped.
- Adapter: login with zero subscriptions + no stamp lands on the prompt; with a subscription or a stamp lands on `hub_home`; public surface unaffected; a `?next=` login bypasses (documents the accepted gap).
- POST Skip **with guilds also checked** → picks discarded, stamp set, zero rows (pins the discard semantics even though the UI disables Skip once picked).
- Template states asserted on parsed HTML (checkbox `name="guilds"`, Save last-in-form, Skip present and carrying the `:disabled="picked > 0"` binding) — not just status codes.

**`tests/hub/` updates:**
- `my_guilds_spec.py` — new heading/explainer/cross-link strings; new toast copy on set/unset (choose assertion substrings that cannot collide with changelog text rendered on hub pages).
- `member_directory_guilds_spec.py` — inverted: no Guild `<select>`, no `directory-card__guilds` markup, `?guild=NN` returns the unfiltered member set, `guilds` absent from context.
- Guild-detail specs — Join form and Leave button/confirm modal absent on both surfaces; subscribed line + Manage in Settings for subscribers; the muted Settings line for non-subscribers; `Log in to the members hub` for anonymous on the guilds surface; member-count chip is a `<span>`, not an `<a>`.
- `_guild_pulse` specs — no membership-derived items: a guild with recent joins and no announcements/classes yields no join lines (and no member names anywhere in the pulse); announcements + classes still merge newest-first under the limit.
- `guild_membership_set` / `user_settings` — every toggle POST stamps a null `guild_updates_prompt_answered_at`; `?tab=guilds` GET stamps (member present) and other tabs don't; the token no-login prefs page renders the new surface-neutral `guild_announcement` description without error.
- URL specs — `hub_guild_join`/`hub_guild_leave` no longer resolve; `hub_guild_membership_set` unchanged.
- Discord command copy specs — `/join-guild` description/replies use follow language; command name unchanged.

No tz/date-window gotchas (the stamp is a plain `timezone.now()` with no windowing).

## 10. Open / deferred

- **`/join-guild` command rename** (to `/follow-guild`) — deferred. Renaming re-registers the command and breaks member muscle memory for a word; description/reply copy carries the reframe. Revisit if members find the name jarring.
- **`/members` Discord guild surfaces — the per-card guild line (`membership/discord_commands.py:641`) AND the guild filter (`:574`, filter `guild_memberships__guild`, prefetch `:585`)** — both left as-is, deliberately. The filter is the same follower-enumeration mechanism the web directory filter is being removed for, so it needs an explicit disposition: on Discord, guild affiliation is **already public by design** — the two-way sync (a locked keep) assigns a visible guild role to every subscriber, and anyone in the server can read a role's full member list natively. The `/members` filter reveals a strict subset of that (it additionally applies `directory_visible`, so it shows *less* than the role list does). Removing it would be privacy theater while the roles stand. The web directory has no such pre-existing exposure, which is why the same mechanism dies there but lives here. Flag for Jo: if the Discord roles themselves ever stop being public, both the filter and the card line should be removed in the same pass.
- **Renaming `GuildMembership`/related names to "subscription"** — no. Model rename + migration churn across dozens of call sites for zero member-visible change violates tend-don't-churn.
- **Middleware gate forcing the prompt** — no. Adapter-only routing accepts the `?next=` bypass (§5); the checklist step is the backstop.
- **Per-guild channel granularity** ("email from Woodshop, Discord-only from Textiles") — explicitly out. The WHICH × HOW split (§6.2) covers the expressed need without per-object `NotificationPreference` scoping.
- **Re-prompting members who answered long ago / "review your guilds" nudges** — out; the Settings tab is always there.
- **Guild-roster privacy changes** — out; rosters stay guild-scoped and directory-privacy filtered as today.
