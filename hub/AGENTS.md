# hub app

Member-facing views. All views are `@login_required`. No models — reads from `membership` and `billing`.

## Views → URLs

| View | URL name | Path |
|------|----------|------|
| `guild_voting` | `hub_guild_voting` | `/guilds/voting/` |
| `snapshot_history` | `hub_snapshot_history` | `/guilds/voting/history/` |
| `snapshot_detail` | `hub_snapshot_detail` | `/guilds/voting/history/<pk>/` |
| `guild_detail` | `hub_guild_detail` | `/guilds/<slug>/` (old `/guilds/<pk>/` 301-redirects via `hub_guild_detail_by_id`) |
| `guild_orientations` | `hub_guild_orientations` | `/guilds/<pk>/orientations/` (the guild's Orientations page, #672: every orientation card from `partials/_guild_orientations_section.html` under the plGuildAutosave root, gated like Guild Settings; every orientation save lands here via `_guild_orientations_url`, and Guild Settings' `?tab=orientations` redirects here) |
| `member_directory` | `hub_member_directory` | `/members/` |
| `profile_settings` | `hub_profile_settings` | `/settings/profile/` |
| `email_preferences` | `hub_email_preferences` | `/settings/emails/` |
| `beta_feedback` | `hub_beta_feedback` | `/feedback/` |
| `tab_detail` | `hub_tab_detail` | `/tab/` |
| `tab_history` | `hub_tab_history` | `/tab/history/` |
| `announcements_overview` | `hub_announcements` | `/announcements/` (Drafts and Sent tabs, `?tab=drafts\|sent`) |
| `announcement_sent` | `hub_announcement_sent` | `/announcements/sent/<pk>/` (read-only record of a sent or sending announcement) |
| `orientations_views.hub_orientations` | `hub_orientations` | `/orientations/` (every bookable orientation as a card; its controls post `next` so the member lands back, `views._orientation_return`; the header's "+ Add an Orientation" links the guilds from `membership.permissions.guilds_for_new_orientation` to their Orientations page, #637 and #672) |
| `orientations_views.hub_orientations_calendar_events` | `hub_orientations_calendar_events` | `/orientations/calendar/events/` (the Orientations page's Calendar view: grid and list for its navigation, `?shell=1` for the whole calendar on first open) |
| `location_views.hub_admin_locations` | `hub_admin_locations` | `/manage/locations/` (admin only: every Location, with the add form; `hub_admin_location_edit` at `/manage/locations/<pk>/`) |
| `orientations_views.hub_orientation_type_permalink` | `hub_orientation_type_permalink` | `/orientations/types/<pk>/` (the stable link an orientation QR sheet encodes, #631: redirects to the type's current booking link, `OrientationType.booking_landing_path`) |
| `orientations_views.hub_orientation_type_flyer`, `hub_orientation_type_qr` | `hub_orientation_type_flyer`, `hub_orientation_type_qr` | `/orientations/types/<pk>/flyer/`, `/orientations/types/<pk>/qr.<svg\|png>/` (the orientation QR sheet and its downloads, #631; gated by `_require_can_run_orientation_type`, refused with `OrientationType.qr_sheet_refusal`) |
| `equipment_views.hub_equipment_flyer`, `hub_equipment_qr` | `hub_equipment_flyer`, `hub_equipment_qr` | `/equipment/<slug>/flyer/`, `/equipment/<slug>/qr.<svg\|png>/` (the equipment QR sheet and its downloads, #631; gated by `can_manage_equipment`, refused with `Equipment.qr_sheet_refusal`) |
| `orientations_views.hub_orientations_bookings` | `hub_orientations_bookings` | `/orientations/bookings/` (the Orientations page's Bookings tab alone, for its lazy open and the refund refresh; built by `hub/orientation_bookings.py`, #626) |
| `orientations_dashboard` | `hub_orientations_dashboard` | `/orientations/manage/` (302 to `/orientations/?view=bookings` with its query; export, add member and the oriented toggle keep their paths under it) |
| `equipment_views.hub_equipment_calendar_events` | `hub_equipment_calendar_events` | `/equipment/calendar/events/` (the Reservations page's Calendar view, the same way) |
| `equipment_views.hub_equipment_bookings` | `hub_equipment_bookings` | `/equipment/bookings/` (the Reservations page's Bookings tab alone, for its lazy open and the late fee refund refresh; built by `hub/reservation_bookings.py`, #627) |

## List / Calendar pages

The Orientations and Reservations pages (#502) open on List and build their calendar only for `?view=calendar`; otherwise the Calendar pane fetches the shell once on first open (`static/js/list_calendar.js`, any placeholder carrying `data-lazy-pane` and `data-pane-src`). Both reuse the guild page's shell (`partials/guild_calendar_app.html`) with `cal_key` and `cal.legend`; `hub/calendar_pages.py` builds their context on `hub/calendar_window.py` (`calendar_window_context`), the one place every calendar's date arithmetic lives, from rows in `hub/calendar_entries.py`.

The Orientations page's Bookings tab (#626, `?view=bookings`) loads the same way. Its rows are scoped by `membership.permissions.manageable_orientation_bookings` (the queryset form of `_require_can_manage_booking`) plus the viewer's own; a member who runs nothing sees only their own. The Reservations page's Bookings tab (#627) is its twin, scoped by `membership.permissions.manageable_reservations` (the queryset form of `can_manage_equipment`). Both lists pass `honour_preview=True`, so an admin previewing as a member sees what that member would; action gates never pass it. Shared pieces live in `hub/bookings_tab.py` and `static/css/bookings-tab.css`.

## Reservation cards

The Reservations page and the guild page's Reservations tab (#502) build their grid with `equipment_views.reservation_cards(member, queryset)`, the one definition of a card. `partials/equipment_cards.html` reads the annotation and prefetches it adds, so render that partial only from its output; a bare queryset 500s the page.

## Announcements

The composer (`hub_compose*`) and the Announcements page share one visibility rule, `_announcement_rows(request, member)` in `hub/views.py`: a row is visible and actionable exactly when `_compose_audience_forbidden(request, draft.audience_value)` is `None` (admins short-circuit to every row). Every composer lookup that takes a `draft_pk` goes through `_handled_draft`, which applies it to resumable rows only. Drafts are shared; `author` is whoever saved last, and the sender once sent.

## Common Pattern

All views call `_get_hub_context(request)` for sidebar data (guild list, user initials) and `_get_member(request)` to get the logged-in `Member`. Views gracefully handle `member is None` (unlinked account).

## Forms (hub/forms.py)

- `VotePreferenceForm` — 3 guild FK selects (guild_1st, guild_2nd, guild_3rd)
- `ProfileSettingsForm` — edits Member fields (pronouns, about_me, discord_handle, etc.)
- `EmailPreferencesForm` — email notification toggles
- `BetaFeedbackForm` — bug/feature/general feedback; calls `form.send(user=user)`
- Self-service tab-entry form: `billing.forms.TabItemForm` with `context="member_tab_page"` (replaces the old `AddTabEntryForm` removed in v1.5.0)

## Templates

`templates/hub/` — one template per view. Layout uses shared `templates/base.html`.

## Template Tags

`hub/templatetags/hub_tags.py` — filters/tags used in hub templates.
