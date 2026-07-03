# Catalog ↔ Calendar consistency — decision + implementation spec (UAT #8)

**Status: Spec only — not yet approved to build.**
**Branch target:** `release-0.20.x` (PR #118). **VERSION stays `0.20.1`** — do not edit `plfog/version.py`.
**Source:** FOG UAT #8 — "Some classes listed on the Community Calendar are not present in the Class Catalog (e.g. Felix Glassblowing Class)." Roadmap slot: "Spec C" in `docs/superpowers/plans/2026-07-03-qa-uat-response.md`.

## Summary

The complaint conflates two independent mechanisms. Both are confirmed in code. One is expected behavior that only *reads* wrong (external subscribed-calendar events); the other is a genuine catalog/calendar asymmetry (an already-started multi-session series). This spec pins both precisely, recommends a light copy/invariant treatment for the first and a one-queryset alignment for the second, gives the BDD test plan, and recommends folding the whole thing into PR #118 as one small commit.

---

## Mechanism 1 — External iCal-feed events with no `ClassOffering` (expected; reads wrong)

The Community Calendar renders raw `CalendarEvent` rows synced from two external-feed families:

- **Named general feeds** — `CalendarFeed.ical_url`, configured at Site Settings → Calendar tab (`core/models.py:224-260`). Synced by `sync_calendar_feed()` / `sync_general_calendar()` with `source="general"` (`hub/calendar_service.py:121-139`).
- **Guild feeds** — `Guild.calendar_url` (`membership/models.py:855`). Synced by `sync_guild_calendar()` with `source="guild"` (`hub/calendar_service.py:110-118`).

Both write plain `CalendarEvent` rows via `_upsert_events()` (`hub/calendar_service.py:78-107`). The calendar view reads them straight from the DB — `events_qs = CalendarEvent.objects.filter(start_dt__date__gte=fetch_from, start_dt__date__lte=fetch_to)` at `hub/views.py:2123`, in `_get_calendar_context` (`hub/views.py:2082-2232`).

These rows have **no `ClassOffering` behind them** and their `source` is `"guild"`/`"general"`, never `"classes"`. They therefore can never appear in the Class Catalog, which is driven entirely by `ClassOffering` rows. A title like "Felix Glassblowing Class" is exactly what a subscribed Google Calendar produces — the word "Class" is upstream free text plfog does not control. **This is the most likely cause of the UAT report and is arguably correct behavior.**

The template already withholds catalog affordances from these events — the "Classes" badge and "Register →" button are gated strictly on `event.source == "classes"` in `templates/hub/partials/calendar_event_item.html:18` and `:34-41`; feed/guild events instead get a source-name badge in the feed's own color (`:42-53`). So the residual problem is purely perceptual: a title containing "Class" implies "this should be in the catalog," and there is no explicit cue that the event came from a subscribed calendar.

### Decision for Mechanism 1 (recommended — keep it light, no data/query change)

Keep external-feed events (excluding them would hide real makerspace activity), and do **not** try to reconcile them into the catalog. Address the perception with two low-cost measures:

1. **Lock the invariant with a test.** Catalog affordances (title→detail link, "Classes" badge, "Register →") must apply *only* when `source == "classes"`. This is already true in `calendar_event_item.html`; add a pin test (see BDD plan) so a future template change can't leak class styling onto a feed event.
2. **Add one line of explanatory microcopy** near the calendar legend / empty-state (the hint slot already exists in `templates/hub/partials/calendar_content.html:122` and `:138`), e.g. *"Colored events come from subscribed and guild calendars — they may not be plfog classes and won't all appear in the Class Catalog."* Pure copy; no model, query, or affordance change.

Rejected heavier alternative: adding an explicit "External" badge to every feed event. The source-name badge (`calendar_event_item.html:42-53`) already differentiates by name and color; a second badge adds visual noise for marginal clarity. Not recommended for v1.

---

## Mechanism 2 — Started-series asymmetry (genuine inconsistency)

**Catalog gate** — `ClassOfferingQuerySet.bookable()` at `classes/models.py:121-140`, specifically:

```python
.annotate(first_session_at=Min("sessions__starts_at"))
.filter(Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE) | Q(first_session_at__gte=now))
```

at `classes/models.py:136-137`. A dated offering (single or series) drops out of the catalog the instant its **first** session begins — "you can't join a series part-way through." This is what powers the public catalog list via `_browsable_classes()` (`classes/views.py:71-78`).

**Calendar gate** — `sync_local_class_events()` at `hub/calendar_service.py:142-201`, specifically the **per-session** filter at `hub/calendar_service.py:165-170`:

```python
qs = ClassSession.objects.filter(
    class_offering__status=ClassOffering.Status.PUBLISHED,
    class_offering__is_private=False,
    starts_at__gte=now,
    starts_at__lte=horizon,
)
```

This keeps every *individual future session*. So for an already-started 4-week series, sessions 2–4 are still `starts_at >= now`; they get materialized as `source="classes"` `CalendarEvent` rows (`url="/classes/<slug>/"`, `hub/calendar_service.py:177-191`) and keep showing on the Community Calendar — while `bookable()` has already removed the whole offering from the catalog. **That is the exact class-on-calendar / absent-from-catalog inconsistency the UAT names.**

**Why it's a dead-end for the member.** The calendar chip links to `public_class_detail` (`classes/views.py:282-287`), which fetches via `public()` (not `bookable()`), so the detail page loads and shows the full schedule with passed dates marked. But the Register action bounces: `register()` returns "Registration has closed for this class — it has already started." at `classes/views.py:500-502` because `offering.is_bookable` is False. So the member finds a class on the calendar, can't find it in the catalog, opens it, and cannot register — the worst of the three surfaces disagreeing.

**Same asymmetry on per-guild calendars.** `guild_calendar_entries()` builds synthetic class entries with the same per-session, non-`bookable()` filter at `hub/calendar_entries.py:67-72` (and, separately, omits the `is_private=False` guard that `sync_local_class_events` has). A started series shows through on guild detail calendars too.

Note: the codebase already documents a deliberate "future sessions are real inventory" stance for a *different* purpose — `ClassSessionQuerySet.upcoming_public()` at `classes/models.py:1053-1066` counts future sessions of started series. That count is about single-session dated inventory; it is not a mandate to advertise an unjoinable started series on the discovery calendar.

### Decision for Mechanism 2 (recommended — align the calendar to the catalog)

**Reuse the `bookable()` gate for the calendar's local-class sync** rather than keeping the per-session filter.

Member-experience rationale: the Community Calendar is a **discovery** surface for things a member can act on — its class events carry a "Register →" affordance. Advertising sessions 2–4 of a series nobody can join is a dead-end that directly produced this UAT report. `bookable()` ("you can't join a series partway through") is the authoritative member-facing rule; the calendar should honor the same rule so the three surfaces (catalog list, calendar, register) agree. Members already enrolled in a started series track their own dates via their registration page (`my_registration`), not the public calendar — so nothing legitimate is lost.

Rejected alternative: intentionally keep the remaining series dates on the calendar and document the asymmetry. This preserves the dead-end and keeps generating exactly this confusion; only justified if there were a "my enrolled classes" view need, which the personal registration page already covers.

---

## Concrete fix

### A. Calendar local-class sync — `hub/calendar_service.py:165-170` (primary fix)

Replace the per-session status/private filter with a `bookable()` gate on the offering. `bookable()` already enforces published + non-private (via `public()`), so those two filters are subsumed; the per-session `starts_at` window stays so a bookable class still only contributes its future, in-horizon sessions:

```python
from classes.models import ClassOffering, ClassSession
...
bookable_ids = ClassOffering.objects.bookable().values_list("pk", flat=True)
qs = ClassSession.objects.filter(
    class_offering_id__in=bookable_ids,
    starts_at__gte=now,
    starts_at__lte=horizon,
).select_related("class_offering", "class_offering__category")
```

Use `.values_list("pk", flat=True)` (not `class_offering__in=ClassOffering.objects.bookable()` directly) so the `annotate(...).order_by(...).distinct()` inside `bookable()` doesn't turn into an ordered/grouped correlated subquery. Behavior: a started series' offering isn't in `bookable()`, so none of its sessions materialize — it drops off the calendar exactly as it drops off the catalog. Single-session classes offered on multiple dates are separate offerings, each gated independently, so their still-future dates are unaffected. The existing purge at `hub/calendar_service.py:196` deletes the now-orphaned `source="classes"` rows on the next sync, so the fix is self-healing with no data migration.

### B. Guild calendar entries — `hub/calendar_entries.py:67-72` (recommended mirror)

Apply the same gate so per-guild calendars align and the missing `is_private` guard is fixed as a bonus:

```python
bookable_ids = ClassOffering.objects.bookable().values_list("pk", flat=True)
sessions = ClassSession.objects.filter(
    class_offering__category__guild=guild,
    class_offering_id__in=bookable_ids,
    starts_at__date__gte=fetch_from,
    starts_at__date__lte=fetch_to,
).select_related("class_offering")
```

### C. Template invariant + microcopy — Mechanism 1

- No code change needed to the affordance gating (`calendar_event_item.html:18,34-41` already correct); add the pin test in the BDD plan.
- Add the one-line explanatory hint in `templates/hub/partials/calendar_content.html` near the existing empty-state hints (`:122`, `:138`) or the legend.

**Fix size: small.** ~3 changed lines in `sync_local_class_events`, ~3 in `guild_calendar_entries`, one copy line in a template. No migration. Fat-model conventions respected — the gate logic stays in the `bookable()` manager method; the service and entry builders just consume it.

---

## BDD test plan (`*_spec.py`, `describe_*`/`it_*`, factory-boy)

Existing coverage lives in `tests/hub/calendar_service_spec.py` (`describe_sync_local_class_events`) using `ClassOfferingFactory` / `ClassSessionFactory` from `classes/factories.py`. Extend it.

**Mechanism 2 — `tests/hub/calendar_service_spec.py`, `describe_sync_local_class_events`:**
- `it_drops_a_started_multi_session_series` — a `SchedulingType.SERIES_PACKAGE`, published, non-private offering with sessions at now−3d, now+4d, now+11d (first already passed). Assert `sync_local_class_events()` creates **zero** `source="classes"` events for it — the still-future sessions must not appear. (This is the core regression test; it fails on today's code.)
- `it_keeps_a_not_yet_started_series` — a SERIES with all sessions in the future (first at now+2d) → every in-horizon session materializes.
- `it_keeps_future_single_dates_when_a_sibling_date_has_passed` — two `SINGLE_SESSION` offerings sharing a `grouping_key`, one past (dropped) and one future (kept) — proves per-offering gating is preserved and singles aren't harmed.
- Confirm the existing `it_skips_draft_private_and_past_sessions` still passes (subsumed by `bookable()` → `public()`).

**Parity assertion (new or in the same spec):**
- `it_shows_a_series_on_both_catalog_and_calendar_before_it_starts` then `it_hides_a_series_from_both_after_it_starts` — assert the same started series is absent from **both** `ClassOffering.objects.bookable()` (catalog) and the `source="classes"` calendar rows, and present in both before start. Locks the two surfaces together against future drift.

**Mechanism 2 mirror — `tests/hub/calendar_entries_spec.py`, `describe_guild_calendar_entries`:**
- `it_omits_a_started_series` / `it_keeps_a_future_series` for the guild synthetic entries.

**Mechanism 1 invariant — `tests/hub/community_calendar_spec.py` (or a render spec for `calendar_event_item.html`):**
- `it_does_not_present_a_feed_event_as_a_bookable_class` — render the calendar with a `source="general"` `CalendarEvent` whose title contains "Class"; assert the output has **no** "Register" link and **no** "Classes" badge, and that the feed-name badge is shown. Pins the "external events never read as catalog classes" invariant.

All new tests to the repo's 100% coverage / mutation-kill gate.

---

## Disposition for PR #118

Fold this into `release-0.20.x` as one small, self-contained commit (suggest label **C6 — catalog/calendar consistency**), sequenced after the in-flight C1–C5. It fits the branch conventions: additive, no migration, fat-model gate reuse, BDD specs. VERSION stays `0.20.1` (do not touch `plfog/version.py`). The started-series show-through is live on prod (0.19.x line) and is member-facing, so when the CHANGELOG is curated centrally it warrants one plain-language line, e.g. *"Classes that have already started no longer linger on the Community Calendar — the calendar now matches the Class Catalog."* Mechanism 1's microcopy needs no changelog entry. No "own release" needed.

---

### Critical Files for Implementation
- /home/josh/Code/plfog/hub/calendar_service.py (primary fix — `sync_local_class_events`, lines 165-170)
- /home/josh/Code/plfog/classes/models.py (`ClassOfferingQuerySet.bookable()`, lines 121-140 — the gate being reused)
- /home/josh/Code/plfog/hub/calendar_entries.py (mirror fix — `guild_calendar_entries`, lines 67-72)
- /home/josh/Code/plfog/templates/hub/partials/calendar_event_item.html (Mechanism 1 invariant + affordance gating, lines 18, 34-53)
- /home/josh/Code/plfog/tests/hub/calendar_service_spec.py (BDD home for the started-series and parity tests)
