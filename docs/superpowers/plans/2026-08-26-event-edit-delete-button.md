# Delete Button on the Event Edit Page — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-26
**Surface:** FOG hub — `templates/hub/community_event_edit.html` (serves both `/events/<pk>/edit/` and `/guilds/<pk>/events/<event_pk>/edit/`)
**Related:** none

---

## 1. Summary

An admin editing a site-wide event (or a guild editor editing a guild event) currently has no way to delete it from the edit page — they must back out to the Community Calendar Events tab (or the guild Events tab) and find the row's Delete button there. This adds a Delete button, with the standard confirm modal, directly on the edit page. No new backend: both delete endpoints already exist and already clean up Google Calendar and Discord.

### Locked decisions

| Decision | Choice |
|---|---|
| Scope | Template + view-context change only; reuse the existing `event_delete` / `guild_event_delete` POST endpoints untouched. |
| Both surfaces | The shared template fixes both the admin site-wide edit page and the guild event edit page in one pass — the view supplies the right `delete_url`. |
| New events | No Delete button when creating (`event.pk` is None) — nothing to delete. |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Site-wide delete endpoint (admin-only, POST, removes from Google + Discord, redirects to calendar Events tab) | `event_delete` | `hub/views.py:3562`, URL `hub_event_delete` (`hub/urls.py:358`) |
| Guild delete endpoint (guild-editor gated, guild-scoped lookup) | `guild_event_delete` | `hub/views.py` (URL `hub_guild_event_delete`, `hub/urls.py:296`) |
| Confirm-before-delete pattern for events | Delete button + `confirm_modal` on the calendar Events tab | `templates/hub/community_calendar.html:276-280`; also `templates/hub/partials/calendar_event_item.html:85` and `templates/hub/guild_edit.html:318` |
| The edit form page | `community_event_edit.html` (rendered by both `event_edit` `hub/views.py:3518` and `guild_event_edit` `hub/views.py:3442`) | `templates/hub/community_event_edit.html` |
| Confirm modal component | `components/confirm_modal.html` | `templates/components/` |

Gap: the edit template renders only "Save event" + "Cancel" (`community_event_edit.html:92-93`); neither view passes a delete URL.

## 3. Where the code lives

```
hub/views.py                                # event_edit + guild_event_edit: add delete_url to context
templates/hub/community_event_edit.html     # Delete button + confirm modal
tests/hub/…event…_spec.py                   # template-state specs (see §9)
```

## 4. Data model

None. No migrations.

## 6. UI / UX

**Screen:** `templates/hub/community_event_edit.html` (edit mode only).

- **Layout:** the existing footer action row (`Save event` primary + `Cancel` secondary, line 92-93) stays as-is on the left. The Delete button is added to the same row, pushed to the far right (`margin-left:auto` via a small flex class on the row — no inline `display` on any `x-show` element is involved), so destructive and constructive actions are visually separated. On narrow screens the row wraps (`flex-wrap`) and Delete drops to its own line with `0.75rem` top spacing — 8px-grid, no clashing margins.
- **Components, named:**
  - Delete button: `<button type="button" class="pl-btn pl-btn--danger" @click="$dispatch('open-confirm', 'delete-event')">Delete</button>`. Full-size (no `--sm`) is a deliberate deviation from the list-row convention: it sits beside the full-size Save/Cancel in the same action row, where a `--sm` button would read as misaligned. `type="button"` so it never rides along on Save (same guard the template already documents at line 72 for another control).
  - Confirm modal: `{% include "components/confirm_modal.html" with confirm_id="delete-event" confirm_title="Delete this event?" confirm_message=delete_confirm_message confirm_action_url=delete_url confirm_button_text="Delete event" %}`. `confirm_message` is **required** by the component, so the views build `delete_confirm_message` alongside `delete_url` (cleanest place to branch the 2×2 copy; a template `{% with %}` chain can't append strings):
    - `moderation_state == PUBLISHED`: "Members will no longer see it on the calendar, and it'll be removed from Google Calendar and Discord. This can't be undone."
    - Everything else (scheduled, or a not-yet-published proposal state — `event_edit` fetches unfiltered, so pending/changes-requested/declined rows can reach this page): "It'll be removed before it's ever announced. This can't be undone." (Message text matches the calendar tab's scheduled-row modal; that modal's title/button differ — no parity requirement.)
    - When `event.recurrence != "none"`, append " This removes the whole series."
  - `{% if event.pk %}` guards both button and modal — the Add page shows neither. The include can sit anywhere (the component `x-teleport`s itself to `body`, so it never nests inside the edit form); keep it after `</form>` for readability.
- **View change:** `event_edit` passes `delete_url = reverse("hub_event_delete", args=[event.pk])`; `guild_event_edit` passes `delete_url = reverse("hub_guild_event_delete", args=[guild.pk, event.pk])` — each only when editing, each alongside its `delete_confirm_message`. Template renders the Delete affordance only when `delete_url` is present, so the two permission models stay exactly where they are today (`_require_admin` / `_require_can_edit_guild` + the guild-scoped 404).
- **States:** success = existing endpoint behavior — `event_delete` redirects to the Community Calendar Events tab; `guild_event_delete` redirects to the guild *management* page's Events tab (`hub_guild_edit?tab=events` — same as this page's `cancel_url`; do not "fix" it to the guild detail page). Both show the "Event deleted." Django message (full-page POST, not HTMX, so messages are correct per FRONTEND.md). Error: permission failure returns the endpoints' existing forbidden response; a stale double-delete 404s — acceptable and already true of the list-page buttons. No loading state needed (plain POST).
- **Dark + light:** `pl-btn--danger` and `confirm_modal` are existing themed components; nothing new to token-check, but verify both themes on the rendered page.
- **Mobile:** action row wraps as above; all three controls are full buttons (real tap targets).

## 8. Build order

1. Pass `delete_url` from both views; add button + modal + `{% if %}` guards to the template; wrap the footer row in a small `pl-` flex class.
2. Specs (§9), lint, `manage.py check`.
3. Bump `plfog/version.py`. Changelog: this is a fix/affordance on a live surface — fold into the current release line's calendar/events entry if one exists at the current `VERSION`, else one short entry ("You can now delete an event right from its edit page.").

> Spec only — do not build until approved.

## 9. Testing

BDD specs alongside the existing hub event specs (`tests/hub/`):

- `it_shows_delete_on_the_site_wide_edit_page` (admin, existing event → button + modal present, posts to `hub_event_delete`)
- `it_shows_delete_on_the_guild_edit_page` (guild lead → posts to `hub_guild_event_delete` with the guild pk)
- `it_hides_delete_when_creating` (add page → no button, no modal)
- `it_uses_series_copy_for_recurring_events` (recurrence set → "whole series" line present)
- Existing endpoint specs already cover the delete behavior itself — don't duplicate.

## 10. Open / deferred

- The Save button says "Save event"; FRONTEND.md Rule 21 says plain "Save". Out of scope here — note for a copy sweep.
