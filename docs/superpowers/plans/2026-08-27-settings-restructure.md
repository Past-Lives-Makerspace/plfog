# Settings Restructure — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-27
**Surface:** FOG hub `pastlives.test` — the User Settings page (`/settings/`) and its four tabs; touches the shared notification matrix partial (also rendered on admin member-edit and the token email-prefs page) and the shared confirm modal component.
**Related:** `2026-08-26-guild-subscriptions.md` (the Guilds tab + `?tab=guilds` answered-stamp this spec now makes the *default* landing), the notification spine (`core/events/`), `2026-08-24-meetings-qol.md` (the `/meetings/` page the new Meetings subtitle links to).

---

## 1. Summary

Six related cleanups that make `/settings/` behave like one coherent page instead of five bolted-on tabs. Members land on the things they touch most (Guilds, then Notifications); the Notifications tab gets a sensible section order (Orientations → Guilds → Events → the rest), jump-to-section chips, and a floating back-to-top button; the redundant Emails tab folds into Account; the Danger Zone stops looking like a prototype; two notification sections gain the same "manage it here →" subtitle the Staff section already has; an admin previewing the page as a Member no longer sees the Staff & Leadership section; and unsaved toggle changes now prompt before the user navigates away.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| (a) View-as and the Staff section | `build_matrix` gains an `include_staff_section` flag; when an admin/officer is previewing as Member (or Guest), the view passes `False` and the Staff & Leadership section is omitted entirely. `settings_matrix` stays framework-agnostic — the view derives the flag from `request.view_as`, the matrix never imports the request. **`save_matrix` gets the same flag** (see §5.2 — without it, saving while previewing would silently wipe staff prefs). |
| (b) Section order | New `CATEGORY_ORDER`: Orientations, Guilds, Events first; the rest keep their current relative order; **Staff & Leadership moves dead-last** (the code's own comment already claims it is — the code just never did it). Jump chips at the top of the Notifications pane, one per rendered section, plain anchor scroll (reduced motion respected). Floating back-to-top after ~400px scroll, bottom-right, hub tokens, with mobile clearance for the Save button. |
| (c) Emails tab | Removed. The whole "Manage Email Addresses" card moves into the Account tab **above** the danger zone. `?tab=emails` resolves to `account` (old links keep working); allauth's `HubEmailView` redirect target updated. Danger Zone gets a dedicated `.pl-danger-zone` card treatment (red-tinted border + background in both themes, warning icon, tightened copy, right-aligned button in a card footer). The typed-DELETE confirm stays exactly as is. |
| (d) Section subtitles | Guilds section: "Manage which guilds send you updates →" switching to the Guilds tab in-page (Alpine, no reload — the `_my_guilds.html:43` pattern). Meetings section: "View upcoming meetings →" linking to `hub_meetings` (`/meetings/`). Both use the existing `.pl-notif-section-note` pattern, shown only on the member's own settings page. Plain language, no dashes; the `→` matches the existing Staff note. |
| (e) Tab order | Guilds, Notifications, Profile, Account. **Default tab becomes `guilds`** — which means every bare `/settings/` visit fires `mark_guild_updates_answered()`. Deliberate and accepted: viewing your guild toggles *is* answering the prompt (it is idempotent, one-way, no-op after the first hit). Profile sub-tabs (Member / Instructor) untouched. |
| (f) Unsaved-changes guard | Scoped to the three batch-save forms only (notification matrix, Guided Tours, Profile). Alpine dirty flags per form; tab switches route through a guard that opens a two-option confirm ("Stay" / "Discard Changes" — reusing `confirm_modal.html` in its JS mode, plus a tiny backward-compatible `confirm_cancel_text` param); `beforeunload` for hard navigations; **`htmx:confirm` interception for hx-boost'd links** (htmx 2.0.4 supports async `issueRequest` resume — verified in the bundled `htmx.min.js`). Guild autosave toggles never trip it; bulk All on/off buttons do; Profile's Alpine-only controls arm it via explicit `data-arm-dirty` markers, and a Profile discard is a pane **reload** (its Alpine-fed hidden inputs make `form.reset()` unsafe — §6.6). Auto-save-on-leave is out of scope; Back-button history restoration is an accepted gap (§10). |

## 2. What already exists (reuse, don't reinvent)

All verified in the codebase on 2026-08-27:

| Need | Existing thing | Location |
|---|---|---|
| The settings page + tab state | Pure Alpine `x-data="{ tab: … }"`, `vote-tab` buttons, `x-show` panes all rendered in initial HTML | `templates/hub/user_settings.html:7-39` |
| Tab whitelist + guilds answered-stamp | `_resolve_settings_tab` — whitelist `{profile,emails,notifications,guilds,account}`, `?tab=guilds` fires `member.mark_guild_updates_answered()` | `hub/views.py:2194-2209` |
| The view + `form_id` POST dispatch | `user_settings` (`profile` / `notifications` / `tours` form_ids; emails POST to allauth) | `hub/views.py:2071-2191` |
| The matrix builder / saver | `build_matrix(user)` / `save_matrix(user, posted)` — raw user, no view-as awareness (bug (a)) | `core/events/settings_matrix.py:343,395` |
| Section ordering | `CATEGORY_ORDER` (:63-73) + `_ordered_categories` (:263-267) — staff section rendered FIRST despite the ":61-62" comment claiming "always forced dead-last" | `core/events/settings_matrix.py` |
| Category grouping per event | `_section_for` — staff-routed events collapse into `STAFF_SECTION` regardless of their `category`; everything else keys off `event.category` | `core/events/settings_matrix.py:150-158` |
| Preference storage is **event-keyed** | `NotificationPreference(user, event_key, channel, enabled)`, unique on `(user, event_key, channel)`; category is never stored. `save_matrix` upserts by `event_key` (`:419-424`) | `core/models.py:1117-1146` |
| View-as roles | `request.view_as` (`ViewAs`), `view_as_role`, `actual_is_admin`, `actual_is_guild_officer`; attached by `ViewAsMiddleware` | `hub/view_as.py:109-202,305-317` |
| The matrix template + Staff note pattern | `.pl-notif-section-note` under `Staff & leadership` with trailing link "Manage your admin duties →" | `templates/hub/partials/_notification_matrix.html:36-42`; CSS `static/css/components.css:417-420` |
| Bulk on/off | `plNotifBulk(scope, on)` — flips non-disabled checkboxes, **fires no `change` events** (matters for (f)) | `_notification_matrix.html:92-103` |
| In-page tab-switch link pattern | `<a href="?tab=notifications" @click.prevent="tab = 'notifications'">` | `templates/hub/partials/_my_guilds.html:43` |
| Guilds tab autosave (no Save button) | per-toggle `hx-post` + error-revert toast — must NOT trip the dirty guard | `templates/hub/partials/_my_guilds.html:24-37` |
| Emails card to relocate | "Manage Email Addresses" — allauth radio list + actions + Add form | `templates/hub/user_settings.html:579-643` |
| allauth redirect into the Emails tab | `HubEmailView` — `success_url = "/settings/?tab=emails"` + GET redirect | `plfog/urls.py:52-65` |
| Current danger zone | bare `hub-card` + `<h3>Danger Zone</h3>` + `pl-btn--danger` + typed-DELETE `confirm_modal` | `templates/hub/user_settings.html:658-669` |
| Confirm modal (incl. JS mode) | `confirm_js` runs inline JS + closes — exactly the discard-confirm shape; Cancel label currently hardcoded | `templates/components/confirm_modal.html:39-42,80-84,109` |
| Back-to-top precedent (public CMS) | Alpine `show: scrollY > 400`, fixed 44px circle bottom-right | `templates/classes/public/list.html:146-155`; CSS `static/css/cms-public.css:436-437` |
| Smooth scroll + reduced motion | `html { scroll-behavior: smooth }` with `prefers-reduced-motion` reset | `static/css/hub.css:182-190` |
| Sticky topbar (anchor offset needed) | `.pl-topbar` sticky, `top:0`, height `var(--topbar-height) + safe-area` | `static/css/hub.css:801-809` |
| hx-boost topology | `<body hx-boost="true">`; the sidebar nav opts OUT (`hx-boost="false"` — real navigations, so `beforeunload` fires there) | `templates/hub/base.html:72,89` |
| htmx async confirm | htmx **2.0.4** bundled; `htmx:confirm` event + `evt.detail.issueRequest(true)` both present | `static/js/htmx.min.js` |
| Meetings page | `hub_meetings` → `/meetings/` | `hub/urls.py:8` |
| Other matrix consumers (untouched, default flag) | token prefs flow (`hub/views.py:1964-2001`), admin member-edit Permissions tab (`hub/views.py:5293-5331`) | as listed |

### Genuine gaps to close

1. `include_staff_section` flag on `build_matrix`/`save_matrix` + the view-as plumbing.
2. Two category re-tags in `core/triggers.py` + the new `CATEGORY_ORDER` + staff-last ordering.
3. Jump chips, back-to-top, danger-zone card, and the dirty-guard JS — all new, all small.
4. `confirm_cancel_text` param plus open-focus/focus-restore on `confirm_modal.html` — backward compatible.
5. POST re-renders must derive the active tab from `form_id` (the failed-profile-save blocker, §5.3), and Profile discard must be a pane reload, not `form.reset()` (§6.6).

## 3. Where the code lives

```
core/
  triggers.py                    # orientation_requested + orientation_update: category "Guilds" → "Orientations"
  events/settings_matrix.py      # include_staff_section flag on build_matrix/save_matrix/_visible_events/
                                 #   visible_channels; new CATEGORY_ORDER; _ordered_categories: staff → tail
hub/
  views.py                       # user_settings: derive include_staff flag from request.view_as, pass to
                                 #   build + save; _resolve_settings_tab: default "guilds", drop "emails",
                                 #   alias emails→account
plfog/
  urls.py                        # HubEmailView: success_url + GET redirect → /settings/?tab=account
templates/
  hub/user_settings.html         # tab reorder + default; Emails pane removed, card moved into Account;
                                 #   danger-zone markup; back-to-top button; dirty-guard x-data + discard
                                 #   confirm include; go() tab guard
  hub/_notifications_settings.html   # jump-chips nav above the matrix
  hub/partials/_notification_matrix.html  # section anchor ids; Guilds + Meetings subtitles; plNotifBulk
                                 #   change-event dispatch
  hub/partials/_profile_field_with_visibility.html  # + data-arm-dirty on the vis pill (dirty-guard arming)
  components/confirm_modal.html  # + confirm_cancel_text param; focus Cancel on open + restore opener on
                                 #   close (a11y)
static/css/
  components.css                 # .pl-notif-jump chips; .pl-danger-zone; .pl-notif-section scroll-margin
  hub.css                        # .pl-scroll-top (hub-tokenized back-to-top); mobile Save clearance
tests/
  core/notification_matrix_ordering_spec.py   (new — ordering, re-tag, staff flag)
  hub/notification_settings_spec.py           (extend — view-as omission, save skip)
  hub/user_settings_tabs_spec.py              (new — tab fallback, emails alias, default landing stamp)
  hub/account_tab_spec.py                     (new or fold into account_delete_spec — moved card, danger zone)
```

Home apps: `core` (matrix/registry), `hub` (view + templates). No new app, no new models — everything inside the existing coverage/mypy scope.

## 4. Data model

**None.** No new models, no migrations. The one data-shaped change is re-tagging two trigger categories (§5.3), and that is safe because preferences are stored per `(user, event_key, channel)` — the category never touches the database.

**Evidence, as required:** `core/models.py:1132-1135` — `NotificationPreference` fields are `user`, `event_key`, `channel`, `enabled`; the unique constraint (`:1138-1143`) is on `(user, event_key, channel)`. `save_matrix` upserts with `event_key=event.key` (`core/events/settings_matrix.py:419-424`). `orientation_requested` and `orientation_update` keep their keys; only `Trigger.category` (a display string) changes, so every stored preference row keeps matching.

**One knock-on effect to state out loud:** `event.category` also rides outgoing email as the `X-Category` header (`core/events/channels.py:147`, `core/email.py:53`). After the re-tag, orientation-request/update emails carry `X-Category: Orientations` instead of `Guilds`. That header is ESP analytics grouping only — arguably more correct now — but the reviewer should see it was noticed.

## 5. Business logic (fat models / service layer)

### 5.1 The event re-categorization inventory

The explorer's claim that "there is no Orientations or Events category" was **wrong** — both exist in the registry, along with **Meetings**; they are merely absent from `CATEGORY_ORDER`, so today all three alpha-sort to the bottom as "extras" (`_ordered_categories:266`). The re-categorization is therefore mostly an *ordering* change plus **two re-tags**. Full inventory (every registered event, its declared category, and where it renders after this change; "Staff" = collapses into Staff & Leadership via `STAFF_RECIPIENTS` regardless of category):

| Section (new order) | Member-facing rows | Staff-collapsed rows from this category |
|---|---|---|
| **1. Orientations** | `orientation_update` (**re-tagged** from Guilds), `orientation.completed` | `orientation_requested` (**re-tagged** from Guilds; recipient `GUILD_ORIENTERS` → renders in Staff anyway — re-tagged for `X-Category` coherence) |
| **2. Guilds** | `guild_announcement`, `guild_announcement.approved`, `guild_announcement.changes_requested`, `guild_announcement.declined`, `discord_guilds_imported` | `guild_announcement.submitted`, `guild_joined` (→ `GUILD_LEAD`) |
| **3. Events** | `event.guild_published`, `event.community_published`, `event.approved`, `event.changes_requested`, `event.declined`, `event.reminder`, `event.happening_now` | `event.submitted`, `event.lead_meeting_published` (→ `ALL_GUILD_LEADS`) |
| 4. Classes | `class_published`, `class_reminder`, `registration_confirmed`, `class_cancelled` (forced), `waitlist_spot_available`, `waitlist_confirmed`, `refund_issued` (forced), `waitlist_promoted`, `waitlist_promoted_pay`, `registration_removed`, `class_announcement` | — |
| 5. Teaching | `instructor_class_approved`, `instructor_changes_requested`, `instructor_new_registration` | `class_review_requested`, `class_validation_requested`, `discount_code.requested` |
| 6. Voting | `voting.closing_soon`, `voting.vote_soon`, `voting.results_published` | `voting.officers_closing_soon`, `voting.results_ready`. (`voting.discord_reminder` / `voting.results_discord` declare only the broadcast `DISCORD` channel — no user channel — so `_visible_events` already drops them; verified `registry.py:853-873`.) |
| 7. Billing | `tab_charged`, `tab_charge_failed`, `tab_entry_added` (forced), `tab_approaching_limit` (forced) | `refund_failed`, `billing.charge_failed_admin` |
| 8. Membership | `member.invited`, `member.login_invite`, `invite_accepted` | `new_member_joined` (→ `FOG_ADMINS`) |
| 9. Spaces | `lease_expiring` (forced), `space.request_approved`, `space.request_declined` | `space.lease_requested`, `space.cubby_requested` |
| 10. Announcements | `site_announcement`, `release.published` | — |
| 11. Security | *(no registered events — category never renders; kept in the tuple for the day one returns)* | — |
| 12. Meetings | `meeting.item_decided`, `meeting.minutes_approved` | `meeting.item_proposed`, `meeting.council_minutes_approved` |
| **13. Staff & Leadership** (moved last) | *(eligible viewers only — all the staff-collapsed rows above)* | |

**Meetings finding (asked explicitly):** a `Meetings` category **does exist** and plain members **do** see it — `meeting.item_decided` and `meeting.minutes_approved` are member-facing. The "View upcoming meetings →" subtitle goes on this category's header (§6.5); no fallback placement needed.

**Code changes:**

- `core/triggers.py`: `orientation_requested` (:100-104) and `orientation_update` (:105-110) — `"Guilds"` → `"Orientations"`. Their keys, labels, descriptions, and audiences are untouched.
- `core/events/settings_matrix.py` `CATEGORY_ORDER` (:63-73) becomes:

  ```python
  CATEGORY_ORDER: tuple[str, ...] = (
      "Orientations",
      "Guilds",
      "Events",
      "Classes",
      "Teaching",
      "Voting",
      "Billing",
      "Membership",
      "Spaces",
      "Announcements",
      "Security",
      "Meetings",
  )
  ```

  ("The rest in their current relative order" — today's *rendered* order for the tail is Classes, Teaching, Voting, Billing, Membership, Spaces, Announcements, then the alpha extra Meetings; Security keeps its slot though it currently renders nothing.)
- `_ordered_categories` (:263-267): staff moves from `head` to `tail` — `return ranked + rest + tail`. The comment at `:61-62` ("STAFF_SECTION is always forced dead-last") finally becomes true; update the docstring on `build_matrix` (:349-351, "rendered first") to match.

### 5.2 `include_staff_section` (feature (a))

`settings_matrix` stays framework-agnostic: a keyword-only bool, no request objects.

```python
def build_matrix(user: User, *, include_staff_section: bool = True) -> list[tuple[str, list[Row]]]: ...
def save_matrix(user: User, posted: dict[str, str], *, include_staff_section: bool = True) -> None: ...
```

- `_visible_events(user, include_staff_section)`: when `False`, skip every event whose `recipient in STAFF_RECIPIENTS` *before* the eligibility check — the staff section is omitted entirely.
- `visible_channels` takes and forwards the flag too — a channel offered only by staff events must not render as a dead column for a member-view preview.
- **Why `save_matrix` needs the flag (the trap):** `save_matrix` treats an absent checkbox as `enabled=False` and iterates `_visible_events(user)` with the raw user. If the GET hid the staff section but the POST saved without the flag, every staff checkbox would be absent → an admin clicking Save while previewing as Member would **silently wipe all their staff notification prefs**. The view computes the flag once per request (the view-as choice is session-backed, so GET and the subsequent POST agree) and passes it to both calls. When `False`, `save_matrix` never touches staff-event rows — same protective pattern as the existing Discord-unlinked skip (`:416-418`).

**View plumbing** (`hub/views.py user_settings`):

```python
va = request.view_as
include_staff = not (
    va.view_as_role in (view_as.ROLE_MEMBER, view_as.ROLE_GUEST)
    and (va.actual_is_admin or va.actual_is_guild_officer)
)
```

Rationale for this exact condition: `ViewAs.is_member` is `True` for everyone holding the member role (admins included), so it cannot be the test. The flag must flip only when a *higher-role holder is previewing down* — an actual plain member (including a guild lead whose `fog_role` is member) always gets `True`, so leads keep the staff rows their `led_guilds` eligibility grants today (`settings_matrix.py:222-227`); a plain member's staff section stays absent via the existing eligibility filter either way. Guest preview also hides it (an admin previewing Guest sees even less than a member). The other two call sites — the token flow (`hub/views.py:1984,1995`) and admin member-edit (`:5296,5329`) — keep the default `True`: no view-as concept applies to a token-authorized member or to an admin editing someone else's prefs.

### 5.3 Tab resolution (features (c) + (e))

`_resolve_settings_tab` (`hub/views.py:2194-2209`):

```python
tab_param = request.GET.get("tab", "guilds")
if tab_param == "emails":          # legacy deep links (old emails tab) land on its new home
    tab_param = "account"
active_tab = tab_param if tab_param in {"profile", "notifications", "guilds", "account"} else "guilds"
```

- Default and unknown-value fallback both become `guilds` (the new first tab). The XSS-whitelist rationale in the docstring stays word-for-word — the value still flows into an Alpine `x-data` expression.
- **Failed-POST re-render must land on the tab that failed (reviewer blocker).** The profile form posts to the current URL with no `?tab` param; on a validation failure the view re-renders instead of redirecting. With `guilds` as default, that re-render would resolve `active_tab="guilds"` — the pane holding the errors hidden, the user believing Save worked — and, as a bonus wrong-write, that render would stamp `mark_guild_updates_answered()` on a landing the user never chose. Fix in `user_settings`: on POST, derive the tab from the submitted `form_id` and consult `_resolve_settings_tab` on GET only:

  ```python
  if request.method == "POST":
      active_tab = {"profile": "profile", "tours": "notifications", "notifications": "notifications"}.get(
          request.POST.get("form_id", ""), "guilds"
      )
  else:
      active_tab = _resolve_settings_tab(request, member)
  ```

  The notifications and tours handlers redirect on success and have no failing-validation path today, so `profile` is the only live re-render — but the mapping covers all three so any future validating handler inherits the right tab for free. The guilds stamp now fires on GET resolution only. Pinned by a spec: "failed profile POST re-renders with `active_tab == 'profile'` and does not stamp `guild_updates_prompt_answered_at`" (§9).
- **Stated deliberately for the reviewer:** with `guilds` as default, `mark_guild_updates_answered()` now fires on **every** bare `/settings/` GET (`:2207-2208`), not just `?tab=guilds` deep links. Accepted: landing on your guild toggles *is* seeing and answering the guild-updates prompt; the method is login-gated, one-way, idempotent, and a no-op after the first hit (`2026-08-26-guild-subscriptions.md` §5 established the write-on-GET pattern; this widens when, not what).
- `HubEmailView` (`plfog/urls.py:62,65`): both `success_url` and the GET redirect become `"/settings/?tab=account"` (allauth's add/primary/resend/remove flows land the user back beside the email card).

## 6. UI / UX

The page keeps its architecture: one Alpine root, `vote-tab` buttons, all panes in the initial HTML behind `x-show`. Every subsection below ends with its states / themes / mobile notes.

### 6.1 Tab bar — new order + default (e)

- **Screen:** `templates/hub/user_settings.html:8-39`.
- Reorder the four buttons: **Guilds, Notifications, Profile, Account** (Emails button deleted). Each `@click="tab = '…'"` becomes `@click="go('…')"` — the dirty-guard router (§6.6). Alpine root default: `x-data="{ tab: '{{ active_tab|default:"guilds" }}', … }"` (the view always supplies `active_tab`; the template default changes for belt-and-braces).
- Profile pane internals (Member/Instructor sub-tabs, `ptab`) untouched.
- **States:** unlinked account (`member is None`) — Guilds pane already renders its "not linked to a membership" message (`_my_guilds.html:49`), so the new default landing still shows something sensible, plus the existing info message (`hub/views.py:2144-2145`). No other state change.
- **Mobile / themes:** existing `pl-tabs` styling; nothing new.

### 6.2 Emails → Account (c): the moved card

- **Screen:** Account pane, `user_settings.html` (current `:658-669`, growing).
- The entire "Manage Email Addresses" card (`:582-642`) moves verbatim into the Account pane, **above** the danger zone. Two copy edits inside it:
  - The footer cross-link "Choose which emails you receive in the **Notifications** tab" keeps its in-page tab switch but routes through the guard: `@click="go('notifications')"`.
  - Profile tab email hint (`:147`) "Manage your emails in the Emails tab." → "Manage your emails in the Account tab."
- The Emails pane (`:579-643`) and its tab button are deleted. Old links: `?tab=emails` aliases to `account` (§5.3); the allauth redirect is updated (§5.3). No other template references `tab=emails` (verified by grep — only `plfog/urls.py`).
- The pane loses its stray "Delete Your Account" `<h2>` page heading — each card carries its own heading (Title Case, per FRONTEND rule 22): "Manage Email Addresses", "Add an Email Address", "Danger Zone".
- **The controls, named:** unchanged from today — radio list per address with Verified/Unverified/Primary pills; **Make Primary** / **Re-send Verification** (shown only for an unverified selection, existing Alpine `selectedVerified`) / **Remove** (native confirm, as today) buttons submitting to `account_email`; the **Add Email** form (`form_field.html` fields + primary button). All POSTs land back on `?tab=account`.
- **States:** zero addresses → existing "No email addresses on file yet." empty state; allauth validation errors re-render via `form_field.html` as today. Success feedback: full-page POST + Django messages (existing pattern, correct for non-HTMX forms).
- **Themes / mobile:** the card is existing, already token-clean; the button row already wraps (`flex-wrap`). Verify both themes after the move (no CSS change expected).

### 6.3 Danger Zone polish (c)

- **Screen:** Account pane, below the email card, `margin-top: 1.5rem` (buttons never touch an adjacent section — FRONTEND rule 18 applies between the cards too).
- **Markup (exact):**

  ```html
  <div class="hub-card pl-danger-zone">
      <div class="pl-danger-zone__head">
          <svg class="pl-danger-zone__icon" width="18" height="18" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
               aria-hidden="true">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
              <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <h2 class="pl-danger-zone__title">Danger Zone</h2>
      </div>
      <p class="pl-danger-zone__text">
          Deleting your account permanently removes your personal info (name, photo, contact details, bio)
          from Past Lives and signs you out everywhere. It does not cancel any paid subscription managed
          outside this site.
      </p>
      <p class="pl-danger-zone__text">
          <strong>This can't be undone from here.</strong> To use Past Lives again, a guild lead or admin
          will need to send you a new invite.
      </p>
      <div class="pl-danger-zone__footer">
          <button type="button" class="pl-btn pl-btn--danger pl-btn--sm"
                  @click="$dispatch('open-confirm', 'delete-account')">Delete My Account</button>
      </div>
  </div>
  ```

  The `confirm_modal` include with `confirm_typed_value="DELETE"` (`:667`) is kept **byte-for-byte** — the typed-DELETE flow is locked. Note the trigger button also drops to `pl-btn--sm` per the destructive-action checklist (§3 of the UX rubric: danger buttons are small, the modal is the guard).
- **CSS (`static/css/components.css`, new — it is a reusable card treatment):**

  ```css
  .pl-danger-zone { border: 1px solid rgba(220, 38, 38, 0.45); background: rgba(220, 38, 38, 0.07); }
  .pl-danger-zone__head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; }
  .pl-danger-zone__icon { color: #f0a0a0; flex-shrink: 0; }
  .pl-danger-zone__title { font-size: 1rem; font-weight: 700; margin: 0; color: #f0a0a0; }
  .pl-danger-zone__text { margin: 0 0 0.75rem; font-size: 0.9rem; }
  .pl-danger-zone__footer { display: flex; justify-content: flex-end; margin-top: 1.25rem;
                            padding-top: 1rem; border-top: 1px solid rgba(220, 38, 38, 0.25); }
  [data-theme="light"] .pl-danger-zone { border-color: rgba(220, 38, 38, 0.35); background: rgba(220, 38, 38, 0.05); }
  [data-theme="light"] .pl-danger-zone__icon,
  [data-theme="light"] .pl-danger-zone__title { color: #b03030; }
  ```

  The `#f0a0a0` / `#b03030` pair is the established danger-text precedent (`hub.css:442,574`); rgba tints over the theme's card background work in both themes without inventing a token. **Verify both themes.**
- **States:** none beyond the modal (existing). **Mobile:** card reflows naturally; the footer button stays right-aligned (a full-width danger button would over-invite the tap).

### 6.4 Notifications — section order, jump chips, back-to-top (b)

- **Screens:** `templates/hub/_notifications_settings.html` (chips), `templates/hub/partials/_notification_matrix.html` (anchors), `user_settings.html` (back-to-top).
- **Section order** is entirely server-side (§5.1/5.3) — the template renders `notif_matrix` in order received.
- **Jump chips** — in `_notifications_settings.html`, between the intro paragraph and the Discord CTA (only this wrapper gets them; the admin member-edit and token pages include the matrix partial directly and stay chip-free):

  ```html
  <nav class="pl-notif-jump" aria-label="Jump to a notification section">
      {% for category, rows in notif_matrix %}
          <a class="pl-notif-jump__chip" href="#notif-{{ category|slugify }}">{{ category }}</a>
      {% endfor %}
  </nav>
  ```

  In `_notification_matrix.html:27`, each section gains its anchor: `<div class="pl-notif-section" id="notif-{{ category|slugify }}">`. `slugify` handles "Staff & leadership" → `staff-leadership`; ids are unique per page (one matrix per page on every consumer). Plain hash anchors: `hx-boost` ignores local `#` links, so no htmx involvement, and `html { scroll-behavior: smooth }` + the existing `prefers-reduced-motion` reset (`hub.css:182-190`) give smooth-or-instant scrolling for free — **no JS**.
- **Anchor offset** for the sticky topbar: `.pl-notif-section { scroll-margin-top: calc(var(--topbar-height) + 1rem); }` (components.css, next to the existing `.pl-notif-section` rules at `:412`).
- **Chips CSS** (components.css, `pl-notif-*` family):

  ```css
  .pl-notif-jump { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0 0 1.25rem; }
  .pl-notif-jump__chip { padding: 0.25rem 0.75rem; border-radius: 999px; font-size: 0.8rem;
                         background: var(--hub-surface); color: var(--hub-text-muted);
                         border: 1px solid transparent; text-decoration: none; }
  .pl-notif-jump__chip:hover,
  .pl-notif-jump__chip:focus-visible { color: var(--hub-text); border-color: var(--color-tuscan-yellow);
                                       text-decoration: none; }
  ```

  Theme tokens only; both themes covered by the tokens themselves.
- **Back-to-top** — in `user_settings.html`, after the tab panes (page-scoped: every tab here is long, and the button is harmless when the page is short because it only shows past 400px):

  ```html
  <button type="button" class="pl-scroll-top"
          x-data="{ show: false }" x-show="show" x-cloak
          x-init="window.addEventListener('scroll', () => { show = window.scrollY > 400 }, { passive: true })"
          @click="window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })"
          aria-label="Back to top">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 15l-6-6-6 6"/></svg>
  </button>
  ```

  This is the public-CMS pattern (`classes/public/list.html:146-155`) with two upgrades: hub tokens, and an explicit reduced-motion check (CSS `scroll-behavior` does **not** govern a JS `scrollTo` with an explicit `behavior`, so the `matchMedia` guard is required, not decoration).
- **CSS (`static/css/hub.css`, hub-tokenized — deliberately NOT the cms `#scroll-top-btn` styles):**

  ```css
  .pl-scroll-top { position: fixed; bottom: calc(1.25rem + env(safe-area-inset-bottom, 0px)); right: 1.25rem;
                   z-index: 90; width: 44px; height: 44px; border-radius: 50%; border: 1px solid var(--hub-border);
                   cursor: pointer; background: var(--hub-elevated); color: var(--color-tuscan-yellow);
                   display: flex; align-items: center; justify-content: center;
                   box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25); }
  .pl-scroll-top:hover { transform: translateY(-2px); }
  @media (max-width: 768px) {
      #pl-settings-root { padding-bottom: 4.5rem; }
  }
  ```

  (`--hub-border` is a real token — defined for both themes at `hub.css:68` and `:139`.) **z-index: 90, pinned** — above page content and the sticky topbar (50; irrelevant anyway, the button lives at the bottom edge) and below the modal layer (`.pl-modal-backdrop`, 500), so the discard confirm always covers the button.
- **Mobile clearance (the locked requirement, corrected per review):** padding the Save rows sideways guards the wrong elements — the real bottom-right colliders on a phone are the **Guilds tab's right-edge toggle switches** (the new default landing), the **danger-zone footer button** on Account, and the **Tours card Save** sitting below `.pl-notif-actions`. One rule covers every tab: `padding-bottom: 4.5rem` on the settings root (`#pl-settings-root`) at ≤768px, so the page's last row of controls always scrolls clear of the fixed 44px button — the FAB floats over the padded strip, never over a control. State in the PR that this was checked at phone width on the Guilds, Notifications, and Account tabs.
- **States:** chips render one per *rendered* section — a member with no staff section gets no dead "Staff & Leadership" chip, and an empty category never renders a section at all (categories derive from actual rows, `build_matrix:392`), so no chip can point at nothing. Back-to-top hidden until 400px (its `x-cloak` prevents a flash). **Reduced motion:** anchors instant via the existing CSS reset; button instant via the `matchMedia` guard.
- **A11y:** chips are real anchors inside a labeled `<nav>` (keyboard + screen-reader native); back-to-top keeps the `aria-label` and is a real `<button>` with a 44px tap target.

### 6.5 Section subtitles (d)

- **Screen:** `_notification_matrix.html`, immediately after the category heading row — extending the existing Staff-note conditional block (`:36-42`), same `.pl-notif-section-note` class, no new CSS.

  ```html
  {% if category == 'Guilds' and matrix_self and not prefs_token %}
      <p class="hub-text-muted pl-notif-section-note">
          <a href="?tab=guilds" @click.prevent="go('guilds')">Manage which guilds send you updates →</a>
      </p>
  {% endif %}
  {% if category == 'Meetings' and matrix_self and not prefs_token %}
      <p class="hub-text-muted pl-notif-section-note">
          <a href="{% url 'hub_meetings' %}">View upcoming meetings →</a>
      </p>
  {% endif %}
  ```

- The Guilds link is the `_my_guilds.html:43` in-page pattern — `href` as a real fallback, `@click.prevent="go(…)"` so it switches the Alpine tab without a reload (and routes through the dirty guard, which is exactly what we want: jumping from unsaved matrix edits to the Guilds tab should prompt).
- The Meetings link is a normal (boosted) navigation to `/meetings/` (`hub_meetings`, `hub/urls.py:8`) — when the matrix is dirty, the htmx-confirm guard (§6.6) intercepts it; that is correct behavior, not an accident.
- **`matrix_self and not prefs_token` guard, reasoned:** `matrix_self` excludes the admin member-edit render (an admin editing *your* prefs has no "your guilds" tab in scope); `not prefs_token` excludes the token email-prefs page, where there is no Alpine `tab`/`go()` and `?tab=guilds` would bounce a logged-out visitor to login. On those surfaces the sections simply render without a subtitle — same behavior as today.
- **Copy check:** plain language, no dashes, `→` matches the existing Staff note ("Manage your admin duties →", `:38`).

### 6.6 Unsaved-changes guard (f)

**Scope.** Exactly the three batch-save forms: the notification matrix form (`_notification_matrix.html:16`), the Guided Tours form (`_tour_settings.html:10`), and the Profile form (`user_settings.html:53`). The Guilds tab autosave toggles, the Skills HTMX controls, and the Account tab's email forms are explicitly out — they save immediately or on their own short-form Save.

**Dirty tracking (Alpine, page root).** The settings root `x-data` grows:

```js
{
  tab: '{{ active_tab|default:"guilds" }}',
  dirty: { notifications: false, tours: false, profile: false },
  pending: null,   // { type: 'tab', tab } | { type: 'nav', proceed }
  isDirty() { return this.dirty.notifications || this.dirty.tours || this.dirty.profile },
  go(next) {
    this.pending = null;   // drop any stale held navigation before deciding (never resume an old one)
    if (this.tab === next) return;
    if (this.isDirty()) { this.pending = { type: 'tab', tab: next };
                          this.$dispatch('open-confirm', 'discard-settings-changes'); }
    else { this.tab = next; }
  },
  discard() {
    const p = this.pending; this.pending = null;
    if (p && p.type === 'nav') {
      // The page is about to unload — nothing to reset.
      this.dirty = { notifications: false, tours: false, profile: false };
      p.proceed();
      return;
    }
    if (this.dirty.profile) {
      // Profile edits live partly in Alpine state — a full re-render is the only honest reset (see below).
      // Clear the flags BEFORE navigating: assign() fires beforeunload, and with dirty still set the
      // browser would stack a native "Leave site?" prompt on top of the discard the user just confirmed
      // (cancelling that would strand a half-state). Mirrors the nav branch.
      this.dirty = { notifications: false, tours: false, profile: false };
      window.location.assign('?tab=' + ((p && p.tab) || this.tab));
      return;
    }
    // Reset ONLY the forms whose flag was actually set. A blanket reset of every data-dirty-key form
    // would include a CLEAN profile form — and by this spec's own analysis, reset() forces the
    // x-model master checkbox unchecked while Alpine's `listed` stays true, so the member's NEXT
    // profile Save would silently delist them. Reachable with profile never dirty (e.g. discarding
    // matrix edits on a tab switch), so profile is structurally excluded here: its discard is always
    // the reload branch above.
    ['notifications', 'tours'].filter((k) => this.dirty[k]).forEach((k) => {
      document.querySelector('form[data-dirty-key="' + k + '"]')?.reset();
    });
    this.dirty = { notifications: false, tours: false, profile: false };
    if (p && p.type === 'tab') this.tab = p.tab;
  },
}
```

The root element gets `id="pl-settings-root"` and `@pl-settings-discard.window="discard()"` (the modal is teleported to `<body>`, outside Alpine scope inheritance — a window event is the documented bridge, same as the toast contract).

Each tracked form gets three attributes: `data-dirty-key="notifications"` (resp. `tours`, `profile`), `@input="dirty.notifications = true" @change="dirty.notifications = true"` (both — `change` alone fires only on blur for text inputs), and `@submit="dirty.notifications = false"` (clears the flag **before** the full-page POST navigates, so `beforeunload` never nags on a legitimate Save; this is the locked "clear before navigation" requirement). Alpine scope inheritance makes `dirty` reachable from the nested profile `x-data` — no plumbing needed.

**Profile's Alpine-only controls must arm the guard explicitly (reviewer blocker, part one).** The directory master seg buttons, the per-field visibility pills, and the Hidden/Public buttons are `@click` buttons that mutate Alpine state feeding hidden `:value` inputs — they fire no `input` or `change`, so the form-level listeners alone would never arm the guard and navigating away would drop exactly those edits with no prompt. Same for the contact-row "+ Add" and clone-"Remove" buttons. Fix: each of these controls gains a `data-arm-dirty` attribute — the two `.pl-profile-master__seg-btn` buttons (`user_settings.html:100,110`), every `.pl-vis-toggle` (the shared `hub/partials/_profile_field_with_visibility.html` pill plus the two inline email/photo pills at `:134,155`), the three contact "+ Add" buttons, and the Remove button inside each contact `<template>` — and the profile form gets one delegated listener: `@click="if ($event.target.closest('[data-arm-dirty]')) dirty.profile = true"`. Explicit opt-in markers rather than a broad "any button" selector, so the Discord connect CTA and the photo-delete modal trigger (both inside the form, both acting elsewhere) can never arm a false prompt before their own navigations.

**Bulk buttons set dirty.** `plNotifBulk` flips `.checked` programmatically, which fires **no** `change` event — add one line at the end (`_notification_matrix.html:97-102`):

```js
const form = scope.closest ? (scope.closest('form') || scope) : scope;
form.dispatchEvent(new Event('change', { bubbles: true }));
```

(`scope` is either the `<form>` itself or a `.pl-notif-section` inside it.) On the admin member-edit and token pages the dispatched event finds no Alpine listener and is inert — safe to share.

**The confirm dialog.** Reuse `components/confirm_modal.html` in JS mode, included once in `user_settings.html`:

```html
{% include "components/confirm_modal.html" with confirm_id="discard-settings-changes" confirm_title="Discard unsaved changes?" confirm_message="You have unsaved changes on this page. If you leave now they will be lost." confirm_button_text="Discard Changes" confirm_cancel_text="Stay" confirm_js="window.dispatchEvent(new CustomEvent('pl-settings-discard'))" %}
```

Two-option, as locked: **Stay** (also Escape / click-outside / ×, all existing modal behavior) and **Discard Changes**. This needs one tiny backward-compatible component change: `confirm_modal.html:109` becomes `>{{ confirm_cancel_text|default:"Cancel" }}<` — every existing include is untouched.

**Discard really discards — two mechanisms, because Profile is special (reviewer blocker, part two).** Because tab panes are `x-show` (DOM persists), merely switching tabs would keep the edits lurking, so a discard must genuinely revert. The matrix and Tours forms are plain server-rendered controls whose defaults live in HTML attributes — `form.reset()` restores the rendered `checked` states and is a faithful discard, so a pure matrix/tours discard resets in place and switches tabs client-side. The Profile pane is **not** resettable that way: its Alpine state (`listed`, `vis`, `fields`) feeds hidden `:value` inputs that `reset()` cannot touch, and reset actively *corrupts* it — the master directory checkbox is `x-model="listed"` with **no `checked` attribute** (`user_settings.html:94-99`), so `reset()` forces it unchecked while `listed` stays `true`, Alpine never re-asserts, and the *next* Save would submit `show_in_directory` absent, **silently delisting the member from the directory**. Cloned contact rows would also survive a reset as empty rows. So when `dirty.profile` is set, `discard()` performs a **pane re-render** instead: `window.location.assign('?tab=<target>')` — a full reload restores server truth for every input, every piece of Alpine state, and the live preview, and drops cloned rows; the reload's `_resolve_settings_tab` lands on the target tab. A mixed-dirty discard (profile + matrix) takes the reload path, which resets everything at once. A `type: 'nav'` discard needs no reset at all — the page is about to unload.

**Hard navigation** (sidebar links — `hx-boost="false"`, `base.html:89` — address bar, tab close):

```js
window.addEventListener('beforeunload', (e) => {
  const root = window.Alpine && Alpine.$data(document.getElementById('pl-settings-root'));
  if (root && root.isDirty()) { e.preventDefault(); e.returnValue = ''; }
});
```

Browser-native prompt; no custom UI possible there, by design. Its remaining role is **true hard navigations only** — sidebar links (`hx-boost="false"`), address bar, refresh, tab close. Untracked *boosted* form submits never reach it: the `htmx:confirm` hold below intercepts them first (and the once-cited Add Email-while-matrix-dirty case cannot occur at all — dirty arises only on the pane being edited, and reaching the Account tab passes the tab guard).

**Boosted-link navigation** (the body-level `hx-boost="true"`, `base.html:72`, where `beforeunload` does NOT fire): htmx 2.0.4's cancelable **`htmx:confirm`** event with async resume — this is the precisely-researched mechanism (both `htmx:confirm` and `issueRequest` verified present in the bundled `static/js/htmx.min.js`, version 2.0.4):

```js
document.body.addEventListener('htmx:confirm', (evt) => {
  const rootEl = document.getElementById('pl-settings-root');
  const root = rootEl && window.Alpine && Alpine.$data(rootEl);
  if (!root || !root.isDirty()) return;
  const elt = evt.detail.elt;
  const boostedLink = elt instanceof HTMLAnchorElement;
  const hasHxVerb = ['hx-get', 'hx-post', 'hx-put', 'hx-patch', 'hx-delete']
      .some((a) => elt.hasAttribute(a));           // any explicit verb, not just get/post (future-proof)
  const boostedForm = elt instanceof HTMLFormElement
      && !elt.hasAttribute('data-dirty-key') && !hasHxVerb;
  const crossFormSave = elt instanceof HTMLFormElement && elt.dataset.dirtyKey
      && Object.entries(root.dirty).some(([k, v]) => v && k !== elt.dataset.dirtyKey);
  if (!boostedLink && !boostedForm && !crossFormSave) return;
  root.pending = null;                       // drop any stale held navigation before holding a new one
  evt.preventDefault();                      // hold the request
  // Re-arm the held form's OWN flag: its Alpine @submit already cleared it in this same submit
  // dispatch, but the hold cancelled the POST — without this, a Stay would leave that pane's
  // never-saved edits unguarded AND invisible to a later Discard's flag-filtered reset.
  // No-op for the link and boosted-form branches (no data-dirty-key).
  if (elt instanceof HTMLFormElement && elt.dataset.dirtyKey) root.dirty[elt.dataset.dirtyKey] = true;
  root.pending = { type: 'nav', proceed: () => evt.detail.issueRequest(true) };
  window.dispatchEvent(new CustomEvent('open-confirm', { detail: 'discard-settings-changes' }));
});
```

Why this filter, precisely: `htmx:confirm` fires for *every* htmx request, and `hx-boost` boosts **forms as well as anchors** (`base.html:72`) — so an anchor-only filter would let boosted POSTs navigate the whole page with no `beforeunload` and no hold. Three branches:

- **Boosted links** — every in-hub navigation link.
- **Boosted forms** (no `data-dirty-key`, no explicit hx verb) — the **reachable** beneficiaries are the *same-pane* plain-POST confirm-modal forms: **photo-delete while the Profile is dirty** and **Discord-disconnect while the matrix is dirty** (both are plain `method="post"` forms inside `confirm_modal.html`, boosted by the body). Yes, that means two dialogs in sequence — action confirm, then the discard hold — accepted: rare, and each answers a different question. The cross-*pane* scenario the earlier draft cited (Add Email while the matrix is dirty) is actually **unreachable**: dirty only arises on the pane being edited, and every route to the Account tab passes the tab guard first — so it justifies nothing, but the branch still correctly covers any future boosted form. Exemptions: forms with an explicit hx verb (`hx-get/post/put/patch/delete` — partial-swap forms like Skills don't navigate, nothing is lost) and forms carrying `data-dirty-key` — for their own key (their `@submit` clears it, and gating a form on its own flag would race the Alpine submit listener).
- **Cross-form saves** (reviewer should-fix): a tracked form's Save must still be held when a *different* pane is dirty — otherwise editing matrix toggles and clicking the Tours card's Save would clear only `dirty.tours`, sail through the exemption, and navigate away with the matrix edits silently gone. The `k !== elt.dataset.dirtyKey` condition checks only *other* keys, so the own-key `@submit` race is irrelevant here. On Discard the held Save proceeds (`issueRequest(true)`) and the other pane's edits are knowingly dropped; on Stay nothing is submitted.

The guild autosave toggles are `<input>` elements → no branch → **still exempt** (locked requirement). Hash-only jump chips never reach htmx at all (boost ignores local `#` links). "Discard Changes" resumes the held request via `issueRequest(true)` (the `true` skips htmx re-asking); "Stay" simply drops it — the click is consumed, the user stays put. Both `go()` and this handler null `pending` up front, so a dropped ("Stay") navigation closure can never be resumed later by an unrelated discard.

**A11y / focus management (checklist item + reviewer should-fix):** `confirm_modal.html`'s root `x-data` grows `opener: null`. On open it records `document.activeElement` and moves focus to the Cancel/Stay button (`x-effect="if (open) { opener = document.activeElement; $nextTick(() => $el.querySelector('.pl-btn--secondary')?.focus()) }"`); **every close path** (Stay/Cancel, Escape, ×, click-outside) restores focus to `opener` — without the restore, "Stay" would strand keyboard focus on a hidden element. A full focus **trap** stays explicitly deferred (§10): the page has no trap utility, and Escape-to-close plus focus restore covers the keyboard flow. **Named behavior change:** every existing confirm modal now opens focused on its Cancel button — including the typed-DELETE account modal, which previously left focus wherever the trigger was; the user tabs or clicks into the typed field. A deliberate safe default for destructive confirms, called out in §9 so existing confirm-modal specs get eyeballed. One more deliberate exemption worth naming: the email card's footer cross-link to Notifications is a **button**, not an anchor (`user_settings.html:638`) — it stays a button, which conveniently keeps it out of the `htmx:confirm` anchor branch; it routes through `go()` like every tab switch.

**States walk (f):** clean forms → tabs switch instantly, links navigate, no prompts. Dirty + tab click → modal; Stay keeps edits and tab; Discard resets in place and switches (matrix/tours) or reloads onto the target tab (profile or mixed). Dirty + boosted link, same-pane confirm-modal POST (photo-delete, Discord-disconnect), or a *different* tracked form's Save (cross-form) → modal; Discard resumes the held navigation/submit. Dirty + sidebar/close/refresh → native browser prompt. Save on any tracked form → flag cleared pre-submit, normal redirect + Django success message, no prompt. Guild toggle flips → no flags, no prompts, existing toast/error-revert behavior. **Accepted limitation, stated rather than implied away (reviewer should-fix):** the browser Back/Forward buttons through htmx's history restoration bypass *both* guards — restoration replays a cached snapshot with no unload event and no htmx request, and `popstate` is not cancelable. Dirty edits are lost without a prompt on Back. Accepted for this round (§10); the guard covers clicks, submits, tab switches, refresh, and tab-close.

### 6.7 View-as walk (a) — what each viewer sees

| Viewer | Staff & Leadership section | Save while in this state |
|---|---|---|
| Plain member (incl. guild lead by `led_guilds`) | Eligibility-filtered as today (leads see their rows) | Unchanged |
| Admin / officer, viewing as self | Rendered — now **last** | Full save incl. staff rows |
| Admin / officer, **viewing as Member or Guest** | **Omitted entirely** (flag `False`) | Staff-event rows untouched (§5.2) |
| Admin viewing as Guild Officer | Rendered (previewing a staff role) | Full save |
| Token email-prefs page / admin member-edit | Unchanged (default `True`) | Unchanged |

Also omitted with the section: its jump chip (chips derive from rendered sections) and the `capabilities_url` note inside it. The `capabilities_url` context computation (`hub/views.py:2160-2164`) can stay as is — the template only renders it inside the staff section.

## 7. Notifications / emails / activity

None sent or changed by this feature. The only spine-adjacent effect is the `X-Category` header shift for the two re-tagged orientation events (§4). No new `SiteActivity` kinds.

## 8. Build order (phased; each phase ships green)

1. **Matrix layer** — `include_staff_section` flag through `_visible_events` / `visible_channels` / `build_matrix` / `save_matrix`; new `CATEGORY_ORDER`; staff-last `_ordered_categories` (+ docstring/comment truth-up); `core/triggers.py` re-tags. Specs for all of it (§9). Run `manage.py check` (registry-adjacent change).
2. **View + tab plumbing** — view-as flag in `user_settings` (GET + POST paths); `_resolve_settings_tab` default/alias/whitelist + the POST `form_id` tab derivation (§5.3 blocker fix); `HubEmailView` redirect; tab-bar reorder in the template (still plain `@click` at this phase). Specs for tab resolution (incl. the failed-profile-POST pin) + view-as rendering. Update the §9 existing-test inventory in the same phase.
3. **Account tab** — move the email card, delete the Emails pane/button, danger-zone markup + CSS, copy fixes. Template-render specs.
4. **Notifications pane** — jump chips + anchors + `scroll-margin-top`, subtitles (d), back-to-top button + CSS + mobile clearance.
5. **Dirty guard** — page `x-data` growth, `go()` rewiring of every tab-switch point (4 tab buttons, `_my_guilds.html:43`, the relocated email-card footer button, the new Guilds subtitle), form attributes + `data-arm-dirty` markers, the Profile reload-discard path, `plNotifBulk` change-dispatch, `beforeunload` + `htmx:confirm` (links **and** boosted forms) listeners, discard modal include, `confirm_modal` `confirm_cancel_text` + open-focus/restore. Run `tests/template_comment_lint_spec.py` with the template phases.

> Spec only — do not build until approved. Versioning/changelog is handled by the release pipeline and is explicitly not part of this spec.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — it silently collects nothing), factory-boy, existing fixtures from `tests/hub/notification_settings_spec.py` and `tests/hub/my_guilds_spec.py`.

**Matrix ordering + re-tag** (`tests/core/notification_matrix_ordering_spec.py`, new):
- Section order for a plain member starts `Orientations, Guilds, Events` and never contains `Staff & leadership`.
- For an eligible admin, `Staff & leadership` is the **last** section.
- `orientation_update` renders under Orientations (not Guilds); `orientation_requested` lands in the staff section for an orienter.
- Re-tag safety: a saved `NotificationPreference` row for `orientation_update` still round-trips through `build_matrix` → checked cell → `save_matrix` unchanged (the event-key evidence, executable).
- An unknown/extra category still falls to the end, alpha, before staff.

**View-as omission + save skip** (extend `tests/hub/notification_settings_spec.py`):
- `build_matrix(user, include_staff_section=False)` yields no staff section and no staff-only channel columns.
- Admin with session `view_as_role = "member"`: GET `/settings/` response context matrix has no staff section; **POST the notifications form in that state and assert the admin's pre-existing staff-event `NotificationPreference` rows are untouched** (the §5.2 wipe trap, pinned).
- Officer previewing member: hidden. Guild lead (fog_role member, `led_guilds`): still shown. Admin viewing as officer: shown.
- Token flow and admin member-edit still render/save staff rows (default flag).

**Tab resolution** (`tests/hub/user_settings_tabs_spec.py`, new):
- No `?tab` → `active_tab == "guilds"`; garbage `?tab=<script>` → `"guilds"`; `?tab=emails` → `"account"`; each surviving value passes through.
- Bare `/settings/` GET stamps `guild_updates_prompt_answered_at` (the deliberate side effect, pinned as intended); second GET is a no-op.
- **Failed profile POST re-renders with `active_tab == "profile"` and does NOT stamp `guild_updates_prompt_answered_at`** (the reviewer-blocker pin — errors visible, no wrong-write).
- allauth `account_email` GET redirects to `/settings/?tab=account`.

**Templates** (render assertions in the same spec files):
- Emails pane gone; email card renders inside the Account pane above `.pl-danger-zone`; danger card contains the typed-DELETE confirm include unchanged.
- Chips: one `pl-notif-jump__chip` per section with matching `#notif-<slug>` anchor ids; no staff chip for a plain member.
- Subtitles: Guilds + Meetings notes present when `matrix_self` and no token; absent on the token page and admin member-edit render.
- `confirm_modal` with `confirm_cancel_text="Stay"` renders "Stay"; without the param still renders "Cancel" (backward-compat pin). Existing confirm-modal specs re-run to catch the `x-effect` focus addition.
- Guard markup pins: `data-dirty-key` on exactly three forms; `data-arm-dirty` present on the seg buttons, the vis pills (shared partial + the two inline ones), and the contact add/remove controls; the discard modal include and the `plNotifBulk` change-dispatch line present in the rendered page.
- `tests/template_comment_lint_spec.py` (multi-line `{# #}` guard) — run after every template phase.

**Existing tests to update (verified breakages — reviewer inventory):**
- `tests/core/events/settings_matrix_spec.py:127` `it_sees_the_section_for_admin_alerts_rendered_first` — pins staff FIRST; inverted by this spec. Rename/re-assert to *rendered last*.
- `tests/hub/views_spec.py:317-323` `it_defaults_to_profile_tab` → defaults to `guilds`.
- `tests/hub/views_spec.py:331` `it_honors_tab_query_param` — asserts `emails` survives the whitelist; now asserts the `emails → account` alias.
- `tests/hub/views_spec.py:334-342` `it_falls_back_to_profile_when_tab_param_is_not_whitelisted` → falls back to `guilds`.
- `tests/hub/views_spec.py:687,702` — allauth redirect assertions `== "/settings/?tab=emails"` → `?tab=account`.
- `tests/hub/my_guilds_spec.py:261` — asserts `active_tab == "profile"` → `guilds`.

**Not server-testable, stated honestly:** the dirty-guard runtime behavior (Alpine flags, `beforeunload`, `htmx:confirm` interception, the reset/reload discard, back-to-top scroll listener) is browser JS. Server-side we pin what we can: the guard *markup* (`data-dirty-key` on exactly three forms, `@submit` clears, the discard modal include, `plNotifBulk`'s dispatch line present in the rendered page). The rest is a manual QA checklist in the PR: dirty→tab-switch modal both outcomes, profile discard reloads onto the target tab (and the master directory toggle survives a discard round-trip un-corrupted), Alpine-only controls (seg buttons, vis pills, contact add/remove) arm the guard, dirty→sidebar native prompt, dirty→boosted link held + resumable, photo-delete confirm while Profile is dirty and Discord-disconnect confirm while the matrix is dirty both held, Tours Save while the matrix is dirty held (cross-form), cross-form hold → Stay → tab-switch Discard resets BOTH panes (the re-armed own flag), guild toggle immunity, bulk-button dirtiness, Save-no-prompt, focus lands on Stay and returns to the opener on close, both themes, phone-width FAB clearance on Guilds/Notifications/Account, reduced-motion instant scrolling.

## 10. Open / deferred / out of scope

- **Auto-save-on-leave** (third option in the discard dialog) — explicitly out; two-option guard is locked.
- **Back/Forward through htmx history restoration** — not guardable (`popstate` isn't cancelable, restoration makes no request); dirty edits are lost without a prompt on Back. Accepted limitation, stated in §6.6's states walk.
- **Focus trap in the confirm modal** — deferred; open-focus + focus-restore ship (§6.6), a full trap needs a utility the codebase doesn't have and Escape-to-close covers the exit.
- **Profile tab redesign** — untouched beyond the guard attributes and one hint-copy fix.
- **Email management forms in the dirty guard** — out; they save immediately per action, and no reachable state submits them while another pane is dirty (dirty arises only on the pane being edited; every route to Account passes the tab guard). The same-pane confirm-modal POSTs that *are* reachable while dirty — photo-delete, Discord-disconnect — are covered by the boosted-form hold (§6.6).
- **Renaming `?tab=` values or URLs** — the `emails→account` alias is the only routing change; nothing else moves.
- **Security category** — kept in `CATEGORY_ORDER` though no event currently declares it; costs nothing, self-heals when one appears.
- **hub/CLAUDE.md staleness** — it still lists `hub_profile_settings` / `hub_email_preferences` URLs that no longer exist in `hub/urls.py`; a one-line doc tidy can ride along with the build PR but is not part of this feature.
