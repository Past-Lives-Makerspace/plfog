# Meetings QoL — Propose Visibility, Unpublish, Published-State Wiring — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-24
**Surface:** FOG hub — `/meetings/` home, meeting workspace, guild detail Meetings tab
**Related:** `docs/superpowers/plans/2026-08-11-meetings.md` (the canonical Meetings spec; this amends its §6.3 propose gating and status vocabulary)

---

## 1. Summary

Four small quality-of-life fixes to Meetings. Editors and admins get the "Propose an agenda item" button too (their submissions land in the same pending-proposals queue). Council meetings accept proposals from **all active members**, not just editors. A published agenda can be **unpublished** back to draft by anyone who can edit it — today PUBLISHED is a dead end with no way back. And the PUBLISHED state finally renders truthfully: today every non-approved meeting shows a "Draft" badge, and a published past-dated meeting is invisible on the guild tab.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Propose button audience | Show "Propose an agenda item" to **everyone** who can propose, including editors/admins — remove the `not can_edit` clause at all three call sites. Editor submissions land in the same pending-proposals queue. |
| Council meeting proposals | Open to **all active members** (currently editors-only) — mirrors how guild meetings gate on active membership. |
| Unpublish permission | Anyone who passes `can_edit_meeting` may unpublish (PUBLISHED → DRAFT) — symmetric with publish, **not** admin-only. Admin-only Unlock of APPROVED stays unchanged. |

## 2. What already exists (reuse, don't reinvent)

There is no meetings app — models live in `membership`, views in `hub`.

| Need | Existing thing | Location |
|---|---|---|
| Status vocabulary | `Meeting.Status` DRAFT/PUBLISHED/APPROVED | `membership/models.py:5530-5533` |
| Lock semantics | `is_locked` (== APPROVED only) / `assert_editable()` | `membership/models.py:5681-5683`, `:5745-5751` |
| The transition to mirror | `Meeting.publish()` DRAFT→PUBLISHED, `save(update_fields=["status"])` | `membership/models.py:5753-5765` |
| The activity-log idiom to mirror | `unlock()` logs `SiteActivity.Kind.MEETING_UNLOCKED`, no broadcast | `membership/models.py:5829-5844`, `core/models.py:1004` |
| Propose permission (all guards) | `can_propose_to_meeting` — locked guard `:169-170`, past-date `:171-172`, editors already True via `is_editable`/`can_edit_meeting` `:183-185`, council non-editors refused at `:186-187` | `membership/permissions.py:142-192` |
| Edit permission (unpublish gate) | `can_edit_meeting` | `membership/permissions.py:104-119` |
| The view to mirror | `hub_meeting_publish` (`@login_required @require_POST`, `can_edit_meeting` gate, `messages.success` + redirect) | `hub/meeting_views.py:779-791` |
| Confirm-then-POST plumbing | `confirm_modal.html` with `confirm_hx_post` + `_hx_redirect`, as the Unlock banner uses | `templates/hub/meeting_workspace.html:59-63`, `hub/meeting_views.py:103-105` |
| Propose modal + form + toast | `_propose_modal.html`, `MeetingItemProposalForm` (title + why), `hub_meeting_propose` already returns 204 + success toast "Proposed. {scope} leadership will review it." | `templates/hub/partials/_propose_modal.html`, `hub/forms.py:1406-1414`, `hub/meeting_views.py:911-929` |
| Archive-window queryset (for "awaiting minutes") | `MeetingQuerySet.archive()` — past + undated, newest first | `membership/models.py:5471-5477` |
| Badge styles to extend | `.pl-meeting-badge` + `--draft`/`--approved` variants; the workspace `.pl-meeting-published-badge` | `static/css/hub.css:6376-6397`, `:6211-6219` |
| Test scaffolding | `MeetingFactory` with `published` trait; `describe_propose`, `describe_unlock` etc. | `tests/membership/factories.py:302-320`, `tests/hub/meetings_views_spec.py:1004`, `:780` |

Gaps to close: `Meeting.unpublish()` doesn't exist (`unlock()` explicitly refuses PUBLISHED at `:5838-5839`); no unpublish view/URL/control anywhere (and Meeting isn't in Django admin — staying that way); no `--published` badge variant; the guild tab shows only `upcoming()` + `approved()` (`hub/views.py:500-513`), so a published past/undated meeting appears nowhere on it.

## 4. Data model & migration

