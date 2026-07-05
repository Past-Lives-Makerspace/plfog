# Space Reservations (meeting rooms + event space) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-04
**Surface:** FOG hub (`pastlives.test`) — a standalone `/reserve/` area, a "My reservations" list, a steward/admin review queue, plus a "Reserve" button injected into the interactive-map detail panel. Admin config via Unfold.
**Related:** Spec 1 of 2 — `docs/superpowers/plans/2026-07-04-interactive-space-map.md` (owns `Floorplan` / `MapHotspot` / `SpaceRequest`). This is **Spec 2 of 2**. Depends on the 0.21 `CommunityEvent` proposal/approval/Google-sync stack.

---

## 1. Summary

A member can **request to reserve a meeting room or the event space** for a specific date and time — straight from the interactive floorplan (Spec 1) or from a standalone `/reserve/` list. Before they pick a time they can see when the room is already busy, so they don't request a slot that's taken. Every request is **free** (a member perk — no checkout) and **needs a steward or admin to approve it** before it's confirmed; the requester gets a notification either way. Reserving the **event space** additionally, on approval, publishes a **Community Calendar event** (so the whole makerspace sees it, and it syncs to Google Calendar) — reusing the exact publish choke point the 0.21 events feature already built.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Approval model | **Approval for everything.** Every meeting-room and event-space reservation is a request an admin/steward approves; nothing self-confirms. Mirrors `OrientationBooking` REQUESTED→CONFIRMED and `CommunityEvent` proposal review. |
| Payment | **Free.** A reservation only notifies the approver + requester. No Stripe, no tab entry. |
| Event space → event | Approving a reservation on a resource with `requires_event=True` **builds a `CommunityEvent` (COMMUNITY, site-wide) and calls `.publish(actor=reviewer)`** — reusing the announce + Google-sync choke point. Cancel/decline removes it. |
| Overlap engine | **Application-level guard in `clean()`** — NO Postgres `ExclusionConstraint` (CI runs on SQLite). Approved reservations hard-block any overlapping request; a member's own pending/approved overlapping request is also blocked (idempotency). |
| Pending vs pending | A **different** member's overlapping *pending* request does **not** block (the approver picks one and sees a conflict flag). Only *approved* reservations lock a slot. *(Flagged open — see §10.)* |
| Who approves ("stewards") | **Admins always.** A resource may also name a `steward_guild`; its leadership then reviews that resource's requests too (reusing `guild_leadership_or_admins`). Blank `steward_guild` ⇒ admins only. *(Flagged open — see §10.)* |
| Resource CRUD | Reservable resources are a **handful of rows** (a few rooms + the event space) → managed in **Django/Unfold admin**, not a bespoke hub editor (YAGNI). |
| Seam with Spec 1 | Spec 1 owns `MapHotspot.kind` with `meeting_room` + `event_space` values. This spec adds only the **resource + reservation models + reserve UI**; it links via a `ReservableResource.hotspot` FK it owns. Degrades to the standalone `/reserve/` list if the map isn't built yet. |

---

## 2. What already exists (reuse, don't reinvent)

This build is **assembly**. Every moving part already ships on `feat-interactive-space-map` (based on `release-0.21.0`) — verified in the worktree.

| Need | Existing thing | Location |
|---|---|---|
| Request → review → decision lifecycle | `OrientationBooking` (REQUESTED/CONFIRMED/DECLINED/CANCELLED) with `confirm()`/`decline()`/`cancel()` + partial `UniqueConstraint` on an active row | `membership/models.py:3613`, methods `:3687`/`:3694`/`:3701`, constraint `:3667` |
| Time-slot booking guards + domain exception | `OrientationSlot.book()` guards + `OrientationError` | `membership/models.py:3553`, `OrientationError` `:3331` |
| Approve-with-notes + emit fan-out | `CommunityEvent.submit_for_review`/`approve`/`decline`, `_emit_submitted`/`_emit_decision`, `InvalidEventTransition` | `membership/models.py:2375`/`2425`/`2465`/`2499`/`2526`, exc `:2006` |
| Event-space "make it live" choke point | `CommunityEvent.publish(actor=…)` → `announce()` + Google push; `remove_from_google()` to unwind | `membership/models.py:2330`, `announce` `:2303`, `remove_from_google` `:2368` |
| Google Calendar push/delete (never raises) | `push_community_event` / `remove_community_event` | `core/integrations/google_calendar.py:187` / `:222` |
| COMMUNITY event shape + the guild/type constraint | `CommunityEvent.EventType.COMMUNITY` (guild must be NULL) | `membership/models.py:2026`, check `ck_communityevent_guild_matches_type` `:2190` |
| Reviewer scope + pending queue helpers | `_reviewer_guild_scope` / `_pending_for_scope` (admin=all, staff=their guilds) | `hub/views.py:2234` / `:2248` |
| Review-queue + decision view pattern | `event_review_queue` / `event_review_decision` (+ `EventDecisionForm`) | `hub/views.py:2361` / `:2384`; form `hub/forms.py:1037` |
| Propose/edit + Django-messages full-page flow | `propose_event` | `hub/views.py:2257` |
| Notification spine | `emit(event_key, *, actor, target, context, url, period, …)` | `core/events/emit.py:43` |
| Approver / requester resolvers | `guild_leadership_or_admins` (`:140`), `fog_admins` (`:95`), `single_user` (`:434`) | `core/events/resolvers.py` |
| Event registration + curated copy | `_NEW_EVENTS` list + `EventCopy` entries; **"Spaces" trigger category already exists** | `core/events/registry.py:339`; copy `core/events/copy.py:724`; category `core/triggers.py:108`, order `:121` |
| Dark-mode datetime-local widget + `showPicker()` | `CommunityEventForm` / `OrientationSlotForm` widget recipe | `hub/forms.py:986` / `:946` |
| Availability rendering (calendar entries) | `CalendarEntry` duck-type + `calendar_service` | `hub/calendar_entries.py`, `hub/calendar_service.py` |
| The `Space`/`Lease` Airtable read-model | `Space.full_price` (can be `None`), `current_occupants`, `SpaceQuerySet.available()` | `membership/models.py:3043` (props `:3117`/`:3126`) |
| Components | `modal.html`, `confirm_modal.html` (HTMX or plain-POST mode), `form_field.html`, `toggle.html`, `page_header.html`; `trigger_toast` | `templates/components/…`; `hub/toast.py` (imported `hub/views.py:43`) |
| Member/admin gating | `_get_member` (`:81`), `_viewing_as_admin` (`:588`), `_require_admin` (`:594`); active test `Member.objects.active()` (`:127`) | `hub/views.py` |

