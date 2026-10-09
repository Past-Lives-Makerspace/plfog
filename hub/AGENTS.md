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
| `beta_feedback` | `hub_beta_feedback` | `/feedback/` (the form, then "Your requests": the viewer's own `FeedbackRequest` rows, #693; `?sent=<pk>` and `#request-<pk>` open a row; `?category=feature` or `bug` preselects the category, #709) |
| `tab_detail` | `hub_tab_detail` | `/tab/` |
| `tab_history` | `hub_tab_history` | `/tab/history/` |
| `announcements_overview` | `hub_announcements` | `/announcements/` (Drafts and Sent tabs, `?tab=drafts\|sent`) |
| `announcement_sent` | `hub_announcement_sent` | `/announcements/sent/<pk>/` (read-only record of a sent or sending announcement) |
| `orientations_views.hub_orientations` | `hub_orientations` | `/orientations/` (every bookable orientation as a card; its controls post `next` so the member lands back, `views._orientation_return`; the header's "+ Add an Orientation" links to `hub_orientation_add`, #637 and #680) |
| `orientations_views.hub_orientation_add` | `hub_orientation_add` | `/orientations/add/` (Add an Orientation, #680: `NewOrientationTypeForm`, the Guild first, its choices and its gate `membership.permissions.guilds_for_new_orientation`; `?guild=<pk>` preselects; Save lands on the guild's Orientations page) |
| `orientations_views.hub_orientations_calendar_events` | `hub_orientations_calendar_events` | `/orientations/calendar/events/` (the Orientations page's Calendar view: grid and list for its navigation, `?shell=1` for the whole calendar on first open) |
| `feedback_views.hub_admin_feedback_inbox` | `hub_admin_feedback_inbox` | `/manage/feedback/` (admin only, #693: every feedback request, filterable by category and status; `hub_admin_feedback_request` at `/manage/feedback/<pk>/` sets status, note and GitHub link; `hub_admin_feedback_mark_live` POSTs Mark live) |
| `spotlight_views.hub_admin_spotlight` | `hub_admin_spotlight` | `/manage/spotlight/` (admin only, #708: the Spotlight text and meeting, saved by `hub_admin_spotlight_text` with `SpotlightTextForm.save_at` stamping `spotlight_text_changed_at` only when a line changed; a live preview of `partials/_spotlight.html` in both states; the open poll with Close now (`hub_admin_spotlight_poll_close`); New poll (`hub_admin_spotlight_poll_new`, `NewPollForm`, posting over an open poll goes through the replace-poll confirm); Past polls via `prepare_table`; `hub_admin_spotlight_poll` shows one poll's totals). `hub/spotlight.py` is the read model: `Spotlight.load(member, now)`, one query, with the meeting's next date kept until it ends, the open poll's tally, the member's own vote and both lines with their fallbacks. |
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

## The Spotlight (#709)

The top left of every hub page for members. `hub_sidebar` adds a lazy `spotlight` (`hub.spotlight.Spotlight.load(member, now)`, one query: the settings row, its meeting and the open poll's answers, counts and the member's vote as scalar subqueries), None for guests (#691), former members and signed-out visitors, who keep the version number and the plain changelog modal. `partials/_spotlight.html` renders Standard and Minimized; `<html data-spotlight-min>` picks one in CSS, set before first paint by an inline head script and kept by `static/js/spotlight.js` (an Alpine store, in the head before Alpine), which also keeps the seen signature (`Spotlight.seen_signature`) in localStorage and shows the yellow dot on Minimized when it differs. Details opens `components/modal.html` "spotlight-expanded" with `partials/_spotlight_expanded.html`: the poll card (`variant="panel"`), the meeting, Suggest a feature / Report a bug (`?category=`), and "Version X.Y · What changed", which shows `includes/_changelog_pages.html` (five a page; the plain modal on public, login and admin pages uses it too). A `#changelog-<slug>` link opens the panel on that entry's page. With no open poll, Standard and Expanded show the newest changelog entry instead (`LatestUpdate`, `partials/_spotlight_update.html`), which opens the changelog at it, and Minimized's first line falls back to its title. With no poll and no meeting the Spotlight shows only while `SiteConfiguration.spotlight_show_when_empty` is on; off, `Spotlight.__bool__` is False and the corner goes back to the logo and version number. A new release alone never lights the dot. Home repeats the Spotlight at the top for phones (`.pl-spotlight-home`, hidden over 768px).

## Common Pattern

All views call `_get_hub_context(request)` for sidebar data (guild list, user initials) and `_get_member(request)` to get the logged-in `Member`. Views gracefully handle `member is None` (unlinked account).

## Forms (hub/forms.py)

- `VotePreferenceForm` — 3 guild FK selects (guild_1st, guild_2nd, guild_3rd)
- `ProfileSettingsForm` — edits Member fields (pronouns, about_me, discord_handle, etc.)
- `EmailPreferencesForm` — email notification toggles
- `BetaFeedbackForm` — bug/feature/general feedback; `form.submit(user=user)` saves a `core.models.FeedbackRequest`, then emails the admins with a link to it
- `FeedbackRequestAdminForm` — the inbox's status, note and GitHub link (a plain Form, so the instance keeps its saved values until `apply_admin_update`)
- Self-service tab-entry form: `billing.forms.TabItemForm` with `context="member_tab_page"` (replaces the old `AddTabEntryForm` removed in v1.5.0)

## Templates

`templates/hub/` — one template per view. Layout uses shared `templates/base.html`.

## Template Tags

`hub/templatetags/hub_tags.py` — filters/tags used in hub templates.