No new models, no new fields. One new `SiteActivity.Kind` choice in `core/models.py` after `MEETING_UNLOCKED` (`:1004`):

```python
MEETING_UNPUBLISHED = "meeting_unpublished", "Meeting agenda unpublished"
```

`SiteActivity.kind` is a `CharField(max_length=50, choices=Kind.choices)` (`core/models.py:1015`), so **`makemigrations` will emit one `AlterField` migration in `core`** — choices are part of the field definition. It is state-only (no SQL executed on Postgres) and auto-reversible; no data migration, no reverse function needed.

## 5. Business logic (fat models)

### `Meeting.unpublish(*, by: User)` — `membership/models.py`, next to `publish()`

Mirrors `publish()` exactly, in the other direction:

```python
def unpublish(self, *, by: User) -> None:
    """Return a published agenda to draft — symmetric with publish().

    Raises:
        MeetingLockedError: If the minutes are approved (unlock is the admin path).
        ValueError: If the meeting is not currently published.
    """
    if self.status == self.Status.APPROVED:
        raise MeetingLockedError("Approved minutes are locked — an admin can unlock them.")
    if self.status != self.Status.PUBLISHED:
        raise ValueError(f"Cannot unpublish a meeting with status {self.status!r}.")
    self.status = self.Status.DRAFT
    self.save(update_fields=["status"])
    from core.models import SiteActivity

    SiteActivity.log(SiteActivity.Kind.MEETING_UNPUBLISHED, actor=by, target=self)
```

Silent by design: `publish()` emits nothing, so unpublish emits nothing either — activity row only, same as `unlock()`.

### `can_propose_to_meeting` council branch — `membership/permissions.py:186-192`

Open the council scope to any active member; the locked (`:169-170`) and past-date (`:171-172`) guards above are untouched, and the editable fast-path (`:183-185`) still answers first. Restructure the tail so the active-member check runs before the guild branch:

```python
# replaces lines 186-192
if member is None or member.status != Member.Status.ACTIVE:
    return False
if meeting.guild is None:
    return True                       # council: any active member may propose
if member_guild_ids is not None:
    return meeting.guild_id in member_guild_ids
return meeting.guild.memberships.filter(member=member).exists()
```

Update the docstring's "Council: guild leads/staff/admins only" line (`:151-153`). No change is needed for "editors propose too" — the function already returns True for editors; the exclusion lives in views/templates (next section). `meeting.item_proposed` routing (`GUILD_LEADERSHIP_OR_EVENTS_APPROVERS`, `core/events/registry.py`) is unchanged.

### Views — `hub/meeting_views.py`, `hub/views.py`, `hub/urls.py`

- **Drop `not can_edit` from the three propose computations** (pass the already-computed editability so no extra query runs):
  - `_workspace_context` `hub/meeting_views.py:353` → `"can_propose": can_propose_to_meeting(request, meeting, is_editable=can_edit)`
  - `hub_meetings` `hub/meeting_views.py:1105-1107` → drop `not meeting.viewer_can_edit and`; keep `member_guild_ids=member_guild_ids, is_editable=meeting.viewer_can_edit`
  - `guild_detail` `hub/views.py:503-507` → drop the `not can_edit_this_guild` clause from `can_propose_next`
  - Delete/replace the now-false "editors add directly" comments at `hub/meeting_views.py:352`, `:1103-1104`, `hub/views.py:505`, and `templates/hub/meeting_workspace.html:263`.
- **New `hub_meeting_unpublish`** — mirrors `hub_meeting_publish` (`:779-791`) but responds to the confirm modal's `hx-post` like `hub_meeting_unlock` (`:828-839`):

```python
@login_required
@require_POST
def hub_meeting_unpublish(request: HttpRequest, pk: int) -> HttpResponse:
    meeting = get_object_or_404(Meeting.objects.select_related("guild"), pk=pk)
    if not can_edit_meeting(request, meeting):
        return HttpResponse("Forbidden", status=403)
    user: User = request.user  # type: ignore[assignment]
    try:
        meeting.unpublish(by=user)
    except (ValueError, MeetingLockedError) as exc:
        return _invalid(str(exc))
    messages.success(request, "Agenda unpublished — back to draft.")
    return _hx_redirect(reverse("hub_meeting", args=[meeting.pk]))
```

