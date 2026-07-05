# Official Guild Membership (My Guilds) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-03
**Base:** release-0.20.x (v0.20.1) — the file line numbers below are release-0.19.x-approximate; anchor by **symbol name** when building on 0.20.x. The reused symbols (`user_settings`, `guild_join`/`guild_leave`, `GuildMembership`, the `active_tab` whitelist, the notification-matrix toggle pattern) are stable across both branches; only line numbers shift.
**Surface:** FOG hub `pastlives.test` — User Settings (`/settings/`), a small touch on the guild page (`/guilds/<slug>/`), and the existing admin Announcements composer (Site Settings → Announcements).
**Related:** `2026-06-21-guild-orientations.md` (guild join → welcome-email side effect), the notification spine (`core/events/`), `reference_sitewide_announcements_composer.md`.

---

## 1. Summary

A member can now decide, from one place in Settings, which guilds they officially belong to. Being "official" in a guild means exactly one thing to the member: **you receive that guild's announcement emails** (and you appear on its roster). The new **Guilds** tab shows every active guild as a simple on/off switch, pre-checked for the guilds you're already in; flipping one on joins you, flipping one off leaves you. This also fills a real gap — until now there was **no way to leave a guild** from the UI at all.

To kick it off, an admin sends a **one-time nudge email to all members** — composed and sent from the *existing* Site Settings → Announcements composer — with a button that drops the member straight onto the new Guilds tab.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| What "official membership" is stored as | **The existing `GuildMembership` row — no new flag/field.** A `GuildMembership` already subscribes you to guild announcement emails (the `guild_members` resolver). "Official" *is* that row; the toggle is join/leave. |
| How the all-members email goes out | **Manual admin send from the existing Announcements composer** (`site_announcement` event). No new blast pipeline, no new email template files. The email carries a CTA link to the Guilds tab. |
| Save behavior for the toggle grid | **Auto-save per toggle over HTMX** (join/leave fires instantly, returns a toast). Chosen over a batch Save button — see §6 rationale. |
| Guild list UI | A **plain toggle list** of all active guilds (~14). No search, no filter, no pagination. |
| Where it lives in Settings | A **new "Guilds" tab** on the existing tabbed settings page — not a section inside the Profile form. See §6. |
| Confirm on leave? | **No confirm modal.** Leaving is one-click reversible and low-stakes; the success toast names the consequence. See §6 rationale. |

## 2. What already exists (reuse, don't reinvent)

This ships as assembly. Almost nothing is net-new.

| Need | Existing thing | Location |
|---|---|---|
| Join a guild (idempotent `get_or_create` + welcome-email/lead-notify side effect) | `guild_join` view + `orientations.member_joined_guild()` | `hub/views.py:1438`, `membership/orientations.py:384` |
| Leave a guild (`filter().delete()`) | `guild_leave` view | `hub/views.py:1455` (URLs `hub_guild_join` / `hub_guild_leave`, `hub/urls.py:63-64`) |
| "Official membership = you get guild announcement emails" | `guild_members` resolver (roster → recipients, **not** last_login-gated) | `core/events/resolvers.py:149`; wired to `GUILD_MEMBERS` / `guild_announcement` event `core/events/registry.py:390` |
| The member's joined set | `GuildMembership` (guild + member, unique) via `member.guild_memberships` | `membership/models.py:1436` |
| All active guilds | `Guild.objects` (soft-delete-aware manager), `is_active` | `membership/models.py:820` |
| Tabbed settings page + `form_id` dispatch + `?tab=` deep-link + tab whitelist | `user_settings` view + `user_settings.html` (Alpine tab switcher) | `hub/views.py:1260`, `templates/hub/user_settings.html:7` |
| Toggle markup for a service-built grid (not a Django form field) | `pl-toggle pl-toggle--sm` hand-rendered, exactly as the notification matrix does | `templates/hub/_notifications_settings.html:68`; CSS in `static/css/components.css` |
| Service that builds grid rows for the template (build/save shape to mirror) | `settings_matrix.build_matrix` / `save_matrix` | `core/events/settings_matrix.py:170` |
| HTMX success feedback | `trigger_toast()` → 204 | `hub/toast.py` (per FRONTEND.md) |
| All-members email (composer, preview, send) | Announcements tab: `admin_site_settings`, `_send_site_announcement`, `_announcement_email_html`, `SiteAnnouncementForm` | `hub/views.py:3209 / 3120 / 3083`, `hub/forms.py:876`, `templates/hub/admin/site_settings.html:371` |
| Broadcast audience for the nudge email | `Recipients.ALL_ACTIVE_MEMBERS` (last_login-gated) | `core/events/resolvers.py:320` |
| Absolute URLs for the email CTA | `MEMBER_BASE_URL` (composer already calls `request.build_absolute_uri`) | `hub/views.py:3136`; helper precedent `membership/orientations.py:32` |