**Genuine gaps to close (small):**
1. Two new models — `ReservableResource`, `SpaceReservation` — plus a domain exception `ReservationError`.
2. **Overlap availability logic** — the one net-new piece (no overlap check exists anywhere in the repo; `Lease` has none either). Application-level, in `clean()` + a manager method.
3. Reserve/My-reservations/review templates + views + forms, and a `_reserve_button.html` partial Spec 1's detail panel includes.
4. Four new emit events under the **Spaces** category + their copy.

---

## 3. Where the code lives

Home app is **`membership`** (models + logic) so the new code stays inside the coverage/mypy source set (`["plfog", "core", "membership"]`) — same home as `CommunityEvent`, `OrientationBooking`, `Space`. Views/forms/templates live in `hub`, matching the events feature exactly.

```
membership/
  models.py                 # + ReservableResource, SpaceReservation, ReservationError,
                            #   their QuerySets. (Appended near Space/Lease + OrientationBooking.)
  admin.py                  # + ReservableResourceAdmin (Unfold-styled ModelAdmin)
  migrations/
    00xx_reservable_resource_and_reservation.py   # additive; reverse = drop tables
    00yy_reservation_hotspot_link.py              # depends_on Spec 1's MapHotspot migration
hub/
  views.py                  # + reserve_index, reserve_resource, reserve_availability,
                            #   my_reservations, reservation_cancel,
                            #   reservation_review_queue, reservation_review_decision
                            #   + helpers _reservation_review_scope / _pending_reservations_for_scope
  forms.py                  # + SpaceReservationForm, ReservationDecisionForm
  urls.py                   # + /reserve/ routes (see §6)
templates/hub/
  reservations.html                 # standalone resource list (entry point / fallback)
  reserve_resource.html             # the request form + busy-times panel
  my_reservations.html              # the member's own reservations + Cancel
  reservation_review_queue.html     # steward/admin queue (mirrors event_review_queue.html)
  partials/
    _reserve_button.html            # included by Spec 1's map detail panel
    _reservation_busy_times.html    # HTMX partial for the availability panel (server-rendered on GET too)
    _reservation_row.html           # one My-reservations row; the 200 cancel swap-target
    _reservation_decision_modal.html # steward decline modal (mirrors _event_decision_modal.html)
static/css/hub.css        # + .pl-reserve-* classes (theme tokens only)
core/events/
  registry.py               # + 4 EventType entries in _NEW_EVENTS
  copy.py                   # + 4 EventCopy entries
tests/membership/…, tests/hub/…, tests/membership/factories.py   # see §9
plfog/version.py          # LAST phase only
```

---

## 4. Data model

### `ReservableResource` (new)

A bookable room/area. Config-level; a handful of rows.

