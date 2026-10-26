# tours app

Facility tours: visits to the building for people curious about Past Lives, replacing SimplyBook. Not the in-app guided tours (`core/tours.py`, `core.models.TourState`, `hub.forms.TourSettingsForm`, `?tour=`, `static/js/pl_tour.js`), which walk a member around the portal; nothing here touches or reuses those names. The round ships in five parts: #947 (this app's base: the Tour Guide permission, guide hours with seats, days off, Tour Settings, the booking model), #948 (public booking on tours.pastlives.space and the visitor emails), #949 (Upcoming and Past tabs with staff actions), #950 (the cairn kiosk; SimplyBook code removed), #951 (Google Calendar per guide).

## Who

- **Tour Guide** is `AdminCapability.Capability.TOUR_GUIDE`, granted on a member's Permissions tab. Admins always have its abilities.
- Pages are gated by `hub.view_as.tour_guide_access_required` (Tour Guides and admins, by the actual role) and `fog_admin_required` (Tour Settings). The sidebar's Tours entry reads the twin `has_tour_guide_access` (`hub.context_processors._tours_nav`: no query for an admin, one for anyone else).
- Whose hours a request touches is decided once, in `tours.views._target_guide`: blank or the viewer's own pk is the viewer; anyone else is an admin's choice only (403 for a guide, before any form is read), and must give tours or already have hours (404 otherwise).
- Whose hours visitors are offered is `tours.models.guide_filter`: an active member holding Tour Guide, an active `fog_role` admin, or a superuser. Revoking the permission stops the offer at once and keeps the rows.

## Models

| Model | Notes |
|-------|-------|
| `FacilityTourSettings` | Singleton (pk 1) with `load()`, like `ClassSettings`. `accepting_bookings` and `closed_message`, `booking_horizon_days` (30, 1 to 365), `min_notice_hours` (24, 0 to 336), `cancel_cutoff_hours` (24, 0 to 336), `default_tour_minutes` (60), `default_seats` (6, 1 to 50). Named apart from the guided tours' `TourSettingsForm`. |
| `TourHours` | One guide's weekly window: `weekday`, `start_time`, `end_time` (half hour marks), `tour_minutes`, `seats`. Carved into back to back tours (`carve_spans`, wall clock, each instant made aware on its own). One guide's rows never overlap (`TourHoursFormSet` refuses it), so a guide and a start name one row. `objects.offered()` is the rows of people who give tours, with the permission in a subquery so the query can be locked. |
| `TourDayOff` | A day or a run of days a guide is away (`starts_on`, `ends_on`, `note`). Their hours offer nothing on those days; tours already booked stay booked. `objects.upcoming()` (not yet ended), `objects.touching(first, last)`, `label`. |
| `TourBooking` | One party on one guide's tour: `guide` (nullable, SET_NULL), `starts_at`, `ends_at`, `party_size`, `visitor_name`, `visitor_email`, `visitor_phone`, `user` (nullable), `status` (booked, cancelled, attended, no show), `cancelled_by`, `cancelled_at`, `self_serve_token` (random, unique), `created_at`. Booked and attended hold seats (`HOLDS_SEATS`); `objects.seats_taken(guide, starts_at)` and `objects.seats_taken_by_tour(...)` sum `party_size`. No screen makes one before #948. |

## Availability

Never stored: `tours/availability.py` carves the hours on every read, so a change to the hours, a day off or a setting is in the next answer, with no nightly job.

- `open_tours(first_day, last_day, *, guide=None, include_full=False, now=None) -> list[OpenTour]`: every bookable tour over local days, minus days off, past the horizon (`today + booking_horizon_days`, included) and inside the minimum notice (`now + min_notice_hours`). Two guides with the same hours give two tours. Full tours drop out unless `include_full`. Four queries for any range.
- `open_tour_at(guide, starts_at, *, lock=False, now=None) -> OpenTour | None`: one tour by guide and start, full or not, or None when it is not bookable now.
- `OpenTour`: `guide`, `hours_id`, `starts_at`, `ends_at`, `seats`, `seats_taken`, `seats_left`, `is_full`. `BookingWindow.at(now, settings)` is the horizon and notice as dates and an instant.
- Neither reads `accepting_bookings`: the booking page decides what a closed site shows, and staff keep moving tours while it is closed.

**Booking without overbooking (#948).** Inside `transaction.atomic()`: `tour = open_tour_at(guide, starts_at, lock=True)`; refuse when it is None or `tour.seats_left < party_size`; create the `TourBooking`; commit. `lock=True` takes `SELECT ... FOR UPDATE` on the guide's `TourHours` rows for that weekday (raising `RuntimeError` outside a transaction), so every booking of one tour queues on the same row lock, and on PostgreSQL's read committed each statement after the lock sees the bookings committed before it. A reschedule takes the lock of the tour it moves to.

## Views → URLs

Mounted at `/facility-tours/` (members only, in `MEMBER_ONLY_PATH_PREFIXES`); not `/tours/`, where hub's guided tours answer `/tours/<key>/state/`.

| View | URL name | Path |
|------|----------|------|
| `tour_hours` | `tours:hours` | `/facility-tours/` (the Tours page, My Hours tab; `?guide=<pk>` for an admin) |
| `tour_hours_save` | `tours:hours_save` | `/facility-tours/hours/save/` (POST `guide` and the `hours` formset) |
| `tour_day_off_save` | `tours:day_off_save` | `/facility-tours/days-off/save/` (POST `guide` and the `day_off` form; HTMX answers 204 + `HX-Redirect`, or the modal body with errors) |
| `tour_day_off_delete` | `tours:day_off_delete` | `/facility-tours/days-off/<pk>/delete/` |
| `tour_settings` | `tours:settings` | `/facility-tours/settings/` (Admin Tools > Tour Settings, admins only) |

Templates in `templates/tours/` (`hours.html`, `settings.html`, `partials/_hours_row.html`, `partials/_day_off_form.html`), styles in `static/css/tours.css`. The hours rows are the equipment Opening Hours editor's shape: `extra=0`, + Add hours clones a `<template>`, each row folds to a summary with Edit (`plTourHoursRow`, inline in `hours.html`), a saved row's Delete confirms and then saves the page. Specs in `tests/tours/`, the browser flow in `tests/e2e/tour_hours_spec.py`.
