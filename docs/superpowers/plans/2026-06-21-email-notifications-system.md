# Event Email Notifications & Digests — Future Spec (stub)

**Status:** Stub / future — not scheduled. Captured during the Guild Orientations brainstorm to flesh out later.
**Date:** 2026-06-21
**Related:** `2026-06-21-guild-orientations.md` (the orientation emails + `guild_joined` trigger are the first concrete
pieces of this).

---

## Idea

Send emails for "all sorts of events" beyond the per-action transactional ones — e.g. **a daily/weekly digest of new
guild members**, new bookings, new registrations, votes, etc. — with **admin-configurable cadence** and per-user
opt-out. The orientation work already lays the groundwork: it adds the `guild_joined` trigger + site activity, and
proves the digest sources exist.

## What already exists to build on

- `core.triggers.TRIGGERS` — a 40+ entry catalogue with `audience`, `force_email`, `push_default`, `email_default`.
- `NotificationPreference` (`core/models.py:659-673`) — per-user, per-trigger push/email opt-out.
- `notifications.dispatch()` (`core/notifications.py:18`) — in-app + push + email fan-out, preference-aware.
- `SiteActivity` (`core/models.py:529-625`) — append-only event log = the natural **digest source** (query by `kind`
  + `created_at` window).
- `run_scheduled_tasks` dispatcher + `ScheduledNotificationMarker` idempotency — the cron home for a digest job.
- `core.email.send()` + `TransactionalEmailLog` — audited HTML+text send.

## Rough shape (to be designed)

1. **Digest model/setting** — a small `DigestSubscription` or settings rows: which event kinds, cadence
   (off / daily / weekly), recipient scope (per-guild lead, admins, all members). Likely admin-configurable defaults +
   per-user overrides layered on `NotificationPreference`.
2. **Digest builder** — group recent `SiteActivity` rows since the last run into a single email ("3 new members joined
   Woodworkers this week", etc.). One email per recipient, not one-per-event.
3. **Cron** — `send_event_digests` management command, time-gated (daily ~morning UTC, weekly on a chosen weekday),
   idempotent via `ScheduledNotificationMarker` keyed by period.
4. **Template** — a digest email template (sectioned by event kind) under `templates/core/email/`.

## Open questions (for when this is picked up)

- Cadence granularity: global vs per-trigger vs per-user? Who sets the default?
- Which events are digest-worthy vs must stay instant/transactional?
- Recipient model: guild leads get their guild's digest; admins get site-wide; members opt in to what?
- Do digests replace or supplement the instant in-app notifications? (Likely supplement.)
- Quiet hours / max frequency caps to avoid spam?

## Not now

This is intentionally deferred. The orientation feature ships the first real per-event notifications
(`orientation_*`, `guild_joined`); revisit this digest layer once those are in use and we know which events people
actually want batched.