### Genuine gaps to close (kept minimal)

1. **A "Guilds" settings tab** on `user_settings.html` (button + panel) and `"guilds"` added to the `active_tab` whitelist in `user_settings` (`hub/views.py:1310`).
2. **A tiny read helper + grid data** — "all active guilds, each flagged joined-or-not for this member" — mirroring `build_matrix`'s shape (a service function, not view logic).
3. **One thin HTMX endpoint** — `guild_membership_set(pk)` — that joins-or-leaves and returns 204 + toast (the existing `guild_join`/`guild_leave` views redirect full-page; the toggle needs a toast). It runs the *same* logic those two views already run.
4. **One small CSS layout class** (`pl-guild-toggle-*`) for the row layout. Reuses existing `pl-toggle` for the switch itself.

## 3. Where the code lives

```
hub/
  views.py                      # + guild_membership_set (thin HTMX join/leave→toast);
                                #   add "guilds" to user_settings active_tab whitelist + context
  urls.py                       # + path settings/guilds/<int:pk>/  name="hub_guild_membership_set"
  guild_membership.py  (new)    # build_my_guilds_rows(member) — the grid-data service
                                #   (mirror of settings_matrix.build_matrix; logic out of the view)
templates/hub/
  user_settings.html            # + "Guilds" tab button + panel that includes the partial
  partials/_my_guilds.html (new)# the toggle grid (hand-rendered pl-toggle rows, per-row HTMX)
static/css/hub.css              # + .pl-guild-toggle-row / list layout (reflow, 8px grid)
templates/hub/guild_detail.html # small: when already a member, show "✓ In this guild ·
                                #   Manage in Settings" — add the missing already-member branch
                                #   in the "Get Involved" panel (~lines 230–235)
```

Home app: `hub` (member-facing, no models — reads `membership`). No new models, no migration. If the shared 3-line join/leave logic is extracted to `Member.join_guild()/leave_guild()` model methods (recommended, fat-models), that is a small **model-layer** change owned by the model layer — this spec does not require it to ship; the view can mirror the existing three lines.

## 4. Data model

**No new model, no new field, no migration.** This is the whole point of the locked decision: official membership is the existing `GuildMembership` row (`membership/models.py:1436` — `guild` + `member` + `joined_at`, unique on `(guild, member)`). Joining = create the row; leaving = delete it. The `guild_members` resolver already turns those rows into the audience for a guild's announcement emails, so no wiring changes there either.

## 5. Business logic (fat models / thin view)

The mutation logic already exists and is trivially thin. The new endpoint reuses it verbatim:

- **Join** (checkbox → on): `GuildMembership.objects.get_or_create(guild=guild, member=member)`; **on `created`**, call `orientations.member_joined_guild(guild, member)` — preserving today's side effects (activity row, lead-only in-app notice, and the guild's welcome email when configured). Its `period=guild:{pk}:join:{member.pk}` already dedupes forever, so rapid on/off/on toggling never double-sends.
- **Leave** (checkbox → off): `GuildMembership.objects.filter(guild=guild, member=member).delete()`. No side effect today; keep it that way.

The view stays skinny — it reads the guild, checks `member is not None`, calls the two paths above based on whether `joined` is present in POST, and returns 204 + toast. **Recommended (model-layer, optional):** move those three lines into `Member.join_guild(guild)` / `Member.leave_guild(guild)` so both the old full-page views and the new toggle endpoint call one method. Flagged for the model layer; not a blocker.