- **URL** — `hub/urls.py`, after the publish route (`:18`): `path("meetings/<int:pk>/unpublish/", meeting_views.hub_meeting_unpublish, name="hub_meeting_unpublish")`.
- **Guild tab data** — `guild_detail` (`hub/views.py`, near `:513`): add to the context

  ```python
  window = Meeting.objects.for_scope(guild).archive().select_related("guild")
  awaiting_minutes = (
      window.exclude(status=Meeting.Status.APPROVED)       # editors: published + slipped drafts
      if can_edit_this_guild
      else window.filter(status=Meeting.Status.PUBLISHED)  # members: published only
  )[:3]
  ```

  `archive()` covers past **and** undated, so a non-approved meeting can't fall in the gap between `upcoming()` and `approved()` — including one that was just **unpublished**: for editors it stays on the tab as a Draft row (mirroring the Meetings-home `needs_attention()` strip, which deliberately keeps both DRAFT and PUBLISHED in scope, `membership/models.py:5479-5488`); plain members still reach it in the site-wide Meetings archive, which lists all statuses (meetings_home.html:177-183).

## 6. UI / UX (per screen, checklist applied)

### 6.1 Meeting workspace — `templates/hub/meeting_workspace.html`

- **Header, published state (`:38-40`):** keep the Published badge, restyled — swap the span's class from `pl-meeting-published-badge` (`:39`) to the shared `pl-meeting-badge pl-meeting-badge--published` (§6.2) and **delete** the now-orphaned `.pl-meeting-published-badge` rule (`hub.css:6211-6219`, its only call site): its `--color-success` green collided with Approved's green — PUBLISHED is one label, one blue, everywhere. Next to the badge, add an **Unpublish** button — `pl-btn pl-btn--secondary pl-btn--sm`, rendered only behind a new `can_unpublish` context flag (`can_edit and meeting.status == 'published'`). This gate is **load-bearing**: the published-state `elif` at `:38` sits **outside** any `is_editable` gate and renders for read-only viewers too, so without the explicit flag every member would see an Unpublish button that 403s. The button dispatches `open-confirm` to a `components/confirm_modal.html` include (same `confirm_hx_post` mechanics as Unlock at `:59-63`):
  - `confirm_id="unpublish-meeting"`, `confirm_title="Unpublish this agenda?"`
  - `confirm_message="The agenda goes back to draft so you can keep editing — items, proposals, and attendance all stay, and members are not notified. Until you publish or approve it again, members will find this meeting only in the Meetings archive."`
  - `confirm_button_text="Unpublish"`, `confirm_button_style="primary"` (not destructive-red: nothing is lost)
  - POST → `hub_meeting_unpublish` → full-page redirect + Django message "Agenda unpublished — back to draft." (success feedback). Guard failures return the standard `_invalid` toast (error state); a plain member never sees the button and the endpoint 403s (matches the publish idiom).
- **Propose button (`:264-269`, modal include `:479-481`):** unchanged markup — it simply now renders for editors too because `can_propose` no longer excludes them. It sits under the agenda alongside the editor's Add form; per the locked decision that's fine. The editor's own submission appears in the pending-proposals strip they already see.
- **Not touched:** the hand-rolled `pl-toggle` "Special meeting" checkbox (`:70-81`) is HTMX-driven, not a Django form field — an accepted deviation; leave it. The autosave-everywhere workspace (no Save buttons) is spec-sanctioned (`2026-08-11-meetings.md:23`) — do **not** add Save buttons.
- Dark + light: the new button and modal are existing components on theme tokens — verify both themes. Mobile: the header actions row already wraps; one more small button reflows with it.

### 6.2 Meetings home — `templates/hub/meetings_home.html`

- **Upcoming badges (`:31-35`):** three-state. Replace the `is_locked`-else-Draft branch with: `is_locked` → Approved (as now); `meeting.status == 'published'` → `<span class="pl-meeting-badge pl-meeting-badge--published">Published</span>`; else → Draft.
- **Coordinator "Most recent" cell (`:109`):** keep the ✓ tick for approved; add `{% elif row.most_recent.status == 'published' %}` → the small `--published` badge. Draft stays unmarked (current behavior).
- **Archive Status column (`:177-183`):** same three-state branch as Upcoming.
- **New CSS:** `.pl-meeting-badge--published` in `static/css/hub.css` next to `--draft`/`--approved` (`:6388-6397`) — same pill anatomy, colored on `--hub-blue` (border + text, transparent background) so Draft (muted) / Published (blue) / Approved (green) read as a progression in both themes. This is the **single** Published treatment site-wide — the workspace header adopts it too (§6.1) and the old green `.pl-meeting-published-badge` is deleted.
- **Propose button on upcoming cards (`:45-52`):** unchanged markup; now renders for editors too via `viewer_can_propose`.
- **Polish (same file):** archive filter Apply button (`:163`) `hub-btn hub-btn--sm hub-btn--primary` → `pl-btn pl-btn--primary pl-btn--sm`, matching every other meetings button. The "+ New meeting" modal (`:199-228`) keeps its inline `pl-modal` shell (the documented house inline pattern — `components/modal.html` only supports HTMX-loaded bodies) but loses the inline `style="flex:1;"` on its action buttons (`:222-223`) — see 6.4's shared class.

