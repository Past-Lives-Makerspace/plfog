# Member-Submitted Guild Announcements — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Base:** release-0.20.x (v0.20.1).
**Date:** 2026-07-03
**Surface:** FOG hub (`pastlives.test`) — the public guild page (`/guilds/<slug>/`) and the guild edit page's **Announcements/Emails** tab (`/guilds/<pk>/edit/?tab=announcements`). No book CMS, no admin.
**Related:**
- `docs/superpowers/plans/2026-07-03-guild-announcement-discord-channel-picker.md` — the sibling spec, revised in parallel. It **persists** `discord_channel = CharField(choices=GuildAnnouncement.DiscordChannel)` on `GuildAnnouncement` and **retires** the `post_to_discord` boolean, turning the Discord destination into a per-post channel choice rendered by `partials/_announcement_channel_picker.html`. This feature composes with it at **approval time**: the lead's approve form carries that same channel picker, `approve()` persists the chosen `discord_channel`, then calls `GuildAnnouncement.notify_members()` (still no-arg on 0.20.x — it reads the row's own switches). This plan neither re-introduces `post_to_discord` nor duplicates the picker; it reuses the partial and the no-arg `notify_members()` signature. The two ship independently and compose.
- `docs/superpowers/plans/2026-06-21-guild-orientations.md` — the confirm/decline UX precedent this mirrors for a lead accepting/declining a member request.

---

## 1. Summary