`build_my_guilds_rows(member)` (new service, `hub/guild_membership.py`) — mirrors `build_matrix`'s shape: returns an ordered list of small rows, one per active guild:

```python
@dataclass(frozen=True)
class GuildToggleRow:
    guild: Guild            # for name, slug, pk
    joined: bool            # member is officially in it (pre-checks the toggle)
    meeting_hint: str       # guild.meeting_schedule or "" — the row's muted subline
```

Built from `Guild.objects.filter(is_active=True).order_by("name")` and the member's joined guild-id set (`set(member.guild_memberships.values_list("guild_id", flat=True))`) — one query for guilds, one for the joined set, no N+1. A `member is None` (unlinked account) returns `[]` and the panel shows the not-linked message the other tabs already use.

## 6. UI / UX

### Placement — a new "Guilds" tab (justified)

The settings page is already a tab bar (Profile / Emails / Notifications) with an Alpine switcher and a `?tab=` deep-link (`templates/hub/user_settings.html:7`). "Which guilds am I officially in / whose announcements do I get" is a **distinct concern** — sibling to Notifications, not part of the Profile *form*. Nesting it inside the Profile `<form>` would either orphan its own submit inside a `<form>` (the nested-form bug we've hit) or bolt an unrelated block onto a big form. So: **a fourth tab, "Guilds."** It is also the exact deep-link target for the nudge email (`?tab=guilds`). Add `"guilds"` to the `active_tab` whitelist (`hub/views.py:1310`) and a tab button + panel to the template — **matching the existing tab pattern exactly**, not a hand-rolled button:

- **Tab button:** a 4th `<button class="vote-tab" :class="{ 'vote-tab--active': tab === 'guilds' }" @click="tab = 'guilds'">Guilds</button>` appended to the existing tab bar (`user_settings.html:8-27`).
- **Panel:** a sibling `<div x-show="tab === 'guilds'" x-cloak>…</div>` inserted **after the Notifications panel closes (after line 371), inside the `x-data` wrapper but outside every `<form>`.** The per-row toggles carry their own `hx-post`, so the panel must NOT sit inside the Profile `<form>` (which closes at line 287) — this placement closes the nested-form/orphaned-submit risk by construction, and there is correctly no page-level Save button.

### Screen: the Guilds tab

- **Screen / partial:** `templates/hub/partials/_my_guilds.html`, included from the `x-show="tab === 'guilds'"` panel in `user_settings.html`.
- **Layout & container:** a single `hub-card`. Inside it, a heading ("My Guilds"), a one-line explainer, then a **vertical list of guild rows** — each row is `[guild name + muted meeting hint]` on the left, a `pl-toggle` on the right. No table; a flex list that reflows. The meeting hint sources `guild.meeting_schedule`, a multi-line `TextField`, so **truncate it to one line** in the row (`|truncatechars:60` in the partial, or `.pl-guild-toggle-row__hint { white-space:nowrap; text-overflow:ellipsis; overflow:hidden }`) so a paragraph-length schedule doesn't break the row rhythm.
- **Components used:** `hub-card`; the `pl-toggle pl-toggle--sm` switch (the exact markup the notification matrix hand-renders — this is the established pattern for a **service-built** grid of toggles, not a Django-form boolean, so it does not fall under FRONTEND.md Rule 3's "use toggle.html" which targets form fields); `trigger_toast()` for feedback. New layout-only class `.pl-guild-toggle-row` in `hub.css`.
- **Explainer copy (above the list):** "Officially joining a guild means you'll get its announcement emails and show up on its roster. Flip a guild on to join, off to leave — changes save instantly."

**The controls, named explicitly:**

- **Each guild toggle = the Save.** There is **no page-level Save button** — this is the auto-save-per-toggle choice. Each toggle is an `<input type="checkbox" name="joined">` inside its `pl-toggle` label, `checked` when `row.joined`, wired:
  - `hx-post="{% url 'hub_guild_membership_set' row.guild.pk %}"`, `hx-trigger="change"`, `hx-swap="none"`.
  - Checkbox semantics do the work: **checked → `joined=on` in the POST → join; unchecked → field absent → leave.** The endpoint returns `204` + a toast.
  - `hx-disabled-elt="this"` locks the switch mid-flight, **plus `hx-indicator="closest .pl-guild-toggle-row"`** so the **row** (not just the checkbox) receives `.htmx-request` and shows the in-flight dim. htmx only puts `htmx-request` on the element that fired the request, so without `closest` the row-level `.pl-guild-toggle-row.htmx-request` dim never fires — this is the fix, name it explicitly.
  - **Error revert bound on the checkbox itself:** `hx-on::response-error` and `hx-on::send-error` each run `this.checked = !this.checked; $dispatch('show-toast', {message: "Couldn't update — please try again.", type: 'error'})`. Binding on the input makes `this` unambiguous; htmx does **not** mutate the control on error, so a single flip restores server truth. The toast fires via the FRONTEND.md client-toast contract (`$dispatch('show-toast', {...})`) — not an unspecified "small script."
- **No "+ Add" / per-row Delete formset controls.** This is deliberately *not* an editable formset — the set of guilds is fixed by admins, not created by the member. The member only flips existing rows on/off. (The famous list-editor checklist doesn't apply: nothing is created or deleted by the member except their own membership, which *is* the toggle.) This is called out so a reviewer doesn't flag a missing Add button.
- **Leave affordance:** flipping a toggle **off** is the leave. This is the first real leave-a-guild UI in the app (closing the missing-already-member branch in the `guild_detail.html` "Get Involved" panel, ~lines 230–235).

**Save-behavior rationale (auto-save per toggle vs. batch Save button).** Chosen: **per-toggle auto-save.** It (a) reuses the join/leave logic that already exists and already fires the correct per-join side effects, so there is **no new batch/diff service** to write, test, and mutation-cover; (b) gives instant, unambiguous feedback on a binary switch (a guild is a light switch, not a form); (c) matches the `member_joined_guild` contract, which is designed to fire once per single join (its `period` dedupe already protects against toggle-spam). A batch Save button (mirroring the Notifications tab exactly) was the considered alternative and is reasonable, but batching earns its keep on the Notifications tab's *dozens* of prefs; for ~14 independent binary switches it adds a diff step and a "did I forget to Save?" failure mode for no gain. (See §10 for the rejected alternative.)

**Confirm-on-leave rationale.** No confirm modal. Leaving is one click to reverse (flip it back on), and the only consequence is "you stop getting this guild's announcement emails / drop off its roster" — reversible and low-stakes, the same class of action as turning off an email channel on the Notifications tab (which also isn't confirmed). Instead, the **leave toast names the consequence** so it's never silent (copy below). A confirm modal on every off-flip would be friction out of proportion to the stakes.

### States

- **Empty:** if zero active guilds exist site-wide, the card shows "No guilds have been set up yet. Check back soon." (There are ~14, so this is a safety net, not the normal path.) Note: a member who's joined *nothing* is **not** the empty state — the grid still lists every guild, all toggles off.
- **Loading:** while a toggle's `hx-post` is in flight, the row is disabled + dimmed — `hx-disabled-elt="this"` locks the switch and **`hx-indicator="closest .pl-guild-toggle-row"`** puts `.htmx-request` on the **row** so the `.pl-guild-toggle-row.htmx-request { opacity:.5; pointer-events:none }` rule in `hub.css` actually fires (htmx tags only the firing element by default, so the `closest` indicator is required). The switch can't be re-fired until the response lands.
- **Error:** the checkbox carries `hx-on::response-error` / `hx-on::send-error` handlers that **revert it to its prior state** (`this.checked = !this.checked` — bound on the input so `this` is unambiguous) and fire the client error toast via the FRONTEND.md contract (`$dispatch('show-toast', {message: "Couldn't update — please try again.", type: 'error'})`). htmx doesn't touch the control on error, so one flip restores server truth. No half-applied UI, no 500 page.
- **Success:** the endpoint returns `204` with `trigger_toast(...)`:
  - Join → success toast: **"You joined {guild}."**
  - Leave → info toast: **"You left {guild}. You'll stop getting its announcements — rejoin anytime."**
- **Unlinked account:** `member is None` → the panel shows the same "Your account is not linked to a membership. Contact an admin for help." message the Profile tab uses. No toggles rendered.
- **No dead ends:** every action feedback is a toast; the tab is reachable and leaveable by the normal tab bar; nothing requires a page reload.

### Dark + light

- Uses **theme tokens only** — `hub-card`, `--hub-text`, `--hub-text-muted`, and the existing `pl-toggle` (already themed for both Obsidian/Slate, including its locked/disabled variants). The row class sets layout only (flex, gap, border via `--hub-border`), no colors of its own.
- **No raw `<input>`/`<select>`/`<textarea>`** on this screen — the only control is the themed `pl-toggle` checkbox, so the recurring white-box-on-dark pitfall (Rule 13) **cannot occur here**. No `--surface` fallback anywhere. No date/time pickers (Rule 14 N/A).
- The spec's requirement: **verify both themes** — the toggle on/off, the disabled/in-flight dim, and the muted meeting subline all read correctly on Obsidian and Slate.

### Mobile

- The row is `display:flex; justify-content:space-between; align-items:center; gap:0.75rem` — guild name/hint on the left (wraps), toggle pinned right. No table, no fixed widths, no horizontal scroll. Rows stack naturally in the single-column card.
- Tap target is the whole `pl-toggle` label (real switch, not a tiny icon). Spacing on the **8px grid** (`0.75rem` row padding, `0.5rem` between name and hint).

### Small consistency touch on the guild page

On `guild_detail.html`, the "Get Involved" panel (**~lines 230–235**) currently shows a **Join** button only when you're *not* a member (`{% if member and not is_member_of_guild %}`) and **nothing** when you already are — the real dead gap is the missing `{% else %}` branch in that panel (line 101 is only the comment). `is_member_of_guild` is already in the context (view ~line 434), so the branch is straightforward. Replace that gap: when `is_member_of_guild`, show a muted **"✓ You're in this guild"** line with a link to **Manage in Settings** (`{% url 'hub_user_settings' %}?tab=guilds`). Keep the existing Join button for non-members. This is a small in-scope polish that closes the same leave-UI gap from the guild page side (the actual leave happens on the Guilds tab).

## 7. Notifications / emails / activity

### Guild announcement emails — unchanged

No change to how a guild's announcement reaches its members. `guild_announcement` → `Recipients.GUILD_MEMBERS` → `guild_members` resolver → everyone with a `GuildMembership` row (`core/events/registry.py:390`, `resolvers.py:149`). Making that row via the new toggle is the entire integration; the send path is already built. No new event, template, copy, or `period`.

### The one-time membership-drive email — a manual admin send (no new pipeline)

Sent by an admin from **Site Settings → Announcements** (`templates/hub/admin/site_settings.html:371`) exactly like any sitewide announcement: compose → **Preview email** → **Send to N members**. It rides the existing `site_announcement` event and `_announcement_email_html()` branded shell — **no new template files, no cron, no bespoke blast.** The admin writes the copy; the composer produces both the HTML (branded, dark-card-styled) and the text part from the same body, so `.txt`/`.html` stay in sync automatically and the shell carries no "BETA".

**The CTA — one clear action, absolute link.** The composer body is Quill rich text, and `render_rich_email_body` renders links **gold-on-dark** in the shell, so the CTA is a link in the body (not a new button component — that would be a new pipeline). Recommended copy:

> **Subject:** Pick the guilds you want to hear from
>
> **Body:** Past Lives is organized into guilds — woodworking, textiles, electronics, and more. **Officially joining a guild means you'll get that guild's announcement emails** (meetups, calls for help, showcases) and you'll show up on its roster.
>
> Take a minute to choose yours — flip on the guilds you're part of, off the ones you're not. You can change it anytime.
>
> **→ [Set your guilds](https://pastlives.app/settings/?tab=guilds)**

The CTA target is the `hub_user_settings` route with `?tab=guilds` — the Guilds tab deep-link. In production that absolute URL is `https://pastlives.app/settings/?tab=guilds` (i.e. `MEMBER_BASE_URL + reverse("hub_user_settings") + "?tab=guilds"`). The admin pastes it into the Quill link. This satisfies the email checklist for a manual send: one obvious CTA, absolute URL, branded shell, subject/body one timezone (the shell's), both parts in sync.

### Activation-gate asymmetry — which gate applies, and the recommendation

Two audiences are in play and they gate differently:

| Send | Resolver | last_login gate? |
|---|---|---|
| The drive email (sitewide announcement) | `ALL_ACTIVE_MEMBERS` (`resolvers.py:320`) | **Yes** — only members who've signed in at least once. |
| A guild's own announcement emails (after they're official) | `GUILD_MEMBERS` (`resolvers.py:149`) | **No** — every active member in the roster, regardless of login. |

**Recommendation: keep both as-is — do not fork a special pipeline for the drive email.**
- The drive email is gated (activated members only) because it's the sitewide-announcement audience. That's correct: a member who has **never logged in can't use the Guilds settings control anyway** (they'd have to log in first, at which point the existing first-login/login-invite email already routes them into the hub). Reaching never-activated accounts would mean building a new, ungated blast — explicitly out of scope (locked decision). Accept the gate.
- The guild announcement emails themselves stay **ungated** — that's intentional and unchanged: once you've officially joined *your own* guild, you hear from it even if you later stop logging in. Privacy/activation governs broadcasts, not your own guild's mail (documented in the `guild_members` resolver).

No new `SiteActivity` kind. The join path already logs `GUILD_JOINED` via `member_joined_guild`; leaving is not logged today and this spec doesn't add logging (YAGNI).

## 8. Build order (phased; each phase ships green)

1. **Grid data service + thin endpoint.** Add `hub/guild_membership.py::build_my_guilds_rows(member)` and the `guild_membership_set(pk)` view (mirrors the existing join/leave logic; 204 + toast; join fires `member_joined_guild` on `created`). Wire the URL `hub_guild_membership_set`. Ships green with specs (§9) before any template exists.
2. **Guilds tab UI.** Add the tab button + panel to `user_settings.html`, add `"guilds"` to the `active_tab` whitelist and pass `my_guilds_rows` in the context, create `partials/_my_guilds.html` (toggle grid, per-row HTMX, error-revert script), add `.pl-guild-toggle-row` layout to `hub.css`. Verify both themes + mobile.
3. **Guild-page consistency touch.** Replace the dead "already a member" gap in `guild_detail.html` with the "✓ You're in this guild · Manage in Settings" line.
4. **Housekeeping.** Bump `plfog/version.py` VERSION (next `0.19.x` patch) + a member-friendly CHANGELOG entry (see §10 note — this is a **new** member-facing feature, so a new grouped entry at the top). The drive email itself needs no code — it's a runbook step (admin composes + sends post-deploy).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under the app's `spec/` dir, `describe_*`/`it_*`, factory-boy (`GuildFactory`, `MemberFactory`, `MembershipPlanFactory` — seed a plan so member provisioning runs), run in the `plfog-web` Docker image, ≥98% coverage.

- **`build_my_guilds_rows`:** returns one row per active guild, ordered by name; excludes soft-deleted / inactive guilds; `joined=True` exactly for guilds the member has a `GuildMembership` in; `meeting_hint` reflects `meeting_schedule`; `member=None` → `[]`; no N+1 (assert query count).
- **`guild_membership_set` view:**
  - `joined=on` on a not-joined guild → creates the `GuildMembership`, **calls `orientations.member_joined_guild`** (assert side effect / emit), returns 204 with a success toast header.
  - `joined=on` when already joined → idempotent (no duplicate row, `member_joined_guild` **not** re-fired because `created` is False).
  - no `joined` field → deletes the `GuildMembership` if present; info toast; idempotent when not a member (no error).
  - unlinked account (`member is None`) → no crash, no membership change, graceful response.
  - `@login_required` + `@require_POST` gating (GET / anonymous rejected); unknown `pk` → 404.
- **`user_settings` view:** `?tab=guilds` sets `active_tab="guilds"` and passes rows; a bogus `?tab=` still falls back to `profile` (whitelist unchanged for the others).
- **Template/states (rendered-HTML assertions, not just view-client):** the Guilds panel renders a `pl-toggle` per row with `checked` matching `joined`; the empty-guilds message appears with zero guilds; the unlinked message appears for `member=None`. (Per `reference_nested_form_save_bug.md`, assert against parsed HTML for the toggle/`hx-post` wiring — a test-client POST won't catch a structural mistake.)
- **No tz/date windows** in this feature. The one gotcha to guard: the join emit's `period` dedupe (`guild:{pk}:join:{member.pk}`) — a spec should confirm a leave-then-rejoin does **not** resend the welcome email (existing behavior, worth pinning).
- **Leave→rejoin non-email side effects.** The toggle makes off→on trivial, so pin the side effects that are **not** period-guarded: rejoin's `get_or_create` returns `created=True`, re-firing `orientations.member_joined_guild`, which logs a fresh `SiteActivity.GUILD_JOINED` row and re-sends the guild-lead in-app "new member" notice each time. Either assert these are acceptable duplicates (matches today's full-page join view) or make the lead notice / activity idempotent per member+guild. At minimum a spec names the decision — so a lead isn't pinged five times by a member flipping the switch.

## 10. Out of scope / deferred

- **No new "official" flag or model field, no migration** — reuse `GuildMembership` (locked).
- **No new email template files, no cron, no bespoke blast pipeline** — the drive email is a manual composer send (locked). No automated "you haven't picked guilds yet" nagging.
- **Batch Save button + `save_guild_matrix` diff service** — considered and rejected in favor of per-toggle auto-save (§6). If instant-toggle ever proves noisy, this is the fallback.
- **Confirm modal on leave** — rejected (reversible, low-stakes; consequence named in the toast).
- **No search / filter / pagination / reordering / "recommended guilds"** — ~14 guilds, a plain list (locked).
- **No change to guild announcement send mechanics** — `guild_members` resolver untouched.
- **No leave logging / no leave side effects** — matches today's `guild_leave`.
- **Reaching never-logged-in members with the drive email** — out of scope; they're funneled via the existing first-login email (§7).
- **`Member.join_guild()/leave_guild()` model-method extraction** — recommended (fat models) but a model-layer change; the view can ship by mirroring the existing three lines.

### Changelog note (housekeeping)

This is a **net-new member-facing feature**, so it gets its own new grouped CHANGELOG entry at the top of `plfog/version.py` (plain, friendly language — e.g. "Choose which guilds you're officially part of, right from Settings — and get just those guilds' announcements. It's also finally easy to leave a guild you're no longer in."), stamped to the new `VERSION`. It is **not** a refinement of the existing `0.19.11` notifications entry, so do not fold it in there.

## Done checklist

- [ ] `build_my_guilds_rows(member)` returns all active guilds, joined-flagged, ordered, no N+1; `None` → `[]`.
- [ ] `hub_guild_membership_set` endpoint joins (fires `member_joined_guild` on create) / leaves, returns 204 + correct toast, is idempotent, `@login_required @require_POST`, 404s unknown guild.
- [ ] New **Guilds** tab added to `user_settings.html`; `"guilds"` in the `active_tab` whitelist; `?tab=guilds` deep-link works.
- [ ] `partials/_my_guilds.html`: `pl-toggle` per guild, pre-checked from `joined`, per-toggle `hx-post`, `hx-disabled-elt` in-flight lock, error-revert script.
- [ ] Empty / loading / error / success / unlinked states all present and described-as-built.
- [ ] Success toasts: "You joined {guild}." / "You left {guild}. …rejoin anytime."
- [ ] Guild page: "✓ You're in this guild · Manage in Settings" replaces the dead already-member gap.
- [ ] Dark **and** light verified; mobile reflow verified (no horizontal scroll); 8px-grid spacing; `pl-` prefix; layout class in `hub.css`.
- [ ] No raw `<input>/<select>/<textarea>` (only themed `pl-toggle`); no `--surface` fallback anywhere.
- [ ] Specs green (≥98% cov) in `plfog-web`; `ruff format .` + `ruff check .` clean.
- [ ] Drive-email runbook noted: admin composes in Announcements, CTA links `…/settings/?tab=guilds`, Discord left off, sent post-deploy.
- [ ] `VERSION` bumped; new grouped CHANGELOG entry added (not folded into the notifications entry).