| Field | Type | Note |
|---|---|---|
| `name` | `CharField(max_length=120)` | Display name — "Meeting Room A", "Event Space". `help_text="Room/area name members see when reserving."` |
| `kind` | `CharField(choices=Kind.choices)` | `Kind` TextChoices: `MEETING_ROOM = "meeting_room"`, `EVENT_SPACE = "event_space"` — **the same string values Spec 1 puts on `MapHotspot.kind`.** |
| `hotspot` | `OneToOneField("MapHotspot", null=True, blank=True, on_delete=SET_NULL, related_name="resource")` | Links the map region to this resource (Spec 1's model). **Added in a second migration** that `depends_on` Spec 1's MapHotspot migration; degrade = ship without it, list-only. |
| `space` | `ForeignKey("Space", null=True, blank=True, on_delete=SET_NULL, related_name="reservable_resources")` | Optional link to the Airtable `Space` row (for photo/price display). **Never written back** (Airtable read-only). |
| `capacity` | `PositiveSmallIntegerField(null=True, blank=True)` | Optional "seats N". |
| `min_duration_minutes` | `PositiveIntegerField(null=True, blank=True)` | Optional floor on booking length. |
| `max_duration_minutes` | `PositiveIntegerField(null=True, blank=True)` | Optional ceiling. |
| `requires_event` | `BooleanField(default=False)` | `True` for the event space. `help_text="When on, approving a reservation also publishes a Community Calendar event and syncs it to Google."` |
| `steward_guild` | `ForeignKey("Guild", null=True, blank=True, on_delete=SET_NULL, related_name="stewarded_resources")` | Whose leadership reviews requests (∪ admins). Blank ⇒ admins only. |
| `is_active` | `BooleanField(default=True)` | Inactive resources are hidden from members + reject new requests. |
| `description` | `TextField(blank=True, default="")` | Optional blurb shown on the reserve page. |
| `created_at` / `updated_at` | `auto_now_add` / `auto_now` | |

- **Manager** `ReservableResourceQuerySet(models.QuerySet)`: `active()` (`is_active=True`), `meeting_rooms()`, `event_spaces()`, `for_kind(kind)`. `objects = ReservableResourceQuerySet.as_manager()`.
- **Meta:** `ordering = ["kind", "name"]`; constraint `CheckConstraint(condition=Q(requires_event=False) | Q(kind="event_space"), name="ck_reservableresource_event_requires_space")` (only the event space auto-creates events).
- `__str__` → `f"{self.name} ({self.get_kind_display()})"`.
- Property `notify_context` → `{"guild": self.steward_guild}` (fed to the approver resolver; `None` guild → admins, exactly as `guild_leadership_or_admins` handles it).

### `SpaceReservation` (new)

A member's time-boxed reservation request + its review outcome. Lifecycle mirrors `OrientationBooking.Status` and `CommunityEvent`'s review fields.

| Field | Type | Note |
|---|---|---|
| `resource` | `ForeignKey(ReservableResource, on_delete=PROTECT, related_name="reservations")` | PROTECT so a resource with history can't be silently deleted. |
| `member` | `ForeignKey(Member, on_delete=CASCADE, related_name="space_reservations")` | The requester. |
| `starts_at` | `DateTimeField` | Aware datetime. |
| `ends_at` | `DateTimeField` | Aware; must be `> starts_at` (check constraint + `clean`). |
| `purpose` | `CharField(max_length=120)` | Required. Becomes the `CommunityEvent.title` for the event space. |
| `notes` | `TextField(blank=True, default="")` | Optional note to the steward. |
| `status` | `CharField(choices=Status.choices, default=PENDING)` | `Status`: `PENDING = "pending"`, `APPROVED = "approved"`, `DECLINED = "declined"`, `CANCELLED = "cancelled"`. |
| `reviewed_by` | `ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | Steward/admin who decided. |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | |
| `review_notes` | `TextField(blank=True, default="")` | Reason shown to the requester on a decline. |
| `created_event` | `ForeignKey("CommunityEvent", null=True, blank=True, on_delete=SET_NULL, related_name="+")` | The event-space event, set on approve; nulled/removed on cancel/decline. |
| `created_at` / `updated_at` | `auto_now_add` / `auto_now` | |

- **Manager** `SpaceReservationQuerySet(models.QuerySet)`:
  - `active()` → `status__in=[PENDING, APPROVED]` (the states that occupy a slot — mirrors `OrientationBookingQuerySet.active()` `:3598`).
  - `pending()` → `status=PENDING`.
  - `approved()` → `status=APPROVED`.
  - `for_member(member)`, `for_resource(resource)`, `upcoming()` → `active().filter(starts_at__gte=timezone.now())`.
  - **`overlapping(resource, starts, ends, *, statuses)`** → the overlap query (see §5). Predicate: `filter(resource=resource, status__in=statuses, starts_at__lt=ends, ends_at__gt=starts)`.
- **Meta:**
  - `ordering = ["-created_at"]`.
  - `indexes = [Index(fields=["resource", "starts_at"], name="idx_spacereservation_res_start"), Index(fields=["status"], condition=Q(status="pending"), name="idx_spacereservation_pending")]` (partial index for the review queue).
  - `constraints = [CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="ck_spacereservation_end_after_start")]`. *(No DB overlap/exclusion constraint — SQLite CI can't run one; overlap lives in `clean()`.)*
- `__str__` → `f"{self.member.display_name} — {self.resource.name} ({self.starts_at:%Y-%m-%d %H:%M}, {self.get_status_display()})"`.

**Migrations.** (1) `ReservableResource` + `SpaceReservation` (reverse = drop both tables). (2) Adds `ReservableResource.hotspot` with `dependencies=[…Spec-1 MapHotspot migration…]` (reverse = remove field). Keeping the FK in its own migration is what lets Spec 2 land even if Spec 1's migration number isn't final yet.

---

## 5. Business logic (fat models)

New domain exception (next to `OrientationError` `membership/models.py:3331`):

```python
class ReservationError(Exception):
    """Raised when a space reservation can't be made or transitioned."""
```

### Overlap / availability — the net-new logic

Two intervals `[s1, e1)` and `[s2, e2)` overlap **iff** `s1 < e2 and s2 < e1`. Adjacent bookings (`e1 == s2`) do **not** overlap. In the ORM this is the manager method:

```python
def overlapping(self, resource, starts, ends, *, statuses):
    return self.filter(resource=resource, status__in=statuses,
                       starts_at__lt=ends, ends_at__gt=starts)