Today only a guild's lead or staff can post an announcement, and their posts go live the instant they hit **Post Announcement** — there is no review step and no way for a rank-and-file member to contribute. This feature lets **any member of a guild** compose an announcement and **submit it to the guild's leadership for review** from the guild page. A lead sees the pending submission on the guild edit page, and either **Approves** it (optionally editing the wording first, and picking the Discord channel) — which posts it live through the exact same fan-out leads' own posts use (in-app bell, opt-out email, Discord) — or **Rejects** it with a short note explaining why. Either way the submitter is notified. Leads and staff keep posting directly with no review, exactly as today.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Who may submit | **Any member with a `GuildMembership` row** for that guild (`guild.memberships.filter(member=…).exists()`, the already-computed `is_member_of_guild`). Not directory-privacy filtered — belonging to the guild is the only test. |
| Who reviews | The guild's **leadership** — lead + all staff — via the existing `Recipients.GUILD_LEADERSHIP` resolver (`Guild.leadership_members()`). No new resolver. |
| Lead direct-post path | **Unchanged.** Leads/staff keep the existing "Post an Announcement" form; their posts are `PUBLISHED` immediately with no review. |
| Approve can edit first | Yes — the Approve control opens the submission in an editable form; the lead may fix wording/expiry (and pick the Discord channel) before it posts. |
| Posting reuses the fan-out | On approve, publication calls the existing **`GuildAnnouncement.notify_members()`** (no-arg — it reads the row's own `send_email` / `discord_channel`), so the fan-out — including the sibling spec's Discord **channel picker** — is inherited. No new posting path. |
| Lifecycle | Add a `status` to `GuildAnnouncement` (it has none today). Model it on the class dual-approval pattern (`ClassOffering.Status` + `ClassApproval`). |
| Status set | **`PENDING` / `PUBLISHED` / `REJECTED`** — no persisted `DRAFT`. Rationale in §4 and §10. |
| Review gate shape | A **single in-hub gate** (fields on the row: `reviewed_by` / `reviewed_at` / `review_note`), **not** a per-role `ClassApproval`-style table with login-free tokens — reviewers are guild leadership already logged into the hub, so no emailed token is needed. |
| Rejection | Terminal. The submitter composes a fresh submission if they want to try again (the compose form is one click away). No edit-my-rejected-submission surface. |

## 2. What already exists (reuse, don't reinvent)

All symbol locations below are **release-0.20.x** line numbers.

| Need | Existing thing | Location (0.20.x) |
|---|---|---|
| The announcement model + live fan-out | `GuildAnnouncement` (guild, author, title, body, `published_at` `auto_now_add`, `expires_at` **`DateField`**, persisted `send_email` / `post_to_discord` booleans); `notify_members()` **no-arg** (reads `self.send_email`/`self.post_to_discord`; emits `guild_announcement` → in-app + opt-out email + Discord, deduped by `period=f"announcement:{pk}"`) | model `membership/models.py:1630`; `send_email:1656`; `post_to_discord:1660`; `notify_members:1678` |
| Announcement queryset | `GuildAnnouncementQuerySet` with `active()` (**expiry-only today — no status filter**) and `for_member()` | queryset `membership/models.py:1620`; `active():1621`; `for_member():1625` |
| `is_active` (expiry only) | property used for the "Expired" badge | `membership/models.py:1674` |
| Lead direct-post / edit / delete views + gate | `guild_announcement_create:1730`, `guild_announcement_edit:1970`, `guild_announcement_delete:1785`; all gated by `_require_can_edit_guild:569` | `hub/views.py` |
| Guild edit page + its context | `guild_edit:658`; `_guild_edit_context:605` (supplies `announcement_form` at `:641`) | `hub/views.py` |
| Announcements tab UI (post form, list, edit-modal, confirm-delete) | `guild_edit.html` `x-show="section === 'announcements'"` block at `:472` (tab label "Announcements/Emails"); the `x-data` reads the `?tab=` query param into `section` (`:5`); Post form `:473`; Recent Announcements card `:487` with its list `<div>` at `:489` iterating `{% for a in guild.announcements.all %}` `:490`; **the edit/delete modal-generation loop at `:521` sits OUTSIDE the form and the `x-show` block** and mints one `confirm_modal` (`del-ann-<pk>`) + one `modal` (`edit-ann-<pk>`) per row | `templates/hub/guild_edit.html` |
| Row + edit-form partials | `_guild_announcement_row.html` (row id **`announcement-row-<pk>`**; `oob` param → `hx-swap-oob="true"` id swap; container `display:flex; justify-content:space-between; …flex-shrink:0` with **no `flex-wrap`**; small buttons are `pl-btn pl-btn--sm` **plus an inline `min-height:unset; padding:…` size hack**); `guild_announcement_edit_form.html` | `templates/hub/partials/…` |
| The 3-payloads-in-one-response HTMX trick to copy | `guild_announcement_edit` POST returns the OOB row swap (body) + `trigger_toast` (`HX-Trigger`) + `close-modal` (`HX-Trigger-After-Settle`); invalid → re-renders the form partial into the modal body | `hub/views.py:1970` |
| Public announcements display (already uses `.active()`) | `announcements = guild.announcements.active()[:5]` | `hub/views.py:489` → `guild_detail.html` |
| Guild activity feed (also uses `.active()`) | `guild.announcements.active().order_by("-published_at")[:limit]` | `hub/views.py:409` |
| "Get Involved" member entry point (Join, Teach a Class, Contact) + `is_member_of_guild` / `can_edit_this_guild` already in context | `guild_detail.html:239` (Get Involved card); Join `<form>` `:246`; context at `hub/views.py:475` (`can_edit_this_guild`) / `:494` (`is_member_of_guild`) | — |
| Where guild_detail's page-level modals mount (outside the Join form) | the guild-lead-profile / EYOP modals at `guild_detail.html:535` / `:686` | — |
| Review pattern to mirror (statuses, submit→review→decide→lifecycle, lead review queue) | `ClassOffering.Status` `classes/models.py:195`; `submit_for_review()` `:421`; `ClassApproval.decide()` `:985`; `on_review_decision_recorded()` `:563`; queue `awaiting_guild_lead()` `classes/models.py:145` + `_guild_lead_review_queue()` `classes/views.py:937` + UI `templates/classes/teach/overview.html:5` (heading count span `overview-title__count` `:8`; empty state `:19`) | `classes/…` |
| Notify guild leadership | `guild_leadership` resolver (`Recipients.GUILD_LEADERSHIP`) — lead + all staff, deduped, reuses `Guild.leadership_members()` (`membership/models.py:1258`) | resolver `core/events/resolvers.py:125`; map row `:437` |
| Notify a single member (the submitter) | `single_user` resolver (`Recipients.SINGLE_USER`) — wraps an explicit `user` in context | resolver `core/events/resolvers.py:422`; map row `:454` |
| Event registration + copy plumbing | `_NEW_EVENTS` list `core/events/registry.py:334`; recipient map `_TRIGGER_RESOLVERS:183`; activity-kind map `_TRIGGER_ACTIVITY_KINDS` (guild_announcement → `"guild_announcement"`); the seeded `guild_announcement` EventType at `:386`; `EventCopy` map `_CURATED` `core/events/copy.py:118` (`EventCopy` class `:48`; `guild_announcement` entry `:316`); seeded to DB by `seed_notification_templates` | `core/events/…` |
| Membership test / gate | `guild.memberships.filter(member=member).exists()` (already `is_member_of_guild`) | `hub/views.py:494` |
| Field-wrapper + input theming | `components/form_field.html` wraps every field in **`.pl-form-group`** (`static/css/components.css:448`); that scope themes inputs via `--hub-input-bg`/`--hub-text` (`:465`), `select option` (`:500`), and **date/time inputs via `color-scheme` for both themes** (`:508`–`:511` dark, `:972`–`:975` light) | `templates/components/…`, `static/css/components.css` |
| Small / danger button classes | `hub-btn` `static/css/hub.css:905`; `hub-btn--primary:920`; `hub-btn--danger:930`; `hub-btn--sm:942`; `hub-btn--ghost:949` | `static/css/hub.css` |
| Absolute-URL helper for emails | `_absolute_url(reverse(…))` (used by `notify_members`) | `membership/orientations.py:32` |
| Editable-list / confirm / modal / toggle / toast components | `form_field.html`, `modal.html`, `confirm_modal.html`, `toggle.html`, `trigger_toast()` | `templates/components/…`, `hub/toast.py` |

**Gaps to close (kept small):** (1) three fields + a status on `GuildAnnouncement` and a matching migration; (2) two model methods (`approve`, `reject`) + submission-notice methods, and two queryset methods (`.pending_for()`, `.active()` gains a status filter); (3) one member-gated submit view + two lead-gated decision views; (4) three spine events + their copy; (5) the submit modal on the guild page and the review-queue card + two decision modals on the edit page; (6) scoping the edit page's Recent-list loop **and** its modal-generation loop to PUBLISHED, and adding `id="recent-announcements"` to the Recent list container.

## 3. Where the code lives

Everything stays inside the already-covered `membership`, `hub`, and `core.events` scopes — no new app.

```
membership/
  models.py                 # + GuildAnnouncement.Status, 3 review fields, approve()/reject()/notify_*,
                            #   queryset .active() (adds status filter) + .pending_for()
  migrations/00XX_….py      # add status(+default PUBLISHED backfill), reviewed_by/at, review_note
  spec/models/guild_announcement_spec.py   # new/expanded

hub/
  forms.py                  # GuildAnnouncementSubmitForm, GuildAnnouncementRejectForm
                            #   (reuse GuildAnnouncementForm — hub/forms.py:1106 — for the approve-with-edit form)
  views.py                  # guild_announcement_submit (member-gated),
                            #   guild_announcement_approve, guild_announcement_reject (lead-gated);
                            #   pending_submissions + published_announcements added to _guild_edit_context (:605)
  urls.py                   # 3 new url names
  spec/views/guild_announcement_review_spec.py

templates/hub/
  guild_detail.html         # "Submit an announcement" button in Get Involved (:239) + submit modal
                            #   mounted at page bottom, OUTSIDE the Join form (near :535/:686)
  guild_edit.html           # "Awaiting your review" card above Recent Announcements; add id="recent-announcements"
                            #   to the Recent list <div> (:489); scope the Recent loop (:490) AND the
                            #   modal-generation loop (:521) to PUBLISHED; two decision modals alongside that loop
  partials/
    guild_announcement_submit_form.html   # member submit form (HTMX into modal)
    _guild_submission_row.html            # one pending-submission row (Approve / Reject)
    guild_announcement_approve_form.html  # editable approve form (reuses form_field.html + the channel picker)
    guild_announcement_reject_form.html   # required-note reject form

core/events/
  registry.py               # 3 EventType entries in _NEW_EVENTS (:334) + _TRIGGER_RESOLVERS rows (:183)
                            #   + _TRIGGER_ACTIVITY_KINDS = None
  copy.py                   # 3 EventCopy entries in _CURATED (:118) (in-app + email, .txt + .html parity)

plfog/version.py            # VERSION bump + CHANGELOG entry
```

No new CSS file — reuse `hub.css` classes (`hub-card`, `hub-btn`/`hub-btn--sm`/`hub-btn--primary`/`hub-btn--danger`, `hub-text-muted`, `hub-badge`) and the `.pl-form-group` field scope that `form_field.html` already emits. Any genuinely new class takes the `pl-` prefix and lands in `hub.css`.

## 4. Data model

### `GuildAnnouncement` — new lifecycle

Add a `Status` and the review metadata **alongside** the fields already on the model in 0.20.x (`published_at`, `expires_at`, `send_email`, `post_to_discord`). The sibling spec's `discord_channel` column, when it lands, is orthogonal — the two migrations are additive and independent. Mirror `ClassOffering.Status`/`ClassApproval` semantics, condensed to a single in-hub gate.

```python
class Status(models.TextChoices):
    PENDING = "pending", "Pending review"      # member submitted; not public yet
    PUBLISHED = "published", "Published"        # live on the guild page + fanned out
    REJECTED = "rejected", "Rejected"           # a lead declined; DB record only (§6 Screen C, §10)
```

| Field | Type | Note |
|---|---|---|
| `status` | `CharField(max_length=20, choices=Status.choices, default=Status.PUBLISHED)` | Default **PUBLISHED** so every existing row and every lead direct-post stays live with zero behavior change. help_text: "Whether this announcement is live, awaiting a lead's review, or was rejected." |
| `reviewed_by` | `FK(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | The lead/staff user who approved or rejected. Null for lead direct-posts (no review happened). |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | When the decision was recorded. |
| `review_note` | `TextField(blank=True, default="")` | The lead's note to the submitter — **required on reject**, optional on approve. Surfaced in the rejection email. |

`author` (FK to `AUTH_USER_MODEL`, `SET_NULL`, `membership/models.py:1640`) keeps its meaning ("who wrote/posted it") and, for a member submission, is set to the **submitter's** user — so the row is attributable and `single_user` notifications can address them via `announcement.author`. `published_at` stays `auto_now_add` for the create path; on **approve** it is re-stamped to `timezone.now()` (see §5) — because `auto_now_add` only sets on insert, the approve path writes it explicitly in `update_fields` — so the public page dates and orders it by when it actually went live, not when it was submitted.

**Migration:** additive columns; `status` ships with `default="published"`, which backfills all existing rows to PUBLISHED (correct — they're all live today). No data migration needed beyond the column default; the auto-generated reverse (drop the four columns) is a real reverse — no `RunPython`, so nothing to hand-write. Ships in one migration, independent of the sibling's `discord_channel` migration.

### Queryset changes (`GuildAnnouncementQuerySet`, `membership/models.py:1620`)

```python
def active(self):
    # unchanged expiry window (currently the whole body of active() at :1621),
    # PLUS: only PUBLISHED rows are ever public
    return self.filter(status=GuildAnnouncement.Status.PUBLISHED).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=timezone.localdate())
    )