### 6.3 Guild detail Meetings tab — `templates/hub/partials/_guild_meetings_tab.html`

- **Propose gate (`:32-38`):** unchanged markup; `can_propose_next` now includes leads/staff, so leadership sees the button on the next meeting too.
- **Unfinished-meeting rows (new, minimal):** in the **Recent minutes** card (`:63-75`), render the `awaiting_minutes` rows (§5) **above** the approved rows, same `.pl-guild-meetings__minute` row shape — title link + date + the meeting's **status badge**: `pl-meeting-badge--published` reading "Published", or (editor-only rows) `pl-meeting-badge--draft` reading "Draft". One state = one label = one color, matching every other surface — the "awaiting minutes" idea lives in row placement (above the ✓ rows), not in a divergent badge label. A published past/undated meeting is now reachable from its guild page for everyone, and an **unpublished** one stays reachable there for its editors instead of vanishing again.
- **Empty state:** "No approved minutes yet." (`:72`) must only show when **both** lists are empty — wrap the two loops in `{% if awaiting_minutes or recent_minutes %} … {% else %}<empty>{% endif %}` instead of relying on the `{% empty %}` clause.
- Mobile/dark: reuses existing row + badge styles; nothing new to verify beyond the badge variant in both themes.

### 6.4 Propose modal rebuild — `templates/hub/partials/_propose_modal.html`

- Replace the hand-rolled `hub-form-group` label/input/textarea markup (`:31-39`) with `components/form_field.html` rendering the real `MeetingItemProposalForm` fields (Topic required, Why optional — validation already lives in the form, `hub/forms.py:1406-1414`).
- **The Why hint must not be dropped:** the current hand-rolled hint "Helps leadership slot it into the meeting." (`_propose_modal.html:38`) has no counterpart on the form field. Add it as `help_text` on `MeetingItemProposalForm.why` (single source of truth — `form_field.html` renders `field.help_text` as the hint automatically, `components/form_field.html:39-42`); no `field_hint` override on the include.
- form_field needs a bound field, so each including context passes an **unbound form with a per-call-site `auto_id`** (this is load-bearing: the meetings-home list renders one modal per card, and a shared form would emit duplicate DOM ids):
  - workspace `_workspace_context`: `"propose_form": MeetingItemProposalForm(auto_id="propose-item-%s")` when `can_propose`
  - `hub_meetings`: attach `meeting.propose_form = MeetingItemProposalForm(auto_id=f"propose-item-{meeting.pk}-%s")` per proposable card
  - `guild_detail`: `"propose_form"` as on the workspace
  - The include contract grows to `with meeting=<m> modal_key=<key> form=<propose_form>`.