```

`SpaceReservation.clean()` (called by the form and re-checked on approve) validates, in order, raising `ValidationError` with a member-readable message:

1. **Ordering** — `ends_at <= starts_at` → `"End time must be after the start time."`
2. **Future** — `starts_at <= timezone.now()` → `"Pick a start time in the future."`
3. **Resource active** — `not resource.is_active` → `"This space isn't available for reservations right now."`
4. **Duration bounds** — length vs `min/max_duration_minutes` → `"Reservations here must be at least/at most N minutes."`
5. **Approved conflict (hard block, all members)** — `overlapping(resource, s, e, statuses=[APPROVED]).exclude(pk=self.pk).exists()` → `"That space is already reserved from 2:00–4:00 PM on Sat, Jul 12. Pick another time."` (Times rendered in the makerspace tz.)
6. **Own duplicate (hard block, this member)** — `overlapping(resource, s, e, statuses=[PENDING, APPROVED]).filter(member=self.member).exclude(pk=self.pk).exists()` → `"You already have a request for this space at that time."` This is the idempotency guard preventing duplicate/conflicting pending per `(member, resource, time)`.

A **different** member's overlapping *pending* request is intentionally NOT rejected — approval is what locks a slot, and the queue flags the conflict so the steward chooses (see §10).

### `SpaceReservation` methods

- **`when_display` (property)** → the makerspace-tz start–end range for notification copy and tooltips, e.g. `"Sat, Jul 12 · 2:00 PM – 4:00 PM"`. Built with `timezone.localtime(self.starts_at)` / `self.ends_at` exactly like `CommunityEvent.when_display` (`membership/models.py:2277`). This is the single source for every `"when"` value in the §7 emit contexts.
- **Validation happens pre-persist, never after a `save()`.** The form (`SpaceReservationForm`) attaches `member` / `resource` / `status = PENDING` to the unsaved instance **before** validation, so `clean()`'s overlap query can read `self.member` / `self.resource`; `is_valid()` runs `full_clean()` on that in-memory instance. `submit()` is therefore the **single save point** and assumes a clean instance — it never runs `full_clean()` after writing a row (which would risk an orphan invalid PENDING row + a 500 on a late conflict). See the `submit()`/form flow below.
- **`submit(*, by: Member) -> None`** (create path): the instance is already validated + attributed by the form. Guards `resource.is_active` and `by.status == Member.Status.ACTIVE`, else `ReservationError`. Wrapped in **`transaction.atomic()`**: it `save()`s the PENDING row, then `emit("space.reservation_submitted", …)` to the approver. Timestamped `period=f"reservation:{pk}:submitted:{timezone.now().timestamp()}"` so a resubmission re-notifies (mirrors `_emit_submitted` `:2499`). The view calls `form.is_valid()` first and, on a `ValidationError`, re-renders the form — an invalid row is never persisted.
- **`approve(*, reviewer: User) -> None`**: guard `status == PENDING` else `ReservationError("This reservation was already handled.")`. Wrap the conflict re-check **and** the save in **`transaction.atomic()`**, taking a **`select_for_update()`** row lock over this resource's APPROVED reservations before re-running the approved-overlap check (race guard — stops two admins concurrently approving two overlapping pendings on Postgres/prod); a now-conflicting request raises `ReservationError`. *(SQLite — CI/local — no-ops `select_for_update`, but the manual, human-paced approval flow makes the residual race negligible there.)* Inside the transaction: set `APPROVED`, `reviewed_by`, `reviewed_at`; if `resource.requires_event`, build + publish the `CommunityEvent` (below) and store `created_event`; `save()`. Then `emit("space.reservation_approved", …)` to the requester (`SINGLE_USER`), `period=f"reservation:{pk}:approved"`.
- **`decline(*, reviewer: User, notes: str) -> None`**: guard `status == PENDING`; `notes` required → `ValueError("A decline needs a note so the requester knows why.")` (mirrors `CommunityEvent.decline` `:2465`). Set `DECLINED` + review fields. No event ever existed (pending never created one). `emit("space.reservation_declined", …)` to the requester, `period=f"reservation:{pk}:declined"`.
- **`cancel(*, by: User) -> None`**: guard `status in {PENDING, APPROVED}` else `ReservationError`. If `created_event` is set: `created_event.remove_from_google()` then `created_event.delete()` (undo the publish — `remove_from_google` `:2368` needs the stored Google ids, so call it *before* delete), and clear `created_event`. Set `CANCELLED`. `emit("space.reservation_cancelled", …)` to the approver so they know the slot freed, `period=f"reservation:{pk}:cancelled"`. *(The view enforces that only the owning member — or an admin — may cancel.)*

### Building the event-space `CommunityEvent`

Private helper `SpaceReservation._build_and_publish_event(*, reviewer) -> CommunityEvent`:

```python
event = CommunityEvent(
    event_type=CommunityEvent.EventType.COMMUNITY,   # site-wide → guild must be None (check :2190)
    guild=None,
    title=self.purpose,
    starts_at=self.starts_at,
    ends_at=self.ends_at,
    location=self.resource.name,
    description=f"Event-space reservation by {self.member.display_name}.",
    created_by=self.member.user,
    submitted_by=self.member.user,
    moderation_state=CommunityEvent.ModerationState.PUBLISHED,  # the reservation review IS the approval
)
event.save()
event.publish(actor=reviewer)   # announce (in-app + Discord) + Google push — the one choke point
return event
```

`publish()` (`:2330`) is best-effort on the Google side (`push_to_google` records `FAILED`/`PENDING`, never raises), so a Google outage never blocks reservation approval — the same guarantee the events feature relies on.

All notification URLs are absolute via `settings.MEMBER_BASE_URL + reverse(...)` (never `/`), matching `_emit_submitted`/`_emit_decision`.

Views stay thin: parse → call `form.is_valid()` / a model method → toast or redirect. All validation is in the form/`clean()`; all state changes are model methods.

---

## 6. UI / UX  ← completeness checklist applied per screen

URL routes added to `hub/urls.py` (next to the `/info/` and `/events/` blocks `:113`/`:196`):

```
path("reserve/", views.reserve_index, name="hub_reserve_index"),
path("reserve/<int:pk>/", views.reserve_resource, name="hub_reserve_resource"),
path("reserve/<int:pk>/availability/", views.reserve_availability, name="hub_reserve_availability"),
path("reservations/", views.my_reservations, name="hub_my_reservations"),
path("reservations/<int:pk>/cancel/", views.reservation_cancel, name="hub_reservation_cancel"),
path("reserve/review/", views.reservation_review_queue, name="hub_reservation_review_queue"),
path("reserve/review/<int:pk>/decision/", views.reservation_review_decision, name="hub_reservation_review_decision"),
```

**Access/gating (stated defaults):** viewing the resource list = any logged-in member; **requesting** = `@login_required` + active membership (`member.status == Member.Status.ACTIVE`); a non-active/guest member sees the reserve CTA replaced by "Your membership needs to be active to reserve a space." **`reserve_availability` is `@login_required` + active member too** — it is an HTMX endpoint that returns approved busy-windows, so it carries the same gate as the reserve page and never leaks reservation times to anonymous callers. The review queue is gated by `_reservation_review_scope` (admin = all resources; a steward = resources whose `steward_guild` they lead/staff), a copy of `_reviewer_guild_scope` `:2234`.

### Screen A — Reserve index (entry point + fallback)  `templates/hub/reservations.html`

- **Layout & container:** dedicated page, `page_header.html` (`title="Reserve a space"`, `description`, `action_url=hub_my_reservations`, `action_label="My reservations"`). Body = two `hub-card` sections ("Meeting rooms", "Event space"), each a responsive grid of resource cards. Stewards/admins also see a "Review requests (N)" button linking to the queue when `pending_count > 0`.
- **Per resource card:** name, `get_kind_display`, capacity ("Seats 8"), optional `space.photo`, optional price via `space.full_price` (`None` → "Free to reserve" — this is a *member perk*, so no price line unless the linked Space carries one; never `var(--surface)` fallbacks). Primary button **"Reserve"** → `hub_reserve_resource`. If the member already has a pending request on this resource: button becomes a muted **"Request pending"** chip linking to My reservations (idempotency surfaced).
- **States:** empty (no active resources) → "No spaces are open for reservations yet." Inactive membership → CTA swapped for the activate message. No loading state (static page).
- **Dark/light + mobile:** `hub-card` + `pl-btn` tokens; grid `repeat(auto-fill, minmax(16rem,1fr))` reflows to one column; 8px spacing.

### Screen B — Reserve form + availability  `templates/hub/reserve_resource.html`

The task's core screen. 4 fields → **dedicated page** (FRONTEND interaction table), not a modal, because the busy-times context sits beside it.

- **Layout:** `page_header.html` (`title=f"Reserve {resource.name}"`). Two-column on desktop (form left, busy-times right), stacked on mobile. Both in `hub-card`s.
- **Form** (`SpaceReservationForm`, all fields via **`form_field.html`** inside a `.hub-form-group` scope so inputs are theme-correct):
  - `starts_at`, `ends_at` — `datetime-local` widgets copied verbatim from `CommunityEventForm` `hub/forms.py:986` (`onclick="this.showPicker?.()"`, `input_formats=["%Y-%m-%dT%H:%M", …]`). **Dark-mode:** `hub.css` inverts `::-webkit-calendar-picker-indicator` (`filter: invert(1)`), reset under `[data-theme="light"]` (`filter: none`); the whole field opens the picker.
  - `purpose` — `CharField`, required, hint "What are you using the space for? (Shown to the steward, and as the event title for the event space.)"
  - `notes` — `Textarea(rows=3)`, optional.
  - Hidden: none. (Duration is derived from start/end; if a resource sets min/max, the hint states the bounds.)
  - **Submit:** a single primary **"Request reservation"** button (`pl-btn pl-btn--primary`), full-width on mobile. Full-page POST → the view calls `form.is_valid()`; on success `submit()` runs and the view issues `messages.success(request, "Reservation requested — you'll get a note when a steward responds.")` + redirect to `hub_my_reservations`. (Full-page post ⇒ Django messages, not a toast — per the rubric.)
- **Event-space public-broadcast notice (`requires_event` resources only):** a visible callout above the Submit button — **"Heads up: when a steward approves this, your reservation becomes a public Community-Calendar event titled with your purpose above, and it's announced in Discord."** So "my birthday party" isn't a surprise site-wide broadcast. Rendered only when `resource.requires_event`; a `pl-reserve-notice` info box (theme tokens).
- **Error state:** overlap / past-time / duration errors render as field errors under the offending field (`form_field.html` shows them). The approved-overlap message (§5 rule 5) names the conflicting window so the member can immediately retry. Because validation runs pre-persist (§5), a late-arriving conflict re-renders the form with the error — it never 500s or leaves a stray row.
- **Availability panel** (`_reservation_busy_times.html`): a compact **next-7-days busy list** — approved reservations only, rendered as read-only "Sat Jul 12 · 2:00–4:00 PM — busy" rows, grouped by day. **Server-rendered on first paint:** the `reserve_resource` GET view passes the upcoming week's approved busy-windows into the template so the panel is populated the moment the page loads (the whole point — the member sees what's busy *before* touching the fields). **HTMX refresh:** the panel is `hx-get hub_reserve_availability`, `hx-trigger="load, change from:#id_starts_at"` — `load` re-syncs on mount, and picking a start date re-fetches that day's neighborhood; both show an in-flight **loading** indicator ("Checking availability…"). It reuses the `CalendarEntry` duck-type/`start_dt`/`end_dt` shape (`hub/calendar_entries.py`) if we later fold these into the shared grid; v1 keeps it a scoped list (YAGNI).
  - **Empty state:** "This space is free all week — pick any time."
  - A persistent note: **"Pending requests aren't shown here — your time isn't held until a steward approves it."** (Makes "pending ≠ confirmed" explicit.)
- **Back/cancel:** a secondary "Back to spaces" link — no dead end.
- **Mobile:** columns stack; the datetime inputs are full-width; tap targets are real buttons.

### Screen C — My reservations  `templates/hub/my_reservations.html`

- **Layout:** dedicated page; a list of `hub-card` rows, newest first, from `SpaceReservation.objects.for_member(member).select_related("resource")`.
- **Per row:** resource name (linked to the reserve page), the when (makerspace-tz range), purpose, and a **status badge**: PENDING → "Awaiting approval" (amber), APPROVED → "Confirmed" (green) — for the event space also "On the Community Calendar" with a link, DECLINED → "Declined" + the steward's `review_notes`, CANCELLED → "Cancelled".
- **Cancel (destructive):** on PENDING/APPROVED rows, a **`pl-btn pl-btn--danger pl-btn--sm`** button (never a toggle), `margin-top:0.75rem`, wired to **`confirm_modal.html`** (`confirm_id=f"cancel-res-{pk}"`, `confirm_message="Cancel your reservation for Meeting Room A on Sat, Jul 12? This frees the space for others."`, `confirm_hx_post=hub_reservation_cancel`, `confirm_hx_target` = the row's element, `hx-swap="outerHTML"`). The cancel view returns **`200` with the re-rendered row partial** (`templates/hub/partials/_reservation_row.html` now in its Cancelled state, Cancel button gone) **plus `trigger_toast(resp, "Reservation cancelled.", "success")`** on that same response — a bare `204` is a no-swap in HTMX, so the row would keep showing "Confirmed" and a live Cancel button. `200` + the partial swaps the row *and* fires the toast. Consequence stated: for the event space, cancelling also removes the Community Calendar event.
- **States:** empty → "You haven't reserved any spaces yet. Browse spaces to reserve →" (links to Screen A). Loading → the confirm-modal button shows its HTMX in-flight state. Error (e.g. already handled) → the view returns the row partial unchanged (or a `400`) with an error toast — never a silent no-op.
- **Dark/light + mobile:** badges use status tokens (reuse existing `.pl-badge--*` if present, else add `pl-reserve-badge--{pending,approved,declined}` in `hub.css`, both themes); rows stack on mobile.

### Screen D — Steward/admin review queue  `templates/hub/reservation_review_queue.html`

Mirrors `event_review_queue.html` + `event_review_decision` (`hub/views.py:2361`/`:2384`) almost exactly.

- **Layout:** dedicated page; pending reservations (`_pending_reservations_for_scope(scope)`, ordered by `starts_at`), each a `hub-card` with requester, resource, when, purpose, notes, and a **conflict flag** ("⚠ Overlaps another pending request") when a different pending request overlaps — computed via `overlapping(..., statuses=[PENDING]).exclude(pk=…)`.
- **Controls per row:**
  - **Approve** → `confirm_modal.html` (plain-POST mode, decision carried in the query string exactly like the events queue at `:2402`) → "Approve this reservation? The requester is notified" (+ "and a Community Calendar event is published" when `resource.requires_event`).
  - **Decline** → opens a **decision modal**, not an inline `x-show` textarea. Add `templates/hub/partials/_reservation_decision_modal.html` mirroring the real events queue's `partials/_event_decision_modal.html` (a `modal.html` with a required note `Textarea` in a `.hub-form-group`, opened via `@click="$dispatch('open-modal', 'decline-modal-{{ pk }}')"`). This sidesteps the `x-show`+inline-`display` trap the spec warns about, and gives error-re-reveal for free: on a blank-note validation re-render, the template re-opens the modal with its note error, exactly like `event_review_queue.html`'s `x-init="$nextTick(() => { {% if open_decision_for %}$dispatch('open-modal', '{{ open_decision_kind }}-modal-{{ open_decision_for }}'){% endif %} })"` (`templates/hub/event_review_queue.html:5`). `ReservationDecisionForm` requires the note for a decline.
  - Both post to `hub_reservation_review_decision`; the view calls `approve()`/`decline()`, catches `ReservationError`/stale-state → `messages.info("That reservation was already handled.")`, and redirects back (full-page → Django messages).
- **States:** empty → "No pending reservation requests." Error → the note-required validation re-renders the queue with the decline modal re-opened and the field error shown (never a silent redirect), mirroring `event_review_decision`'s re-render at `:2408`.
- **Gating:** `_reservation_review_scope` returns `True` (admin), a queryset of stewarded resources (steward), or `None` → `403`.
- **Nav pending badge:** so a steward who never opens `/reserve/` still sees pending work (not just the Screen A button + email), expose a `reservation_review_pending_count` in the hub context and render a count badge on the sidebar/nav "Reservations" link — reusing the `review_pending_count` pattern the events feature sets at `hub/views.py:2777` (`_pending_reservations_for_scope(scope).count()` when the requester can review, else `0`).
- **Dark/light + mobile:** tokens only; the decline modal's textarea sits in a `.hub-form-group`; cards stack on mobile.

### Screen E — "Reserve" button on the map detail panel (the seam)  `templates/hub/partials/_reserve_button.html`

- Spec 1's map detail panel `{% include "hub/partials/_reserve_button.html" with hotspot=hotspot %}` for `meeting_room`/`event_space` hotspots. This partial (owned here) renders:
  - If `hotspot.resource` exists and `is_active` → a **"Reserve this space"** `pl-btn pl-btn--primary` link to `hub_reserve_resource`.
  - If the member already has a pending request → "You have a pending request" chip → My reservations.
  - If membership inactive/guest → the activate/login prompt.
  - If no linked resource → render nothing (so Spec 1's panel degrades cleanly).
- **Degradation without Spec 1:** the whole map is optional — resources are fully reachable via Screen A (`/reserve/`). The `hotspot` FK is null until an admin links a region; the standalone list is the guaranteed entry point.

### Admin — `ReservableResourceAdmin` (Unfold)

`membership/admin.py`: a ModelAdmin with `list_display = ["name", "kind", "capacity", "requires_event", "steward_guild", "is_active"]`, `list_filter = ["kind", "is_active"]`, and the fields grouped. This is standard Unfold admin — its own Save; no bespoke hub CRUD (only ~3 rows). Flagged in §10 in case a hub-side editor is later wanted.

---

## 7. Notifications / emails / activity

Four new events, registered in `_NEW_EVENTS` (`core/events/registry.py:339`) and given curated copy in `core/events/copy.py`, all under the existing **"Spaces"** category (`core/triggers.py:108`). `activity_kind=None` on all four (no new `SiteActivity` kinds — the model methods own state; consistent with the events entries at `:511`+). Channels: in-app ON + email ON, **no Discord** (these are per-person workflow replies, not broadcasts — matching `event.submitted`/`event.approved` `:511`/`:521`).

The `"when"` value in every context below is the reservation's **`when_display`** property (§5) — the makerspace-tz start–end range, so subject and body agree on one timezone.

| Key | Trigger | Audience (resolver) | Context |
|---|---|---|---|
| `space.reservation_submitted` | `submit()` | `GUILD_LEADERSHIP_OR_ADMINS` (`:140`) — steward guild + admins, or admins if none | `{"guild": resource.steward_guild, "requester_name", "resource_name", "when": reservation.when_display, "purpose", "review_url"}` |
| `space.reservation_approved` | `approve()` | `SINGLE_USER` (`:434`) — the requester | `{"user": member.user, "resource_name", "when": reservation.when_display, "reservations_url", "event_url"?}` |
| `space.reservation_declined` | `decline()` | `SINGLE_USER` | `{"user", "resource_name", "when": reservation.when_display, "reviewer_notes", "reserve_url"}` |
| `space.reservation_cancelled` | `cancel()` | `GUILD_LEADERSHIP_OR_ADMINS` | `{"guild": resource.steward_guild, "requester_name", "resource_name", "when": reservation.when_display, "review_url"}` |

**Email/notice quality (per rubric §9):**
- The **subject noun is a link** — the resource name / "your reservation" links to the review queue (steward) or My reservations (requester), never dead text.
- **One primary CTA** — "Review this request" / "See your reservations" — plus, for an approved event-space booking, a secondary "See it on the Community Calendar" (`event_url` = `created_event.absolute_url`).
- Surface **human content** — the requester's `purpose`/`notes` in the submitted email; the steward's `review_notes` in the declined email (guarded so it only shows when set).
- **Absolute URLs** via `MEMBER_BASE_URL + reverse(...)`; branded shell (`templates/membership/emails/_base.html`) via the copy renderer; subject + body one timezone (makerspace tz, via `when_display`).
- **Plaintext + HTML both authored.** The copy system is DB-editable strings, not `.txt`/`.html` template files: each event's `EventCopy` fills both `ChannelCopy.body_text` **and** `ChannelCopy.body_html` for the EMAIL channel (plus `subject`), mirroring the `event.submitted` entry at `core/events/copy.py:724` (`ChannelCopy` fields at `copy.py:39`).
- Each `emit()` gets a **unique `period`** (§5) so decisions and resubmissions actually deliver and dedupe.

---

## 8. Build order (phased; each phase ships green)

1. **Models + overlap logic.** `ReservableResource`, `SpaceReservation`, `ReservationError`, QuerySets, `clean()` overlap guard, `submit`/`approve`/`decline`/`cancel`, `_build_and_publish_event`. Migration (1). Factories + full model specs (overlap boundaries, guards, event create/remove). No UI yet. — Green: suite + lint + mypy.
2. **Notifications.** Register the 4 events in `_NEW_EVENTS` + copy in `copy.py`; wire the `emit()` calls from the model methods. Specs assert audience + absolute URLs + `body_text`/`body_html` both authored on each EMAIL `ChannelCopy`.
3. **Member UI.** `SpaceReservationForm`, `reserve_index`/`reserve_resource`/`reserve_availability`/`my_reservations`/`reservation_cancel` views, templates (Screens A/B/C), `_reservation_busy_times.html`, `hub.css` (both themes). Django-messages on full-page posts, toast on the HTMX cancel.
4. **Review UI.** `ReservationDecisionForm`, `_reservation_review_scope`/`_pending_reservations_for_scope`, `reservation_review_queue`/`reservation_review_decision`, template (Screen D). Unfold `ReservableResourceAdmin`.
5. **Map seam.** `_reserve_button.html` partial + migration (2) adding `ReservableResource.hotspot` (`depends_on` Spec 1's MapHotspot migration). Spec 1's detail panel includes the partial. If Spec 1 hasn't landed, ship phases 1–4 and defer this phase.
6. **Housekeeping — LAST.** Bump `plfog/version.py` VERSION to the next number in the 0.21 line (coordinate with Spec 1 so both features announce cleanly) + **one** member-friendly `CHANGELOG` entry, e.g.:
   > **Reserve a meeting room or the event space** — Book a room right from the floorplan or the new Reserve page. Pick your time (you can see when a space is already busy), tell us what it's for, and a steward confirms it. Reserving the event space automatically puts your event on the Community Calendar. It's free — a member perk.

   Curate by feature: do not add per-phase entries.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*`), factory-boy, ≥98% coverage, run in the `plfog-web` Docker image. New factories in `tests/membership/factories.py`: `ReservableResourceFactory`, `SpaceReservationFactory` (alongside `SpaceFactory` `:77`, `CommunityEventFactory` `:197`, `OrientationBookingFactory` `:337`).