def pending_for(self, member):
    """PENDING submissions in guilds this member leads or holds a staff role on."""
    return self.filter(
        status=GuildAnnouncement.Status.PENDING,
    ).filter(
        Q(guild__guild_lead=member) | Q(guild__staff_memberships__member=member)
    ).distinct().order_by("published_at")
```

`.active()` gaining the `status=PUBLISHED` filter is the **one backward-compat-sensitive line**. **Leak audit — every `.active()` consumer stays correct with no template change:**
- `hub/views.py:489` — `guild.announcements.active()[:5]` (public guild page). ✅ PENDING/REJECTED now excluded.
- `hub/views.py:409` — `guild.announcements.active().order_by("-published_at")[:limit]` (guild activity feed). ✅ same — a pending/rejected submission never appears in the pulse feed.
No other caller of `.active()` exists on `GuildAnnouncement`. `.pending_for()` mirrors `ClassOfferingQuerySet.awaiting_guild_lead()` (`classes/models.py:145`) — same lead-or-staff `Q` shape (`guild_lead` FK `:923`, `staff_memberships` related_name `:1294`) — and drives the review queue.

`is_active` (`membership/models.py:1674`) stays as-is (expiry only) — it's used on the edit page's own "Expired" badge, which should still read regardless of status.

## 5. Business logic (fat models)

Views stay thin — a member-submit view, an approve view, and a reject view each parse the request, call a form + a model method, and return feedback. All state changes live on the model, mirroring `ClassApproval.decide()` → `on_review_decision_recorded()`.

- **Submit** — no new model method needed; the submit *view* creates the row directly (`status=PENDING`, `author=submitter.user`, `guild=…`) via `GuildAnnouncementSubmitForm.save(commit=False)`, then fires the review-requested event. (Creation *is* the submit, exactly as the lead create view at `hub/views.py:1730` already builds a row; keeping a `submit()` method thin here would just wrap `create` + `emit`, so instead the notification is a one-liner in the view calling `announcement.notify_leadership_of_submission()` — see below — to keep the emit off the view.)

- `GuildAnnouncement.notify_leadership_of_submission() -> None`
  Emits `guild_announcement_submitted` to `Recipients.GUILD_LEADERSHIP` (context carries `guild`, submitter name, title, body, and the absolute URL to the review tab). `period=f"guild_ann_submitted:{self.pk}"`. Called once by the submit view after save.

- `GuildAnnouncement.approve(self, reviewer: User) -> None`
  Guard: `if self.status != Status.PENDING: raise ValueError`. Sets `status=PUBLISHED`, `reviewed_by=reviewer`, `reviewed_at=now`, `published_at=now` (re-stamp), saves. Then **`self.notify_members()`** — the existing **no-arg** live fan-out, which reads the row's own `send_email` and (post-sibling) `discord_channel` — **and** `self._notify_submitter_approved()`. The inline edits (title/body/expiry/`send_email`/`discord_channel`) are already persisted by the approve *form* before `approve()` runs, so `notify_members()` sends the final wording to the chosen Discord channel. **Do not pass or set `post_to_discord`** — the sibling spec retires it; the channel picker is the Discord control.

- `GuildAnnouncement.reject(self, reviewer: User, note: str) -> None`
  Guard: `status == PENDING` else `ValueError`; `note` non-empty is enforced by the form's `clean_review_note`. Sets `status=REJECTED`, `reviewed_by`, `reviewed_at`, `review_note=note`, saves. Then `self._notify_submitter_rejected()`. Does **not** fan out to the guild (nothing was posted).

- `_notify_submitter_approved()` / `_notify_submitter_rejected()`
  Emit `guild_announcement_approved` / `guild_announcement_rejected` to `Recipients.SINGLE_USER` with `context={"user": self.author, "guild": self.guild, "guild_name": …, "announcement_title": …, "guild_url": …, "review_note": self.review_note}`. Unique periods `f"guild_ann_approved:{self.pk}"` / `f"guild_ann_rejected:{self.pk}"`.

Domain guard uses `ValueError` at the model boundary; the views translate a non-PENDING race (two leads acting at once) into a friendly toast rather than a 500 (see §6 error state).

## 6. UI / UX  ← completeness checklist applied per screen

Three screens: (A) the member's **Submit** modal on the public guild page; (B) the leads' **review queue + decision** modals on the edit page; (C) the small surface changes that make status legible. Every form below has a named, visibly-present submit button; every list its states.

### Screen A — "Submit an announcement" (public guild page, `guild_detail.html`)

- **Entry point:** in the **Get Involved** card (`guild_detail.html:239`), add a button **only** for a member who's in the guild but can't already post directly:
  `{% if is_member_of_guild and not can_edit_this_guild %}` → a full-width `pl-btn pl-btn--secondary` **"Submit an announcement"** (megaphone icon, matching the sibling buttons' inline-SVG style). Leads/staff (`can_edit_this_guild`) don't see it — they use the edit page's direct-post form. Non-members and logged-out users don't see it.
- **Nested-form safety (P2):** the trigger button lives in the Get Involved list, but the **modal must mount at the page bottom, OUTSIDE the "Join This Guild" `<form>`** (`guild_detail.html:246`–`:249`) — alongside the existing page-level modals near `:535`/`:686`. This repo has a logged "Save does nothing = a nested `<form>` orphans the submit button" bug; the submit modal contains its own `<form>`, so it may not sit inside the Join form.
- **Container:** `components/modal.html` (`modal_size="md"`), opened with `@click="$dispatch('open-modal', 'submit-ann')"` and HTMX-loading `guild_announcement_submit_form.html` into `#submit-ann-body`. Three fields (title, body-textarea, expiry) with a substantial textarea → a modal is the right weight (they stay on the guild page); it matches the existing announcement **edit** modal precedent.
- **Form partial (`guild_announcement_submit_form.html`) — `GuildAnnouncementSubmitForm`:**
  - Fields via `components/form_field.html`: `title`, `body` (textarea), `expires_at`. **No `send_email`/`discord_channel` here** — those fan-out choices belong to the lead at approval, not the member. A short `field_hint` on `body`: "A lead will review this before it's posted to the guild." Expiry hint: "Optional — the last day it shows on the guild page."
  - **Submit button (named, present):** a visible `pl-btn pl-btn--primary` labeled **"Submit for review"** at the bottom of the form, plus a secondary **"Cancel"** (`@click="$dispatch('close-modal', 'submit-ann')"`). Posts `hx-post` to `hub_guild_announcement_submit`, **`hx-target="#submit-ann-body" hx-swap="innerHTML"`** (P1 — so a validation re-render lands somewhere; see States).
  - **Dark/light:** every control flows through `form_field.html`, which wraps it in **`.pl-form-group`** (`components.css:448`). That scope already themes the background (`--hub-input-bg`), text (`--hub-text`), `select option`, and — for the `expires_at` **`<input type="date">`** — the native picker via **`color-scheme`** (dark `components.css:508`–`:511`, light `:972`–`:975`). There are **no raw inputs and no white-box risk**, and **no Rule-14 `filter:invert` treatment is needed** — the date axis is already clean for both themes. (If click-anywhere-to-open is later wanted, wire `showPicker()` through the widget `attrs` in the form, not via a bespoke class; not in scope for v1.)
  - **States:**
    - *Success:* view returns **`204`** + `trigger_toast(response, "Sent to your guild leads for review.", "success")` and an `HX-Trigger-After-Settle` `close-modal: submit-ann`. A 204 performs no swap even with a target set, so the toast + close fire and the modal empties/closes cleanly. Same one-response pattern as `guild_announcement_edit` (`hub/views.py:1970`). No page nav.
    - *Validation error* (blank title/body): view re-renders the form partial (200) into **`#submit-ann-body`** (the `hx-target`), so `form_field.html` field errors show and the modal **stays open** (no close trigger). Message is Django-form-standard ("This field is required.").
    - *Empty state:* n/a (it's a create form). *Loading:* HTMX button shows its in-flight state; the modal body shows the loaded form.
- **Mobile:** the modal is full-width on narrow screens (existing `modal.html` behavior); textarea and date field reflow; buttons stack via existing modal footer styles.

### Screen B — Review queue + decisions (guild edit page, `guild_edit.html`, Announcements/Emails tab)

This is where a lead already manages announcements, so the queue lives here — directly above "Recent Announcements", inside `x-show="section === 'announcements'"` (`:472`).

- **New card: "Awaiting your review"** (`hub-card`), rendered from a `pending_submissions` context list (`guild.announcements.pending_for(member)`, added in `_guild_edit_context`, `hub/views.py:605`):
  - Heading with a count, mirroring the class queue: `Awaiting your review ({{ pending_submissions|length }})` (the class queue's `overview-title__count` span at `overview.html:8` is the precedent; a plain parenthetical is fine here).
  - **Empty state:** `<p class="hub-text-muted">No submissions waiting — you're all caught up.</p>` (mirrors the class-queue empty copy at `overview.html:19`, minus the emoji to fit the tab's tone). Present and named.
  - **Each row (`_guild_submission_row.html`, `id="submission-row-{{ s.pk }}"`):** title (bold), a muted line "Submitted by {{ s.author…display_name }} · {{ s.published_at|date:'M j, Y' }}", and a 2–3 line body preview (`{{ s.body|truncatewords:40|linebreaksbr }}`). Container: `display:flex; justify-content:space-between; gap:0.75rem; **flex-wrap:wrap**` with the action group `flex-shrink:0`. Two controls, right-aligned, `gap:0.5rem` — **small buttons per this repo's button standards** (FRONTEND.md; MEMORY `feedback_button_standards`), the same convention the class review queue uses (`hub-btn hub-btn--sm` at `overview.html:14`–`:15`):
    - **Approve** — `hub-btn hub-btn--sm hub-btn--primary` (`hub.css:942`/`:920`), opens a modal (`open-modal: approve-sub-{{ s.pk }}`) that HTMX-loads the editable approve form. (Not `pl-btn--sm` — the row buttons must render small; the repo's small/danger convention is `hub-btn--sm`/`hub-btn--danger`, both in `hub.css`.)
    - **Reject** — `hub-btn hub-btn--sm hub-btn--danger` (`hub.css:942`/`:930`), opens a modal (`open-modal: reject-sub-{{ s.pk }}`) with the note form.
    (Reject is destructive-ish but *note-required*, not a silent delete, so it uses its own modal form rather than `confirm_modal.html` — the note is the point.)
- **Nested-form safety (P2):** the approve/reject modals each carry a `<form>`, so they **must mount OUTSIDE the "Post an Announcement" `<form>`** — alongside the **existing** edit/delete modal-generation loop at `guild_edit.html:521` (which is already outside both the Post form and the `x-show` block). Add the two decision modals to that same out-of-form region, generated per pending submission.
- **Approve modal (`guild_announcement_approve_form.html`, reuses `GuildAnnouncementForm` at `hub/forms.py:1106`):**
  - Pre-filled editable `title`, `body`, `expires_at`, the `send_email` toggle, and **the shared Discord channel picker** (`partials/_announcement_channel_picker.html` from the sibling spec — default resolved as in that spec) via `form_field.html`. The lead can fix wording/expiry and pick where it posts before it goes out. A muted line at top: "Posting this notifies everyone in {{ guild.name }} (in-app, email, and the Discord channel you pick below)."
  - **Submit button (named, present):** visible `pl-btn pl-btn--primary` **"Approve & Post"**; secondary "Cancel" closes the modal. `hx-post` → `hub_guild_announcement_approve`, **`hx-target="#approve-sub-{{ s.pk }}-body" hx-swap="innerHTML"`** (P1 — so a validation re-render lands in the modal body).
  - *Success:* the view saves edits (persisting `send_email` + `discord_channel`), calls `announcement.approve(request.user)` (→ no-arg `notify_members()` + submitter notice), and returns a **200 whose body is OOB-only** — no primary content — so HTMX empties the (about-to-close) modal body and flies the OOB parts to their targets:
    1. **the newly-published row appended to the Recent list** via `_guild_announcement_row.html` rendered with an OOB-append directive **`hx-swap-oob="beforeend:#recent-announcements"`** (P1 — a plain id swap would find no target because a just-published row was never in that list; `beforeend` into the id'd container is what makes it appear without a reload; on the next page load Meta `ordering = ["-published_at"]` re-sorts it to the top);
    2. **an OOB removal of the pending row** — `<div id="submission-row-{{ s.pk }}" hx-swap-oob="true"></div>` empties it (that element exists, so the id swap succeeds);
    3. `trigger_toast(response, "Posted to the guild.", "success")` (`HX-Trigger`);
    4. `close-modal` on `HX-Trigger-After-Settle` (rides after-settle so it doesn't clobber the toast trigger).
    This is the same three-/four-payloads-in-one-response shape documented at `hub/views.py:1970`, extended with the `beforeend` append.
  - *Validation error:* re-render the approve form partial (200, non-OOB) into `#approve-sub-{{ s.pk }}-body`; modal **stays open** with field errors.
  - *Race error* (another lead already decided → `ValueError`): the view catches it, returns `204` + `trigger_toast("This submission was already handled.", "info")` + `close-modal`, plus the OOB removal of the stale pending row. No 500.
- **Reject modal (`guild_announcement_reject_form.html`, `GuildAnnouncementRejectForm`):**
  - One required field `review_note` (textarea) via `form_field.html`, `field_label="Reason for the submitter"`, `field_hint="They'll see this. Keep it kind and specific."`
  - **Submit button (named, present):** visible `pl-btn pl-btn--danger` **"Send rejection"**; secondary "Cancel". `hx-post` → `hub_guild_announcement_reject`, **`hx-target="#reject-sub-{{ s.pk }}-body" hx-swap="innerHTML"`** (P1).
  - *Success:* view calls `announcement.reject(request.user, note)`; returns a **200 OOB-only** body that OOB-removes the pending row (`id="submission-row-{{ s.pk }}"`), `trigger_toast("Submission declined — the member was notified.", "success")`, `close-modal`. (No Recent-list append — a rejected row is never shown; see Screen C / §10.)
  - *Validation error* (blank note): `clean_review_note` raises "Add a short reason so the member knows what to change." → re-render the reject form (200) into `#reject-sub-{{ s.pk }}-body`, modal stays open.
- **No new list-add/delete formset here** (the queue isn't member-editable), so the "+ Add / margin-spaced Delete" list-editor triad doesn't apply. The lead's own **direct-post** form and the FAQ/Links editors on this page are untouched and keep their existing patterns.
- **Reviewer scope is by design (P3 #11):** `.pending_for(member)` surfaces submissions **only to that guild's lead/staff**, but the approve/reject views are gated by `_require_can_edit_guild` (`hub/views.py:569`), which also admits **site admins/officers**. A site admin who is *not* this guild's lead or staff therefore sees an **empty** "Awaiting your review" card — they *can* act on a submission by URL, but the queue only lists it for the guild's own leadership. This matches the locked "reviewers = lead + all staff" decision and is intentional, not a bug: the wider edit gate protects the decision endpoints, while the queue stays a leadership-only worklist.
- **Dark/light:** all controls via `form_field.html` → `.pl-form-group`; the body preview uses `hub-text-muted`; badges use `hub-badge`. No inline `background`/`color` on any control. The `expires_at` date field is themed for both themes by `.pl-form-group`'s `color-scheme` rule (no extra treatment). Verify both themes.
- **Mobile:** rows are `display:flex` with **`flex-wrap:wrap`** and a `flex-shrink:0` action group, so on narrow widths the Approve/Reject buttons wrap under the title (the existing `_guild_announcement_row.html` does **not** wrap — it uses space-between + `flex-shrink:0` only — so this row **adds** `flex-wrap` rather than inheriting a wrap it doesn't have). Modals are full-width.

### Screen C — status legibility (small)

- **Edit-page tab affordance:** when `pending_submissions` is non-empty, show a small count badge next to the **Announcements/Emails** tab label (e.g. `<span class="hub-badge">{{ pending_submissions|length }}</span>`) so a lead landing on another tab sees there's something to review. Purely additive; hidden at zero.
- **Recent Announcements list + modal loop scoped to PUBLISHED (P3 #10):** two loops on this page iterate `guild.announcements.all` today and would otherwise show/mint UI for PENDING/REJECTED rows once statuses exist. Scope **both** to PUBLISHED — supply a `published_announcements` context list (`guild.announcements.filter(status=Status.PUBLISHED)`) and use it for:
  1. the **Recent Announcements** list loop (`guild_edit.html:490`) — so leads see only what's live there (the queue owns the pending ones), and
  2. the **edit/delete modal-generation loop** (`guild_edit.html:521`) — so it no longer mints hidden, never-triggered `edit-ann-<pk>` / `del-ann-<pk>` modals for PENDING/REJECTED rows.
  Also add **`id="recent-announcements"`** to the Recent Announcements list `<div>` (`guild_edit.html:489`) so the approve response's `hx-swap-oob="beforeend:#recent-announcements"` has a target.
- **Public page + activity feed:** unchanged markup — `.active()` (now PUBLISHED-filtered) keeps PENDING/REJECTED off both (`guild_detail.html` via `hub/views.py:489`; pulse via `:409`).
- **Stale counts after a decision — accepted (P3 #7):** the OOB row removal does **not** touch the "Awaiting your review (N)" heading or the tab badge, so both read one too high until the next page load. **We accept this staleness for v1** (documented, not a bug): the row visibly disappearing from the queue is clear feedback, and the accurate count returns on any reload/navigation. We deliberately do **not** add a fourth OOB payload to re-swap the count/badge — the response stays the row-remove + toast + close shape. (If leads report the mismatch as confusing, OOB-swapping an id'd count wrapper is the follow-up.)
- **REJECTED has no lead-facing surface — DB record only (P3 #8):** a rejected submission's record lives **only in the database** (its `status`, `reviewed_by/at`, `review_note`). It is OOB-removed from the queue on decision and never re-listed: Recent Announcements is PUBLISHED-only, and there is no "rejected submissions" view in v1. The submitter learns the outcome via the rejection notification (bell + email carrying `review_note`); a lead who needs to audit a past rejection uses the Django admin / DB. (Adding a lead-facing rejected list is deferred — §10.) The §4 `Status.REJECTED` comment reads "DB record only" to reflect this.
- **Submitter's own view:** kept minimal — the submitter learns the outcome via the notification (bell + email), not a new "my submissions" page. (Deferred; see §10.)

## 7. Notifications / emails / activity

Three new spine events (register in `core/events/registry.py`: an `EventType` in `_NEW_EVENTS` at `:334`, a recipient row in `_TRIGGER_RESOLVERS` at `:183`, and `None` in `_TRIGGER_ACTIVITY_KINDS`; add copy in `_CURATED` at `core/events/copy.py:118`; seed via `seed_notification_templates`). All links absolute (via `_absolute_url`, `membership/orientations.py:32`); both `.txt`/`.html` present; copy-mode emails styled by the branded shell (verify cream-on-dark, gold links — FRONTEND.md). Each `emit()` uses a **unique per-announcement `period`** so it delivers once and dedupes on re-save.

| Event key | Recipient | Channels | When | Copy essentials |
|---|---|---|---|---|
| `guild_announcement_submitted` | `GUILD_LEADERSHIP` | in-app ON, email ON, Discord OFF | Member submits | Subject "New {{ guild_name }} announcement to review". **Link the guild name** to the guild page; **primary CTA "Review it" → the edit page Announcements tab** at `hub_guild_edit` **`?tab=announcements`** (absolute; the `?tab=` param is what the tab `x-data` reads at `guild_edit.html:5`). Surface the submitter's **title + body** (the human content) and who sent it. |
| `guild_announcement_approved` | `SINGLE_USER` (submitter = `announcement.author`) | in-app ON, email ON | Lead approves | Subject "Your {{ guild_name }} announcement is live". **Link guild name → guild page** where it now shows; CTA "See it on the guild page". Surface the (possibly lead-edited) title. |
| `guild_announcement_rejected` | `SINGLE_USER` (submitter) | in-app ON, email ON | Lead rejects | Subject "Your {{ guild_name }} announcement wasn't posted". Surface the **lead's note** (`review_note`) prominently, guarded so it only renders when set. CTA "Visit {{ guild_name }}" (they can submit a new one from there). No dead end. |

- **Reuse for the live post:** the approve step calls the existing `guild_announcement` event via the no-arg `notify_members()` (`membership/models.py:1678`) — *not* a new event — so the guild hears an approved submission through the identical in-app/email/Discord path (and the sibling's channel picker) as a lead's own post. Nothing to add for that leg, and the `period=f"announcement:{pk}"` there is distinct from the three review periods above.
- **Opt-out vs forced:** the two submitter notices are informational-but-personal; keep email **ON (opt-out)** consistent with `guild_announcement`, in-app always on. The leadership review request is ON/ON like `class_review_requested` (`_TRIGGER_RESOLVERS` maps it to `GUILD_LEADERSHIP` at `registry.py:200`).
- **Activity:** optional `SiteActivity`/`activity_kind` is **out of scope** for v1 (leads' direct posts already carry `activity_kind="guild_announcement"`, but the review lifecycle need not) — set `None` in `_TRIGGER_ACTIVITY_KINDS` for all three. Revisit only if leads ask for an audit trail.

## 8. Build order (phased; each phase ships green)

1. **Model + migration.** Add `Status` + the 3 review fields to `GuildAnnouncement`; `.active()` status filter; `.pending_for()`; `approve()`/`reject()`/`notify_*` methods (approve reuses the existing no-arg `notify_members()`). One migration (default backfills PUBLISHED). Specs for the model + queryset. Full suite + lint + mypy green. *(Ships invisibly — no UI yet, existing behavior unchanged because default is PUBLISHED and `notify_members()` is untouched.)*
2. **Spine events + copy.** Register the 3 `EventType`s in `_NEW_EVENTS`, the `_TRIGGER_RESOLVERS` rows, `_TRIGGER_ACTIVITY_KINDS = None`, and `EventCopy` in `_CURATED` (in-app + email, `.txt`/`.html` parity); wire the three `notify_*` methods to `emit()`; verify the rejection email surfaces `review_note` (guarded). Spec the copy renders. Green.
3. **Submit path (Screen A).** `GuildAnnouncementSubmitForm`, `guild_announcement_submit` view (member-gated via `guild.memberships…exists()`), URL, Get Involved button + submit modal (mounted outside the Join form) + form partial. View + form specs (member allowed, non-member 403, blank title/body invalid + re-renders into `#submit-ann-body`). Green.
4. **Review path (Screens B + C).** `pending_for` queue + `published_announcements` in `_guild_edit_context`; "Awaiting your review" card + `_guild_submission_row.html`; approve/reject views (lead-gated via `_require_can_edit_guild`), forms, modals (mounted alongside the existing modal loop); `id="recent-announcements"`; scope the Recent loop **and** the modal loop to PUBLISHED; tab count badge. View specs (approve publishes + fans out + notifies submitter + OOB-appends the row + removes the pending row; reject requires note + notifies; non-lead 403; already-decided race → toast). Green.
5. **Housekeeping.** Verify both themes on the submit modal + review card + decision modals + date field; mobile reflow pass (confirm the submission row actually wraps). Bump `plfog/version.py` VERSION and add the member-facing CHANGELOG entry (§ below).

> Spec only — do not build until approved.

**CHANGELOG (member-facing, curate into the current 0.20.x line):** this is a net-new feature, so a fresh top entry —
> **Suggest an announcement for your guild.** Any guild member can now write up an announcement and send it to the guild's leads for a quick look. A lead can tweak the wording, choose where it posts, and share it with the whole guild the usual way — or send it back with a note. You'll hear either way. Leads keep posting directly, no review needed.

## 9. Testing

BDD `*_spec.py` under `membership/spec/` and `hub/spec/`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image (`--no-cov` for subsets), ≥98% coverage gate.

- **Model / queryset (`membership`):**
  - `.active()` excludes PENDING and REJECTED, keeps PUBLISHED-and-unexpired; PUBLISHED-but-expired still excluded (existing expiry test still passes).
  - `.pending_for(member)` returns only PENDING rows in guilds the member leads *or* staffs; excludes other guilds and non-PENDING rows.
  - `approve()` from PENDING → PUBLISHED, sets `reviewed_by/at`, re-stamps `published_at`, calls the no-arg `notify_members()` **once** and notifies submitter; approving a non-PENDING row raises `ValueError`. (Assert it does **not** touch/require `post_to_discord`.)
  - `reject()` from PENDING → REJECTED, stores note, notifies submitter, does **not** call `notify_members()`; non-PENDING raises.
  - Backfill/default: a row created with no status is PUBLISHED (guards the migration default).
- **Notifications (`core.events`):** each of the 3 events resolves to the right audience (leadership / submitter-user); email + in-app copy render with the merge fields; the rejection copy includes `review_note` and is guarded when blank; the submitted-copy CTA points at `?tab=announcements`; periods are unique per announcement (a re-save doesn't double-send).
- **Views (`hub`):**
  - Submit: a guild member can submit (row created PENDING, `author` = them, leadership notified); a non-member gets 403; blank title/body re-renders with errors **into `#submit-ann-body`**; the direct-post lead path is untouched (still PUBLISHED immediately).
  - Approve: a lead publishes with inline edits persisted and the fan-out fired; the response OOB-appends the row into `#recent-announcements` and OOB-removes the pending `#submission-row-<pk>`; a non-lead 403; an already-decided row returns the friendly "already handled" toast, not a 500.
  - Reject: a lead with a note declines and the submitter is notified; a blank note re-renders into `#reject-sub-<pk>-body`.
  - **Template-state assertions (nested-form lesson):** parse the HTML (not just a 200) to assert the submit modal and both decision forms each contain a real `<button type="submit">`, and that the submit / approve / reject modals mount **outside** the Join form / the "Post an Announcement" form respectively — so a structural regression (orphaned button / nested form) is caught.
- **Gotchas:** `expires_at` is a `DateField` compared to `timezone.localdate()` — keep the tz-window tests from the existing `.active()` suite. The approve view's response carries multiple payloads (OOB append + OOB remove + toast + close) — assert the `HX-Trigger`/`HX-Trigger-After-Settle` headers and the `hx-swap-oob="beforeend:#recent-announcements"` attribute as `guild_announcement_edit`'s spec (`hub/views.py:1970`) does for its own OOB row.

## 10. Open / deferred

- **No persisted `DRAFT` state.** The feature says "draft and submit," but the compose form *is* the draft surface; a persisted draft would pull in a "my drafts" list, edit-draft, and delete-draft surface (YAGNI). If members later ask to save-and-finish-later, add `DRAFT` then.
- **Rejection is terminal — no edit-and-resubmit.** Unlike a class (which bounces DENIED→DRAFT for the instructor to fix), a rejected submission is closed; the member composes a new one. Reconsider only if members find re-typing painful.
- **No lead-facing "rejected submissions" list.** REJECTED rows are DB-only (§6 Screen C); if leads later want to review what they've declined, add a scoped list then.
- **No "My submissions" page for the submitter.** Outcome reaches them via bell + email; a personal status list is deferred until there's demand.
- **Queue count/badge don't live-update after a decision** (accepted staleness, §6 Screen C) — OOB-swapping an id'd count wrapper is the follow-up if the mismatch confuses leads.
- **No login-free tokened review.** Reviewers act from inside the hub (they're logged-in leadership), so no `ClassApproval`-style emailed token. If we ever want a lead to approve straight from the email without logging in, add a token then.
- **Discord channel picker** for the live post comes from the sibling spec `2026-07-03-guild-announcement-discord-channel-picker.md` via `notify_members()` reading the row's `discord_channel`; this plan reuses the picker at approval and neither duplicates nor blocks it, and does not re-introduce `post_to_discord`.
- **Activity log** for submit/approve/reject is out of scope for v1 (parity with today's un-logged review lifecycle).
- **Rate-limiting / spam control** on member submissions (e.g. cap pending-per-member) is out of scope; the leadership review gate is the control. Revisit if abused.