- Keep the modal's inline `pl-modal` shell, the `hx-post` + reset-on-success behavior (`:28-29`), and the existing success toast (verified: `hub_meeting_propose` returns 204 + "Proposed. {scope} leadership will review it." — `hub/meeting_views.py:927-928`; validation errors return the `_invalid` error toast).
- **Inline-style cleanup:** replace `style="flex:1;"` on the two action buttons (`:41-42`, and the new-meeting modal's `:222-223`) with a shared `pl-modal__actions--fill` modifier in `static/css/components.css` (`.pl-modal__actions--fill .pl-btn { flex: 1; }`) — scoped so the many other `pl-modal__actions` users are untouched.
- States: empty = the blank form; error = toast via `_invalid`; success = toast + modal closes + form resets; loading = HTMX default (sub-second POST, no spinner needed). Verify both themes (fields now go through `form_field.html`, which is already theme-correct).

## 8. Build order (phased; each phase ships green)

1. **Model + permission.** `Meeting.unpublish()`, `SiteActivity.Kind.MEETING_UNPUBLISHED` + the `AlterField` migration, the `can_propose_to_meeting` council branch, docstring updates. Model + permission specs. Run `manage.py check` after the migration.
2. **Views + UI.** `hub_meeting_unpublish` view + URL; drop `not can_edit` at the three propose sites; guild-tab `awaiting_minutes` context; all template changes from §6; `--published` badge + `pl-modal__actions--fill` CSS; propose-modal form_field rebuild with per-site `auto_id` forms. View + rendering specs; verify both themes.
3. **Housekeeping.** Bump `plfog/version.py` `VERSION` 1.6.1 → **1.7.0** and add **one** member-friendly CHANGELOG entry (dict with `version`/`date`/`title`/`changes`, stamped `1.7.0`) covering the whole bundle, e.g. title "Meeting Agendas Get More Flexible", changes: anyone can now propose agenda items for Council meetings; the propose button shows for meeting editors too; a published agenda can be taken back to draft for more edits; meetings now show whether they're Draft, Published, or Approved everywhere. `ruff format`/`check`; note Discord auto-announces on merge when VERSION changes — the entry must be paste-ready before merging.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` only (no `context_*` — it silently skips), factory-boy, 100% coverage + mutation gate.

**`tests/membership/meetings_models_spec.py` — `describe_unpublish`:**
- it_returns_published_to_draft (via `MeetingFactory(published=True)`; refresh, status == DRAFT)
- it_logs_the_unpublished_activity (one `SiteActivity` row, kind `meeting_unpublished`, actor + target set)
- it_raises_locked_on_approved_minutes (`MeetingLockedError`; status unchanged)
- it_raises_value_error_on_a_draft (message names the status; no activity row)

**Permission specs (alongside the existing `can_propose_to_meeting` cases):**
- council + active plain member → True (the changed behavior)
- council + inactive member → False; council + anonymous/unlinked → False
- council + active member, meeting locked → False; past-dated → False (guards survive)
- guild meeting behavior unchanged (non-member still False, guild member still True)
- an editor still True (the `is_editable` fast-path)

**`tests/hub/meetings_views_spec.py`:**
- `describe_unpublish` (new, next to `describe_unlock` `:780`): guild lead POST → redirect + status DRAFT; **fog admin** → allowed; plain member → 403 (status unchanged); draft meeting → `_invalid` (422-style) toast; approved meeting → `_invalid` with the locked message; GET → 405
- propose visibility: workspace context `can_propose` True for an editor; `hub_meetings` sets `viewer_can_propose` True on an editor's card; guild tab `can_propose_next` True for the lead; an editor's `hub_meeting_propose` POST creates a PENDING proposal (same queue)
- unpublish rendering (next to `describe_workspace_lifecycle_rendering` `:1289`): an **editor's** published workspace contains the Unpublish button and its confirm modal; a **plain member's** published workspace contains **neither** (Published badge only) — the mis-gate canary
- badge rendering: a `published` meeting shows the blue Published badge in Upcoming, in the archive Status column, as the coordinator most-recent marker, and in the workspace header; draft/approved renderings unchanged
- guild tab: a published **past-dated** meeting renders in the Recent minutes card with a Published badge for a plain member; an **undated** published one too; an editor **additionally** sees a past-dated DRAFT row with the Draft badge, which a plain member does not; approved list unchanged; empty state only when both lists are empty
- propose modal: fields render via `form_field.html` with per-meeting ids on the home list (no duplicate DOM ids); required-title validation error returns the error toast

Gotchas: `pytest | tail` masks the exit code — read the summary line; run `manage.py check` (CI runs it, local pytest doesn't); local mypy crashes on the django-stubs plugin — CI is the real check; `template_comment_lint_spec` guards any new template comments.

## 10. Out of scope

- **No Save buttons** — the autosave workspace stays as spec-sanctioned (`2026-08-11-meetings.md:23`).
- **No email/Discord/push** for publish or unpublish — both stay silent (activity log only for unpublish).
- **No Django admin registration** for Meeting.
- **No rework** of the special-meeting `pl-toggle` or action-item checkboxes (HTMX-driven, sanctioned deviations).
- **No changes to Approve/Unlock** — unlock stays `@fog_admin_required`, APPROVED semantics untouched.
- **No full componentization of the inline modals** onto `components/modal.html` — its body is HTMX/context-string only; the inline `pl-modal` shell is the documented house pattern for inline-form modals. Only the field markup and inline styles are cleaned up here.
- `needs_attention()` deliberately keeps PUBLISHED in scope (`membership/models.py:5479-5488`) — correct as-is, untouched.