**`tests/membership/space_reservation_models_spec.py`**
- `describe_overlapping` / `describe_clean`:
  - approved `[2–4]` **blocks** a new `[3–5]`; **allows** adjacent `[4–6]` (boundary — `starts_at__lt/ends_at__gt` strictness). *Explicit boundary case.*
  - same member's own pending `[2–4]` blocks their new `[3–5]`; a *different* member's pending does **not** block.
  - past start, `ends <= starts`, inactive resource, and min/max-duration each raise with the right message.
- `describe_when_display`: renders the makerspace-tz start–end range (asserts against a fixed `America/Los_Angeles` datetime).
- `describe_submit`: non-active member / inactive resource → `ReservationError`; happy path emits `space.reservation_submitted` (assert audience + absolute `review_url`). **No orphan-row test:** a POST that fails validation (late conflict) is rejected by `form.is_valid()` *before* `submit()`, so `SpaceReservation.objects.count()` is unchanged — assert it (this is the fix-4 regression guard).
- `describe_approve`:
  - meeting room → APPROVED, `reviewed_by/at` set, emits `space.reservation_approved`, **no** `created_event`.
  - event space → builds a `CommunityEvent` (COMMUNITY, `guild=None`, `title==purpose`, location==resource name), calls `publish` (assert `announce`/`push_to_google` invoked — Google is disabled in tests, so `push_community_event` records PENDING and never raises), stores `created_event`.
  - race guard: approving a request that now overlaps an already-approved one → `ReservationError` (the conflict re-check inside the `atomic()`/`select_for_update()` block; SQLite no-ops the row lock but the re-query still fires — assert the raise).
  - approving a non-pending → `ReservationError`.
- `describe_decline`: blank note → `ValueError`; sets DECLINED + notes; emits declined.
- `describe_cancel`: approved event-space cancel calls `created_event.remove_from_google()` then deletes it (mock/spy the model method) and clears the FK; pending cancel just flips status; cancelling a declined/cancelled row → `ReservationError`.

**`tests/hub/space_reservation_views_spec.py`**
- `reserve_resource`: GET renders the form; POST valid → redirect to My reservations + success message; POST overlapping → re-render with the field error.
- `reserve_availability`: returns only approved reservations for the window; empty-state text; **anonymous / inactive-member call → login redirect / 403** (gating, fix-8); `reserve_resource` GET also carries the server-rendered busy list in its context (fix-2 first-paint).
- `my_reservations`: shows the member's rows + statuses; **cancel POST → `200` (not `204`) whose body is the re-rendered `_reservation_row.html` in its Cancelled state, with the toast `HX-Trigger` header present** (fix-1 regression guard — assert status is 200 and the response body contains the Cancelled row, since a 204 would leave the row unswapped); a member can't cancel another member's reservation (`404`).
- review queue: admin sees all pending; a steward sees only their `steward_guild` resources'; a plain member → `403`. Approve via the query-string decision; decline via the decision modal; blank-note re-render re-opens the modal with the error; stale-state → info message, not `500`. Nav badge count = `_pending_reservations_for_scope(scope).count()` for a reviewer, `0` otherwise (fix-9).

**tz / window gotchas to encode:** build all datetimes with `timezone.make_aware`/`timezone.now()`; assert the adjacent-boundary non-overlap explicitly (the classic off-by-one). Render "when" copy in the makerspace tz (`America/Los_Angeles`) and assert the subject/body agree. Note DST-spanning reservations are computed from aware datetimes (fine) and are out of scope for special handling in v1.

## 10. Open / deferred

- **Does a *pending* request lock a slot for other members?** Spec assumes **no** — only approved reservations hard-block others; a competing pending is surfaced to the steward as a conflict flag. If Josh wants first-come holds on unconfirmed slots, add PENDING to the approved-block statuses in §5 rule 5 (one-line change) — flag for confirmation.
- **Who "stewards" a resource?** Spec assumes **admins always**, plus an optional per-resource `steward_guild` whose leadership also reviews. Confirm whether meeting rooms should ever route to a guild lead (like cubbies in Spec 1) or stay admin-only.
- **Resource CRUD stays in Unfold admin** (a handful of rows). If members/leads should self-serve new rooms, a hub editor mirroring the FAQ/Links formsets is the follow-up — deferred (YAGNI).
- **Recurring reservations** (weekly standing meeting) — out of scope; `CommunityEvent` recurrence exists but reservations are single-window in v1.
- **Buffer/setup time, capacity-vs-attendee counts, "future-available" dates** — out of scope.
- **Availability rendering:** v1 ships a scoped busy-times list, not the full shared calendar grid. Folding event-space bookings into `community_calendar` happens automatically via the published `CommunityEvent`; meeting-room busy-times stay on the reserve page. Promoting meeting-room reservations onto the shared grid (via `CalendarEntry`) is a later nicety.
- **Version number** is intentionally not hardcoded — coordinate the final bump with Spec 1 so the two features announce together in the 0.21 line.
