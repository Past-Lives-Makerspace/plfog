# Interactive Space Map + Space Requests — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-04 · **Refreshed:** 2026-07-22 against `main` @ `76f3dba` (VERSION 0.23.25)
**Surface:** FOG hub `pastlives.test` — the Space & Org Info page (`/info/`), a new admin placement editor (`/info/map/edit/`), and a request-review queue (`/info/requests/review/`). Public-read map; member-gated requests.
**Related:** Spec 2 of 2 — *Time-based Reservations (meeting rooms + event space)* — a separate doc. This spec owns `Floorplan`, `MapHotspot`, `SpaceRequest`, the read map, the placement editor, and the lease/cubby request flow. Spec 2 owns reservable resources, reservations, availability/overlap, and event-space→`CommunityEvent`. The seam is the `MapHotspot.kind` enum (§4) — this spec defines `meeting_room` / `event_space` so spec 2 adds no hotspot schema change.

---

## 1. Summary

Today `/info/` shows a single flat floor-plan image you can only click to zoom (`templates/hub/_org_floorplan.html`). This feature turns it into an **interactive, pannable, multi-floor map**: a member picks a floor, sees every studio / cubby / shop as a colored shape (green = available, muted = occupied, amber = maintenance), clicks one to read its code, size, price, status, and current occupant, and — if it's a leasable studio or an open cubby — **requests it in one click**. The request routes to the right approver (studios → makerspace admins; guild-owned cubbies → that guild's lead), who approves, declines, or asks for changes with a note; the member is notified of the decision. No money changes hands in-app — the map shows the monthly price and a human finalizes the lease in Airtable. Admins get a visual editor to upload each floor's image and drop each existing `Space` onto it. A keyboard-navigable list of the same spaces (grouped by floor) is always available as an accessible equal of the map.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Scope / packaging | **Two specs.** This is Spec 1 (map + human-routed requests). Reservations are Spec 2. Build the map first. |
| Hotspot shape | **Regions + pins in one model.** Rectangular `region` for rooms/studios/cubbies (hover-highlight, click anywhere inside); labeled `pin` for small info markers (restrooms/exits/shops). `MapHotspot.shape ∈ {region, pin}`. |
| Payment | **Display price, arrange offline.** The map shows each space's monthly price from Airtable; a request only **notifies** the approver. No in-app checkout, no Stripe. |
| Airtable is read-only | `Space` / `Lease` are pulled from Airtable and never written back. Map coordinates and requests are **new Django-owned models** referencing `Space` by FK. On approval a human fulfills the lease in Airtable — approving a request does **not** create a `Lease`. |
| Floorplan vs the single image | The interactive map is fed by new `Floorplan` records and **supersedes** the single-image lightbox when ≥1 published floor exists; the legacy `OrgInfoPage.floorplan_image` lightbox stays as the graceful fallback when there are no floors yet. |
| Moderation shape | Mirror the **`CommunityEvent`** proposal→review→decision state machine and its review-queue views (see §2). ⚠️ **The original reason for this choice has expired.** The spec said `GuildAnnouncement` was "a plain news post with no moderation"; since then it has grown its own `ModerationState` (PUBLISHED/PENDING/CHANGES_REQUESTED/DECLINED) plus `submit_for_review` / `withdraw` / `approve` / `request_changes` / `decline` at `membership/models.py:2272`, `:2438`–`:2566`. So there are now **two** implementations of this pattern. `CommunityEvent` is still the better model here because this spec's flow is member-proposes → reviewer-decides on a *calendar-adjacent* object, but whoever builds this should compare the two first and consider whether the duplication should be factored out rather than tripled. |
| Request states | **Approve / Decline / Withdraw only — no `changes-requested`.** A space request has no editable body worth a back-and-forth thread; "not this one" is a decline-with-note. Dropping the changes state also keeps idempotency simple: "open" ≡ `PENDING`, so the partial `UniqueConstraint(condition=Q(state="pending"))` is exactly right. |
| Cubby routing + fallback | Route via the spine's `GUILD_LEADERSHIP_OR_ADMINS` resolver so a guild-owned cubby reaches its lead+staff and an open/unowned shelf falls back to admins — a bound resolver can't fall back on its own, and `GUILD_LEAD` alone silently drops lead-less shelves. See §5 + §10. |
| Access | Map **view** = public-read (mirrors `org_info`). **Editing** = admin-only (`_require_admin`). **Requesting** = logged-in **active** members (`Member.Status.ACTIVE`); guests are prompted to log in. |

---

## 2. What already exists (reuse, don't reinvent)

This build is assembly. Every column below was originally confirmed against VERSION 0.21.0 and **re-verified against `main` at `76f3dba` (VERSION 0.23.25) on 2026-07-22** — every symbol still exists, but the line numbers moved substantially (`CommunityEvent` alone went 2037 → 3484) and have been updated. Line numbers rot; if one is off, search by the symbol name in the same column.

| Need | Existing thing | Location |
|---|---|---|
| The `/info/` page to extend | `OrgInfoPage` singleton (`load()`, `HeroCropMixin`) + `floorplan_image` / `floorplan_caption` | `membership/models.py:2032`, `:2051`, `:2057` |
| Public-read page view | `org_info` (no `@login_required`; `can_edit=_viewing_as_admin`) | `hub/views.py:2259` |
| Admin-gated editor + own-endpoint formsets | `org_info_edit` / `org_info_faq_save` / `org_info_links_save` / `org_info_floorplan_delete` (`_require_admin`, `?tab=` Alpine) | `hub/views.py:2309`, `:2330`, `:2349`, `:2368` |
| Inline-formset editor idiom (`extra=0`, own save endpoint) | `OrgFAQItemFormSet` / `OrgLinkFormSet` | `hub/forms.py:1077`, `:1089` |
| Current flat map partial (fallback + empty state) | `_org_floorplan.html` (lightbox, `.pl-org-map`, `.pl-org-map--empty`) | `templates/hub/_org_floorplan.html` |
| The space data (read-only) | `Space` — `space_id`, `space_type`, `status ∈ {available,occupied,maintenance}`, `sublet_guild` FK, `full_price` (may be `None`), `current_occupants`, `SpaceQuerySet.available()` | `membership/models.py:4862`, `:4937` (`full_price`), `:4946` (`current_occupants`), `:4844` (`available`) |
| Guild lead / leadership | `Guild.guild_lead` FK + `Guild.leadership_members()` (lead + staff, deduped) | `membership/models.py:1224`, `:1660` |
| Edit-permission source of truth | `membership/permissions.py` → **`can_edit_guild`** (renamed since this spec was written — it was `_can_edit_guild`); view helpers `_viewing_as_admin` / `_require_admin` / `_get_member` | `membership/permissions.py:51`; `hub/views.py:550`, `:556` |
| **Approve-with-notes state machine to mirror** | `CommunityEvent.ModerationState` (PUBLISHED/PENDING/CHANGES_REQUESTED/DECLINED) + `submit_for_review` / `withdraw` / `approve` / `request_changes` / `decline` + `_emit_submitted` / `_emit_decision`; domain exc. `InvalidEventTransition` | `membership/models.py:3512`, `:4157`, `:4192`, `:4207`, `:4224`, `:4249`, `:4283`, `:4310` (class at `:3484`) |
| **Review-queue views to mirror** | `_reviewer_guild_scope` → returns a `_ReviewScope` whose **`.pending()`** supplies the queue (the standalone `_pending_for_scope` helper in the original spec no longer exists); `propose_event`, `event_review_queue`, `event_review_decision`, `event_withdraw` | `hub/views.py:2903`, `:2914`, `:3003`, `:3026`, `:2983` |
| Decision form to mirror | `EventDecisionForm` (`decision ∈ {approve,changes,decline}`, `notes` required for changes/decline) | `hub/forms.py:1507` |
| Review-queue template + decision modal to mirror | `event_review_queue.html`, `partials/_event_decision_modal.html` (confirm_modal for approve, note-carrying modal for changes/decline) | `templates/hub/event_review_queue.html`, `templates/hub/partials/_event_decision_modal.html` |
| Notification spine | `emit()` (`period` required in practice) | `core/events/emit.py:44` |
| Resolvers | `fog_admins` (`FOG_ADMINS`), `guild_leadership_or_admins` (`GUILD_LEADERSHIP_OR_ADMINS`), `single_user` (`SINGLE_USER`) | `core/events/resolvers.py:95`, `:140`, `:457`; registered in `_RESOLVERS` `:470` |
| Event registry + copy seeding | `_NEW_EVENTS` list + `seed_notification_templates`; **"Spaces" trigger category already exists** | `core/events/registry.py:345`; `core/triggers.py:145` (`lease_expiring`, category `"Spaces"`), `:158` (`CATEGORY_ORDER`) |
| Absolute URLs for emails/notices | `settings.MEMBER_BASE_URL` + `membership.orientations._absolute_url` | used throughout `CommunityEvent._emit_*` |
| Lazy-load-a-CDN-lib + JSON-coords-save pattern | `hero_cropper.js` (loads Cropper.js from CDN on first use, writes a `{x,y,w,h}` box to a hidden input) + `hub_hero_adjust` (permission-gated JSON POST, `save(update_fields=…)`) | `static/js/hero_cropper.js`, `hub/views.py:314` |
| Gallery drag-reorder | `components/gallery_manager.html` (native HTML5 DnD, `sort_order`) | `templates/components/gallery_manager.html` |
| Components | `modal.html` (HTMX body target), `confirm_modal.html`, `form_field.html`, `toggle.html`, `image_field.html`, `page_header.html`, `gallery_manager.html` | `templates/components/` (all confirmed present) |
| Status colors (reuse — **no new colors**) | `--color-success` `#7bc88f` + `--color-success-bg` (available); the **caution/warn** hue `#fbbf24` via `.hub-pill--warn` (maintenance — **not** `--color-tuscan-yellow`, which is the primary CTA gold); `--hub-text-muted` (occupied); pill classes `.hub-pill--neutral/--warn/--danger`, `.hub-badge` | `static/css/hub.css:80`, `:399` (`.hub-pill--warn`), `:1048`, `:396` |
| Toasts | server `hub.toast.trigger_toast(resp, msg, type)` / client `$dispatch('show-toast', …)` (in base) | `hub/toast.py` |

**Genuine gaps to close (net-new):**
1. Three models: `Floorplan`, `MapHotspot`, `SpaceRequest` (§4).
2. The pan/zoom/pinch map interaction — no such code exists (`static/js/space_map.js`, hand-rolled pointer math on one transformed wrapper).
3. The visual placement editor (`static/js/space_map_editor.js`, reusing the hero-cropper lazy-load + JSON-save shape).
4. Three semantic **marker classes** (`.pl-map-marker--available/--occupied/--maintenance`) that reuse the existing color tokens above — flagged: no new color *values*, only three new `pl-`-prefixed classes.

---

## 3. Where the code lives

Home apps: `membership` (models), `hub` (views/forms/templates), `core/events` (registry/triggers wiring). Mirrors the `org_info` + `CommunityEvent` architecture so it stays inside the current coverage/mypy scope.

```
membership/
  models.py                          + Floorplan, FloorplanQuerySet
                                     + MapHotspot, MapHotspotQuerySet
                                     + SpaceRequest, SpaceRequestQuerySet, InvalidSpaceRequestTransition
  migrations/0099_floorplan_maphotspot_spacerequest.py   (CreateModel ×3 + constraints/indexes)
  spec/models/floorplan_spec.py, map_hotspot_spec.py, space_request_spec.py
  factories.py                       + FloorplanFactory, MapHotspotFactory, SpaceRequestFactory
core/events/
  registry.py                        + 4 EventType entries in _NEW_EVENTS (space.*)
  triggers.py                        + Spaces-category Trigger rows (member-facing decision notices)
hub/
  views.py                           + org_map_edit, org_map_floors_save, map_hotspot_position (JSON),
                                       map_hotspots_save, map_hotspot_detail, space_request_create,
                                       space_request_withdraw, space_request_review_queue,
                                       space_request_review_decision  (+ _map_reviewer_scope helper)
  forms.py                           + FloorplanForm/FloorplanFormSet, MapHotspotForm/MapHotspotFormSet,
                                       SpaceRequestForm, SpaceRequestDecisionForm
  urls.py                            + /info/map/… and /info/requests/… routes
  spec/views/space_map_spec.py, space_request_spec.py
templates/hub/
  org_info.html                      (edit: choose interactive map vs legacy lightbox)
  _org_map.html                      NEW — read map: floor switcher + pan/zoom wrapper + markers + list fallback
  org_map_edit.html                  NEW — admin placement editor (floors + markers + visual mode)
  space_request_review_queue.html    NEW — mirror of event_review_queue.html
  partials/_space_detail.html        NEW — HTMX detail panel (into modal body)
  partials/_space_request_form.html  NEW — the request form (message + submit)
  partials/_space_marker.html        NEW — one marker (region/pin), reused by map + OOB pending swap
  partials/_space_request_decision_modal.html  NEW — mirror of _event_decision_modal.html
static/js/
  space_map.js                       NEW — read-map pan/zoom/pinch + floor switch (Alpine component)
  space_map_editor.js                NEW — placement editor (lazy-load + JSON save, hero_cropper shape)
static/css/hub.css                   + .pl-map-* section (markers, wrapper, detail, switcher, list)
```

---

## 4. Data model

All three models follow CLAUDE.md patterns: `TextChoices` for every choice field, `help_text` on every field, meaningful `__str__`, indexes/constraints. Status/price/occupant are **never stored** — always derived from the linked `Space`.

### 4.1 `Floorplan`

One image per physical floor. Fed to the interactive map; ordered; publish-gated.

| Field | Type | Note |
|---|---|---|
| `name` | `CharField(max_length=100)` | Floor label shown on the switcher — e.g. "Floor 1", "2nd Floor". |
| `image` | `ImageField(upload_to="org/floorplans/", validators=[validate_image_size])` | The floor's map image. Normalized to the HERO long edge in `save()` (mirrors `OrgInfoPage.floorplan_image`) so annotations stay legible when zoomed. |
| `sort_order` | `PositiveSmallIntegerField(default=0)` | Ascending; lower shows first / left-most on the switcher. |
| `is_published` | `BooleanField(default=False)` | When on, this floor appears on the public map. Draft floors are editor-only. |
| `caption` | `CharField(max_length=300, blank=True, default="")` | Optional line under the map for this floor. |
| `created_at` / `updated_at` | `DateTimeField(auto_now_add / auto_now)` | Audit. |

- **Manager** `FloorplanQuerySet.published()` → `filter(is_published=True)` (Meta ordering already applies `sort_order`).
- `save()` calls `delete_orphan_on_replace(self, "image")` + `normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_HERO)`.
- **Coordinates are percent-based, so a mere resize/normalize of the *same* image doesn't move markers** (that's why hotspot coords are percentages, not pixels). But replacing a floor with a *genuinely different or re-cropped* image misplaces its existing markers — the editor warns and prompts a re-check (§6.4). To detect it, `save()` sets a transient `_image_changed` flag when `image` differs from the DB value (checked before `super().save()`), which the Placement tab reads to show a "re-check markers" hint.
- `Meta.ordering = ["sort_order", "name"]`; `__str__ → self.name`.
- Relationship to `OrgInfoPage.floorplan_image`: **kept**. `org_info` passes `floorplans = Floorplan.objects.published()`; the template renders the interactive `_org_map.html` when `floorplans` is non-empty, else the legacy `_org_floorplan.html` lightbox. No data migration — the single image survives as the fallback until floors are added.

### 4.2 `MapHotspot`

A positioned marker on a floor. Optionally bound to a `Space` (studios/cubbies) or a free label (facilities/info).

| Field | Type | Note |
|---|---|---|
| `floorplan` | `ForeignKey(Floorplan, on_delete=CASCADE, related_name="hotspots")` | The floor this sits on. |
| `shape` | `CharField(choices=Shape.choices)` | `REGION="region"` (rect, hover-highlight, click-anywhere) or `PIN="pin"` (labeled dot). |
| `kind` | `CharField(choices=Kind.choices)` | Drives color + CTA (below). **Includes the spec-2 seam values.** |
| `space` | `ForeignKey("Space", null=True, blank=True, on_delete=SET_NULL, related_name="hotspots")` | Optional. Set for studios/cubbies (price/size/status/occupant/guild derive from it). Null for facility/info markers. |
| `label` | `CharField(max_length=120, blank=True, default="")` | Marker text for facility/info hotspots with no `Space` (e.g. "Wood Shop", "Bathroom 3"). Ignored when `space` is set (`space.__str__` wins). |
| `description` | `TextField(blank=True, default="")` | Optional blurb for the detail panel (facilities/info). |
| `x` / `y` | `DecimalField(max_digits=5, decimal_places=2, default=Decimal("50.00"))` | Percent (0–100) of the natural image. For a region these are the **top-left** corner; for a pin, the center. **The `default=50` matters:** a marker added via the "+ Add marker" formset (which never posts coords — see §6.4) lands dead-center so the admin can drag it into place, instead of the row failing `NOT NULL`. |
| `w` / `h` | `DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)` | Percent width/height. **Region only** — null for pins. |
| `sort_order` | `PositiveIntegerField(default=0)` | Ascending; tie-break for the list fallback + z-order. |
| `created_at` / `updated_at` | `DateTimeField` | Audit. |

```python
class Kind(models.TextChoices):
    STUDIO       = "studio",       "Studio (leasable)"       # → "Request to lease"  (Admins)
    CUBBY        = "cubby",        "Cubby / shelf"           # → "Request this cubby" (Guild lead ∪ admins)
    FACILITY     = "facility",     "Facility / shop"         # info-only (wood shop, gallery, CNC…)
    INFO         = "info",         "Info marker"             # info-only (generic note)
    RESTROOM     = "restroom",     "Restroom"                # info-only
    EXIT         = "exit",         "Emergency exit"          # info-only
    MEETING_ROOM = "meeting_room", "Meeting room"            # spec 2 → "Reserve"
    EVENT_SPACE  = "event_space",  "Event space"             # spec 2 → "Reserve"
```

**Derived properties (never stored):**
- `display_label` → `space.space_id` if `space` else `label`.
- `status` → `space.status` if `space` else `None`.
- `status_display` → `space.get_status_display()` ("Available"/"Occupied"/"Maintenance") if `space` else `"Facility"` — the human string used in the marker `aria-label` and detail header.
- `availability_class` → `"available" | "occupied" | "maintenance"` from `space.status`; `None` for info markers (neutral marker).
- `full_price` → `space.full_price` (may be `None`); `None` for info markers.
- `price_display` → `f"${self.full_price|floatformat:2}/mo"` when `full_price` is set, else `"Price on request"` (space present) or `""` (info marker). Used everywhere price is shown — templates and the `aria-label` — so "price on request" is never re-derived ad hoc.
- `occupants` → `space.current_occupants` if `space` else `[]`.
- `cta_kind` → `"lease"` (STUDIO) · `"cubby"` (CUBBY) · `"reserve"` (MEETING_ROOM/EVENT_SPACE — spec 2) · `None` (facilities/info).
- `is_requestable` → `cta_kind in {"lease","cubby"}` **and** `space is not None` **and** `space.status == Space.Status.AVAILABLE`.

**Meta:**
- `ordering = ["floorplan__sort_order", "sort_order", "id"]`.
- `indexes = [Index(fields=["floorplan", "sort_order"], name="idx_maphotspot_floor_order"), Index(fields=["space"], name="idx_maphotspot_space")]`.
- `constraints` — a `CheckConstraint` enforcing shape/dimension coherence (safe on SQLite; **not** an ExclusionConstraint):
  ```python
  CheckConstraint(
      name="ck_maphotspot_region_has_dims",
      condition=(Q(shape="region") & Q(w__isnull=False) & Q(h__isnull=False))
                | (Q(shape="pin") & Q(w__isnull=True) & Q(h__isnull=True)),
  )
  ```
  The `CheckConstraint` covers **only** region-has-dims. In-bounds validation is app-level, enforced in **both** write paths (`MapHotspotForm.clean()` and the JSON position endpoint, §6.4): `0 ≤ x,y ≤ 100`, `0 < w,h ≤ 100` for regions, and `x + w ≤ 100` / `y + h ≤ 100` so a region never runs off the image. Friendly per-row messages ("A region needs a width and height; a pin doesn't." / "This marker runs off the edge of the floor plan.") live in the form; the endpoint returns a 400 JSON error the editor surfaces as a toast.
- `MapHotspotQuerySet.for_map()` → `select_related("space", "space__sublet_guild")` to kill N+1 across markers.
- `__str__ → f"{self.get_kind_display()} · {self.display_label} ({self.floorplan.name})"`.

### 4.3 `SpaceRequest`

A member's ask for a studio lease or a cubby. **Django-owned; never mutates `Space`/`Lease`.** Mirrors `CommunityEvent`'s state machine, plus a real `WITHDRAWN` terminal state (a request is an audit record we keep, unlike a withdrawn event which is deleted — see §5).

| Field | Type | Note |
|---|---|---|
| `requester` | `ForeignKey(Member, on_delete=CASCADE, related_name="space_requests")` | Who asked. |
| `space` | `ForeignKey("Space", on_delete=PROTECT, related_name="requests")` | The target space (price/guild derive from it). **`PROTECT`, not `CASCADE`** — a request is an audit record we keep; the Airtable pull doesn't hard-delete Spaces, so `PROTECT` never fires in practice. If a Space ever *were* removed, `PROTECT` surfaces it loudly rather than silently wiping request history. (If Airtable deletes become real, snapshot `space_id` onto the request and switch to `SET_NULL` — deferred, §10.) |
| `hotspot` | `ForeignKey(MapHotspot, null=True, blank=True, on_delete=SET_NULL, related_name="requests")` | Provenance — which marker they clicked. Optional; the `space` is the real target. |
| `kind` | `CharField(choices=RequestKind.choices)` | `LEASE="lease"` (studio) / `CUBBY="cubby"` (shelf). Set from `hotspot.cta_kind` at creation. |
| `state` | `CharField(choices=ModerationState.choices, default=PENDING)` | `PENDING / APPROVED / DECLINED / WITHDRAWN` — **no changes-requested state** (a space request has no editable body; "not this one" is a decline-with-note). |
| `message` | `TextField(blank=True, default="")` | Optional member note ("for pottery storage"). Surfaced to the reviewer + carried into the notification. |
| `reviewed_by` | `ForeignKey(User, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | Who decided. |
| `reviewed_at` | `DateTimeField(null=True, blank=True)` | When. |
| `review_notes` | `TextField(blank=True, default="")` | Reviewer's reason (required for a decline). |
| `created_at` / `updated_at` | `DateTimeField` | Audit. |

```python
class ModerationState(models.TextChoices):
    PENDING   = "pending",   "Pending review"
    APPROVED  = "approved",  "Approved"
    DECLINED  = "declined",  "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"

class RequestKind(models.TextChoices):
    LEASE = "lease", "Studio lease"
    CUBBY = "cubby", "Cubby / shelf"
```

**Meta — idempotency (the mandatory dup-pending guard):**
```python
constraints = [
    UniqueConstraint(
        fields=["requester", "space"],
        condition=Q(state="pending"),
        name="uq_spacerequest_one_pending_per_member_space",
    ),
]
indexes = [
    Index(fields=["state", "created_at"], name="idx_spacerequest_state_created"),
    Index(fields=["space", "state"], name="idx_spacerequest_space_state"),
]
ordering = ["-created_at"]
```

**Manager `SpaceRequestQuerySet`:** `pending()` → `filter(state=PENDING)` (also serves the reviewer queue — "open" and "awaiting review" are the same set now that there's no changes state); `for_scope(scope)` → all lease+cubby for admins (`scope is True`), else cubby requests whose `space__sublet_guild__in=scope` (a lead sees their guilds' cubby asks).

**Properties:** `is_open` → `state == PENDING` (drives the "request pending" UI on marker/detail/list); `review_audience_label` → `"the makerspace admins"` for lease (and for a cubby with no owning guild / lead-less guild) or `f"the {space.sublet_guild.name} lead"` for a guild-owned cubby — the member-facing explainer of where their ask goes.

### 4.4 Migration

`membership/migrations/0099_floorplan_maphotspot_spacerequest.py` — three `CreateModel` operations plus the `CheckConstraint` (MapHotspot) and partial `UniqueConstraint` (SpaceRequest). **Fully reversible**: these are net-new tables, so Django's `CreateModel` reverses cleanly (`migrate membership 0072` drops all three), leaving `Space` / `Lease` / `OrgInfoPage` untouched. No data migration, so no custom reverse function is needed. Run `ruff format` + `git add` the migration together (CI's `ruff format --check` covers migrations).

---

## 5. Business logic (fat models)

Views stay thin — they parse the request, call a model method, and return a toast/redirect. All transitions guard state and raise the domain exception; all side-effects (state change → notify) live on the model, exactly like `CommunityEvent`.

**Domain exception:** `class InvalidSpaceRequestTransition(ValueError)` (mirrors `InvalidEventTransition` at `membership/models.py`).

**`SpaceRequest` methods** (mirror `CommunityEvent.submit_for_review`/`approve`/`decline`/`withdraw` at `membership/models.py:3512`–`:2484` — but **no `request_changes`**, per the locked decision):

| Method | Guard (raises `InvalidSpaceRequestTransition`) | State → | Side effects |
|---|---|---|---|
| `submit(*, requester)` | new row only | `PENDING` | `save()`; `_notify_submitted()`. (One-shot — a request is created once; there is no resubmit loop.) |
| `approve(*, reviewer)` | `state == PENDING` | `APPROVED` | records `reviewed_by/at`; `save(update_fields=…)`; `_notify_decision("space.request_approved", …)`. **Does not create a `Lease`** — a human fulfills in Airtable. |
| `decline(*, reviewer, notes)` | `state == PENDING`; `notes` non-blank (else `ValueError`) | `DECLINED` | stores `review_notes`; `_notify_decision("space.request_declined", …)`. |
| `withdraw(*, by)` | `state == PENDING` and `by == requester.user` | `WITHDRAWN` | `save(update_fields=["state", "updated_at"])`. **Keeps the row** (audit) and releases the partial-unique so the member can re-request. This diverges from `CommunityEvent.withdraw` (which deletes) — justified: a request is a record of an ask, not a draft. |

**Creation guard (idempotency, belt-and-suspenders):** `SpaceRequestForm.clean()` rejects a second pending request — `if SpaceRequest.objects.filter(requester=member, space=space, state=PENDING).exists(): raise ValidationError("You already have a pending request for this space.")` — a friendly error before the DB `UniqueConstraint` fires. The view also guards `hotspot.is_requestable` and `member.status == Member.Status.ACTIVE`.

**Routing (`_notify_submitted`)** — the reviewer audience, keyed off `kind`, reusing existing resolvers. `submit` is one-shot (no resubmit loop), so the `period` is simply unique per request:

- **Lease** → `emit("space.lease_requested", actor=requester.user, target=self, context={...}, url=review_url, period=f"spacereq:{self.pk}:submitted")` — registry recipient `FOG_ADMINS`.
- **Cubby** → same shape with key `"space.cubby_requested"` and `context={"guild": self.space.sublet_guild, ...}` — registry recipient `GUILD_LEADERSHIP_OR_ADMINS` (`core/events/resolvers.py:140`). This single resolver **is** the required fallback: a guild-owned shelf reaches the guild's lead+staff (∪ admins), and an open/unowned shelf (`sublet_guild` is `None` → `guild_leadership` returns `[]`) resolves to **admins only**. `GUILD_LEAD` alone was rejected because a bound resolver can't fall back and would silently drop lead-less shelves (see §10).

Context carries absolute URLs (`settings.MEMBER_BASE_URL` + `reverse(...)`, via `_absolute_url`): `review_url` (`hub_space_request_review_queue#request-{pk}`), `space_code`, `space_label`, `member_name`, `requester_message`, `price_display`, and `audience_label`.

**Decision notice (`_notify_decision`)** → `SINGLE_USER` to the requester (`context={"user": requester.user, ...}`), mirroring `CommunityEvent._emit_decision` at `:2526`. Approved carries the map/detail URL + an "an admin/lead will be in touch to finalize" line; declined carries the reviewer note + a link back to the map.

**Activity:** each `emit` sets its registry `activity_kind="space_request"` so the spine writes one `SiteActivity` row per event — no separate logging in the view.

---

## 6. UI / UX  ← completeness checklist applied per screen

Theme rule applies to **every** screen: tokens only (never `--surface`); wrap any control in `.hub-form-group`; sections in `hub-card`; buttons `pl-btn pl-btn--primary/--secondary/--danger/--sm`; 8px spacing grid; **verify both dark ("Obsidian") and light ("Slate") themes.** No `display` in an inline `style` on an `x-show` element (put layout in a class).

### 6.1 Read map — `templates/hub/_org_map.html` (+ `static/js/space_map.js`, `.pl-map-*` in `hub.css`)

Rendered by `org_info` inside the existing "Map & Facilities" `hub-card`, replacing the `_org_floorplan.html` include **when `floorplans` exist** (else the legacy lightbox shows). Public-read.

- **Floor switcher:** a segmented `pl-map-switch` row of buttons, one per published `Floorplan`, ordered by `sort_order`. Alpine `x-data="plMap()"` holds `floor` (active id); each floor is a `<div class="pl-map-floor" x-show="floor === {{ f.id }}">`. No reload, no HTMX — all published floors render server-side; only the active one is shown. Buttons get `:class="floor === id ? 'is-active' : ''"`, `role="tab"`, `aria-selected`. Hidden when there's only one floor.
- **Pan/zoom stage:** the alignment approach from the brief, baked in — one `position:relative` wrapper (`.pl-map-stage`) holds `<img class="pl-map-img" style="width:100%">` plus absolutely-positioned markers at `left:X%; top:Y%` (regions add `width:W%; height:H%`). Pan/zoom is `transform: translate() scale()` on **that one wrapper** so image + markers move together at any width. `space_map.js`: desktop drag + wheel-zoom; mobile one-finger pan + pinch; `+ / − / Reset` buttons (real tap targets); clamped scale (1–4×); honors `prefers-reduced-motion` (no transition when set). All layout is in CSS classes, not inline (Alpine-`x-show` safe).
- **Markers** — `partials/_space_marker.html`, one per hotspot:
  - Region → `<button class="pl-map-marker pl-map-marker--region pl-map-marker--{{ h.availability_class|default:'info' }}">`; pin → `.pl-map-marker--pin` with the label. Both are real `<button role="button">` with `aria-label="{{ h.display_label }} — {{ h.status_display }}{% if h.price_display %}, {{ h.price_display }}{% endif %}"` (the derived `status_display` + `price_display` from §4.2, so "Price on request" reads correctly in the label), keyboard-focusable (`:focus-visible` ring), Enter/Space activate.
  - **Color by availability** (existing tokens, no new color values): `--available` = `--color-success` fill/border; `--occupied` = `--hub-text-muted` (muted); `--maintenance` = the **caution/warn** hue (`.hub-pill--warn`'s `#fbbf24`) — **not** `--color-tuscan-yellow`, which is the primary CTA gold and would make a maintenance space glow like a primary button; info markers = neutral border. Region **hover-highlight** raises fill opacity + border.
  - **"Request pending" state:** if the viewer has an open `SpaceRequest` for this hotspot's space, the marker gets `.pl-map-marker--pending` (a dashed ring + a small "•" badge) and its `aria-label` appends "— your request is pending".
  - Click/Enter → `hx-get="{% url 'hub_map_hotspot_detail' h.pk %}"` into the modal body + `$dispatch('open-modal','space-detail')`.
- **Loading state:** the `<img>` uses `loading="lazy"` + a `.pl-map-stage--loading` skeleton shown until `@load`; the modal body shows a small `.pl-map-detail__spinner` while the HTMX detail is in flight (`hx-indicator`).
- **Empty states:** *no floorplan yet* → the existing `.pl-org-map--empty` card ("The facility map is coming soon…") with the admin "Upload the floor plan →" CTA pointing at `/info/map/edit/`. *Floor with zero hotspots* → the image renders with a muted caption "No spaces marked on this floor yet." (+ admin "Add markers →").
- **Accessible list/table fallback (mandatory):** below the stage, a `<section class="pl-map-list" aria-label="All spaces by floor">` — for each floor a `<h3>` then a table (mobile: stacks into cards) with **Code · Size · Status pill · Price (`price_display`) · CTA**. Each row is `<tr id="list-row-hotspot-{{ h.pk }}">` so it can be OOB-swapped after a request (below). The **CTA cell carries the same three states as the marker/detail** so a keyboard-only user never hits a dead end or a dup-pending surprise:
  - available studio → "Request to lease"; available cubby → "Request this cubby" (opens the same detail modal via `hx-get`);
  - **the viewer already has a pending request for this space → a "Request pending" pill + a Withdraw button** (same `confirm_modal.html` POST as the detail panel);
  - occupied/maintenance → status + occupant, no CTA; meeting_room/event_space → "Reserve" (spec-2 placeholder, see 6.3); facilities → nothing.
  Status pills reuse `.hub-pill--neutral/--warn` + `.hub-badge`. Because every CTA routes through the detail modal, keyboard users reach every space and every action without touching the map.

### 6.2 Space detail panel — `templates/hub/partials/_space_detail.html` (HTMX GET → `modal.html` body)

Loaded into `components/modal.html` (`modal_id="space-detail"`, `modal_title` = the space code / facility name). A modal (not a drawer — no hub slideover exists). Content by kind:

- **Header:** `display_label` + a status pill (available/occupied/maintenance) or a "Facility" tag.
- **Body:** size (`size_sqft` / `width×depth`), status, **price** (`full_price` or **"Price on request"** when `None`), occupant(s) (`current_occupants`, names/guild) when occupied, `description` (facility/info). A photo (`space.photo`) if present.
- **CTA (primary action, obvious):**
  - STUDIO available → **"Request to lease"** button → reveals `_space_request_form.html` (`x-show`, closed by default) in-place.
  - CUBBY available → **"Request this cubby"** → same form; the form shows the `review_audience_label` explainer ("This goes to the {Guild} lead" / "…the makerspace admins").
  - Occupied/maintenance → **no CTA**; muted "Currently {occupied by X / under maintenance}."
  - FACILITY/INFO/RESTROOM/EXIT → **no CTA**; just the label/description.
  - MEETING_ROOM/EVENT_SPACE → a disabled **"Reserve · coming soon"** button — **owned by spec 2**, which swaps in the live reserve flow. Degrades gracefully if spec 2 isn't built.
  - Viewer has an open request → a "**Request pending**" pill + a **Withdraw** button (`pl-btn pl-btn--danger pl-btn--sm`, via `confirm_modal.html`, POST `hub_space_request_withdraw`).
  - **Guest (not logged in)** → "**Log in to request**" link (`/accounts/login/?next=/info/`).
  - **Logged-in but not active** → disabled CTA + hint "Requesting a space needs an active membership."
- **States:** error → if the hotspot 404s or the detail can't render, the modal body shows a friendly "Couldn't load this space — close and try again." Every path has a Close (modal's built-in) — no dead end.

### 6.3 Request form — `templates/hub/partials/_space_request_form.html`

1 field → **modal + toast** per the FRONTEND.md interaction table (revealed inside the detail modal, not a new page).

- **Fields:** `message` (optional `Textarea`) via `components/form_field.html` (theme-correct; no bare `<textarea>`). A `field_hint` names where it goes (`review_audience_label`). Hidden `hotspot` id.
- **Save/submit:** a **"Send request"** `pl-btn pl-btn--primary` posting `hx-post="{% url 'hub_space_request_create' hotspot.pk %}"`. On success the view returns `trigger_toast(resp, "Request sent — you'll hear back soon.", "success")` **plus** an OOB swap of **all three** surfaces so they can't drift out of sync: `_space_marker.html` (→ pending marker), the detail-panel CTA (→ "Request pending" + Withdraw), **and the list-fallback row `#list-row-hotspot-{{ pk }}` (→ pending pill + Withdraw)** — so a keyboard user who requested from the list doesn't then hit the dup-pending error. On validation error (dup pending / inactive) the partial re-renders with the form error inline; the modal stays open. HTMX mutation → **toast**, never a Django-messages redirect.
- **Cancel:** the modal Close; the form also collapses via the same toggle.

### 6.4 Admin placement editor — `templates/hub/org_map_edit.html` (+ `static/js/space_map_editor.js`)

New admin page at `/info/map/edit/`, gated by `_require_admin` (403 partial otherwise). Alpine `?tab=` shell like `org_info_edit.html`, two tabs: **Floors** and **Placement**.

**Floors tab** — a list editor of `Floorplan` rows (`FloorplanFormSet`, `extra=0`, `can_delete=True`):
- **"+ Add floor"** button clones a hidden `<template>` of `formset.empty_form`, swaps `__prefix__`, bumps `TOTAL_FORMS` (the plfog way — copy the FAQ/Links editor in `guild_edit.html`).
- Each row: `name` + `is_published` **toggle** (`toggle.html`) + `sort_order` + the floor **image** via `components/image_field.html` (drag-drop upload + size guard) + `caption`.
- **Per-row Delete:** a real `pl-btn pl-btn--danger pl-btn--sm` **button** with `margin-top:0.75rem` — **never a toggle.** An **empty** floor (zero hotspots) deletes with the plain auto-save row-delete (flip the hidden `DELETE`, submit / remove the DOM node for an unsaved clone). A **populated** floor routes through **`confirm_modal.html`** first, because deleting it cascades its markers: *"Delete {name}? This also removes its {N} markers from the map. This can't be undone."* The template branches on `floor.hotspots.count` to pick the guarded vs. plain path; the confirm posts the same `DELETE`-flip save.
- **Save:** a primary **"Save floors"** posting to `hub_org_map_floors_save` (own endpoint, mirrors `org_info_faq_save`), redirect back to `?tab=floors` with a Django `messages.success` ("Floors saved.").
- **Empty state:** "No floors yet. Add your first floor to start the map."

**Placement tab** — assign existing `Space` records + drop facility markers on the chosen floor. **Two write paths that never fight** (this is the key reconciliation): the JSON drag endpoint owns **coordinates** (`x/y/w/h`); the formset owns **structural fields** (`space/kind/shape/label/description/sort_order`) and **explicitly EXCLUDES `x/y/w/h`**. A formset Save therefore can never clobber a marker's dragged position, and the drag endpoint never touches structural data.

- A floor `<select>` (in `.hub-form-group`; `select option {background;color}` styled) picks which floor to edit; its image renders in the same `.pl-map-stage` wrapper (edit mode).
- **"Re-check markers" hint (image replaced):** when the selected floor's `image` was just replaced (`Floorplan._image_changed`, §4.1), the Placement tab shows a dismissible caution banner (`.hub-message` warn style) — *"You replaced this floor's image. Coordinates are kept, but a differently-cropped image can misplace markers — please re-check each one."* Percent coords survive a resize/normalize untouched; only a genuinely different crop needs re-verification.
- **List editor of markers** (`MapHotspotFormSet`, `extra=0`, **fields = `space/kind/shape/label/description/sort_order` only**): **"+ Add marker"** clones `empty_form` and creates a marker with **no coords posted** → it saves fine thanks to the `x/y` model `default=50` (§4.2), landing **dead-center** for the admin to then drag into place. Per-row **Delete** (`pl-btn pl-btn--danger pl-btn--sm`, `margin-top:0.75rem`; saved rows flip `DELETE`, clones drop the node). **Save "Save markers"** → `hub_map_hotspots_save` (own endpoint). `MapHotspotForm.clean()` enforces: a region needs `w`/`h`; a `studio`/`cubby` kind needs a `space`; a facility/info kind needs a `label`.
- **Visual placement mode** (`space_map_editor.js`, reusing the `hero_cropper.js` lazy-load + JSON-save shape): selecting a saved marker in the sidebar then **click-drag a rectangle** on the image sets its region (or single-click sets a pin center); the tool writes `{hotspot_id, x, y, w, h}` as **percentages of the natural image** and POSTs JSON to `hub_map_hotspot_position` — a permission-gated endpoint (`_require_admin`) that updates **only the coord fields** (`save(update_fields=["x","y","w","h","updated_at"])`), exactly like `hub_hero_adjust` (`hub/views.py:314`). Existing markers are draggable/resizable (same endpoint). The endpoint **re-validates bounds** (`0 ≤ x,y ≤ 100`; region `x+w ≤ 100`, `y+h ≤ 100`, §4.2) and returns a 400 the editor surfaces as an error toast; a valid save confirms with a success toast. Workflow: add a marker in the list (centered) → assign its Space/kind → drag it onto the plan.
- **Dark/light:** the stage, sidebar, and drawn boxes use `--hub-*` tokens; drawn regions use a translucent accent fill that reads on both themes. **Mobile:** the editor is desktop-first (placement is a mouse task) but the Floors tab reflows to stacked cards; a note tells admins to place markers on a larger screen.

### 6.5 Request review queue — `templates/hub/space_request_review_queue.html` (mirror of `event_review_queue.html`)

At `/info/requests/review/`, gated by `_map_reviewer_scope` (admin → all lease+cubby; guild lead/staff → their guilds' cubby requests; else 403) — modeled on `_reviewer_guild_scope` (`hub/views.py:2903`).

- **Layout:** a `hub-card` list; each pending request is a row (`display_label` · requester · guild/"Site" · kind · created-at · the member `message`).
- **Controls per row — two actions only** (spaced so Approve isn't fat-fingered next to Decline, exactly like the events queue):
  - **Approve** → `confirm_modal.html` ("Approve this request? The member is notified; you'll finalize the lease in Airtable — this does not create a lease automatically.") → POST `hub_space_request_review_decision?decision=approve`.
  - **Decline** → `partials/_space_request_decision_modal.html` (mirror of `_event_decision_modal.html`) — a modal carrying a **required note** `Textarea`. Server re-opens + repopulates the modal on a blank-note error (`open_decision_for` / `decision_note_value` / `decision_note_error`), never a bare 404.
- **Feedback:** full-page POST → Django `messages.success` ("Request approved." / "Request declined."). A stale decision (already handled) surfaces the model guard as a friendly "That request was already handled" message, not a 500.
- **Empty state:** "Nothing awaiting review. 🎉" (matches the events queue).
- A **"Space requests"** entry appears in the reviewer's nav with a pending count (reuse the `review_pending_count` pattern at `hub/views.py:2777`).

### 6.6 Member's own requests

Surfaced compactly so a member can see/withdraw their asks without a new page: a **"Your space requests"** `hub-card` on `/info/` (shown only when the member has a pending request), each row = space code + a `.hub-pill--warn` "Pending" pill + a **Withdraw** button (`pl-btn pl-btn--danger pl-btn--sm` + `confirm_modal.html`, POST `hub_space_request_withdraw`). Mirrors the `my_proposals` pattern on `propose_event` (`hub/views.py:2318`). There is no changes/resubmit state — a declined request simply drops off the list (its decision arrived by notification, §7); the member can re-request the space from the map.

---

## 7. Notifications / emails / activity

Register **4 `EventType`s** in `_NEW_EVENTS` (`core/events/registry.py:345`) — each with a seeded copy entry (`seed_notification_templates`) — plus matching `Trigger` rows in the existing **"Spaces"** category (`core/triggers.py:145`). Channels follow the events pattern (in-app + email; these are personal, not broadcasts — no Discord).

| Event key | Recipient (resolver) | Audience | Channels | Copy (subject noun links to `space_url` / `review_url`) |
|---|---|---|---|---|
| `space.lease_requested` | `FOG_ADMINS` | staff-only | in-app + email | "{member} wants to lease {space code}" → review queue; body carries price + message. |
| `space.cubby_requested` | `GUILD_LEADERSHIP_OR_ADMINS` (ctx `guild=sublet_guild`) | staff-only | in-app + email | "{member} wants cubby {code}" → review queue; guild-scoped, admin fallback. |
| `space.request_approved` | `SINGLE_USER` (ctx `user=requester.user`) | member | in-app + email | "Your request for {code} was approved" → detail; "an admin/lead will be in touch to finalize." |
| `space.request_declined` | `SINGLE_USER` | member | in-app + email | "Update on your {code} request" → map; carries the reviewer note. |

Email rules (FRONTEND.md → *Email Templates*, if any `.txt`/`.html` template is authored beyond the DB copy): the **space code is a link** to its detail (`{space_url}` = `/info/#hotspot-{pk}` or the detail), **one primary CTA** (review queue for staff / detail for the member) + helpful secondary ("see the map"), the member's **`message`** surfaced guarded, **absolute URLs** via `_absolute_url`, branded shell, no "BETA", subject+body one timezone, `.txt` + `.html` in sync, and each send has a **unique `period`** (timestamped for re-notifiable submits). `SiteActivity` kind `"space_request"` set via the registry `activity_kind` so the spine logs one row per event.

---

## 8. Build order (phased; each phase ships green — full suite + lint + mypy)

1. **Models + logic.** `Floorplan`, `MapHotspot`, `SpaceRequest` + querysets + `InvalidSpaceRequestTransition` + all fat-model methods (`submit`/`approve`/`decline`/`withdraw`) and `_notify_*`. Migration `0073`. Factories. Full model specs. (No UI yet — methods callable + tested.)
2. **Notifications wiring.** 4 `EventType`s in `_NEW_EVENTS` + copy entries + "Spaces" `Trigger` rows. Specs assert resolver routing (lease→admins; guild cubby→lead∪admins; open cubby→admins) and unique `period`.
3. **Read map + detail + list fallback.** `org_info` passes `floorplans`/hotspots; `_org_map.html`, `_space_marker.html`, `_space_detail.html`, `space_map.js`, `.pl-map-*` CSS; the legacy-lightbox fallback branch. `hub_map_hotspot_detail`. View specs for gating + template states (empty / loading / pending / price-on-request / occupied).
4. **Request flow.** `SpaceRequestForm`, `_space_request_form.html`, `hub_space_request_create` / `hub_space_request_withdraw`, the "Your space requests" card, pending-state OOB swaps. Specs for dup-pending guard, active-member gate, guest prompt, withdraw.
5. **Placement editor.** `org_map_edit.html`, `FloorplanFormSet`/`MapHotspotFormSet`, `space_map_editor.js`, `hub_org_map_edit` / `hub_org_map_floors_save` / `hub_map_hotspots_save` / `hub_map_hotspot_position`. Admin-gate specs + coord round-trip.
6. **Review queue.** `_map_reviewer_scope`, `space_request_review_queue.html`, `_space_request_decision_modal.html`, `SpaceRequestDecisionForm`, `hub_space_request_review_queue` / `hub_space_request_review_decision`, nav pending-count. Specs for scope (admin vs lead), decision transitions, blank-note re-open, stale-decision guard.
7. **Housekeeping (LAST).** Bump `plfog/version.py` `VERSION` (0.21.x patch) + **one** member-friendly `CHANGELOG` entry grouped under this feature (e.g. *"Explore the space on an interactive map — see what's open, and request a studio or cubby right from the floor plan."*). Do not touch `version.py` in phases 1–6.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` in each app's `spec/` dir, `describe_*`/`it_*` (**never `context_*`** — not collected), factory-boy, run in the `plfog-web` Docker image, ≥98% coverage gate (CI runs on **SQLite** — so the `CheckConstraint` is fine but no Postgres-only constructs). Cases:

- **Models:** `Floorplan.published()` ordering + `_image_changed` flag on a replaced image; `MapHotspot` derived props (`display_label`/`status`/`status_display` (incl. "Facility" for a marker with no space)/`availability_class`/`price_display` **None → "Price on request"**/`is_requestable` gates on AVAILABLE + space present); the region/pin `CheckConstraint` (region without dims / pin with dims → `IntegrityError`); a formset-added marker (no coords posted) saves at the `x/y=50` default.
- **`SpaceRequest` transitions + side effects:** each method's happy path, each guard raising `InvalidSpaceRequestTransition` (no `request_changes` exists), blank-note `ValueError` on decline, `withdraw` keeps the row as `WITHDRAWN`, approve does **not** create a `Lease`. Partial `UniqueConstraint` blocks a second pending; a withdrawn/declined one lets a new request through. `space` is `PROTECT` (deleting a referenced Space raises, not cascades).
- **Routing (mock `emit`, assert event_key + resolver context):** lease → `space.lease_requested`; guild-owned cubby → `space.cubby_requested` with `context["guild"]` set; **open/unowned cubby → resolves to admins** (`sublet_guild None`); each decision → `SINGLE_USER` with `context["user"]`; unique timestamped `period`.
- **Views (gating):** map view is public (anon 200); `org_map_edit`/save/position endpoints 403 for non-admin; request-create 302/redirect-to-login for anon, 403/disabled for non-active member; review queue scope (admin sees lease+cubby; lead sees only their guild's cubby; unrelated member 403); decision endpoint honors the model guard on a stale row.
- **Forms + coord bounds:** `SpaceRequestForm.clean()` dup-pending message; `MapHotspotForm.clean()` region-needs-dims / studio-needs-space / facility-needs-label / **out-of-bounds coords** (`x>100`, `x+w>100`); `SpaceRequestDecisionForm` note-required-for-decline; the JSON `hub_map_hotspot_position` endpoint rejects out-of-bounds coords with a 400 and updates only `x/y/w/h` (a Save-markers formset POST leaves coords untouched — the two-write-path guarantee).
- **Templates/states:** no-floorplan empty state, floor-with-no-hotspots caption, pending marker + detail + **list-row** (all three OOB-swapped after a request), guest "Log in to request", occupied → no CTA + occupant shown, populated-floor Delete goes through the confirm modal. (The hub "what's new" widget echoes the CHANGELOG — assert on button `href`/`title=`, not visible text.)
- **Gotcha:** member-gated view specs must seed a `MembershipPlan` before login (the signal skips `Member` creation without one) — passes on stale local SQLite, fails fresh CI.

## 10. Open / deferred

- **Cubby routing — confirm the audience.** This spec uses `GUILD_LEADERSHIP_OR_ADMINS` (guild lead+staff, admins as guaranteed backstop / sole recipient for unowned shelves) rather than the brief's literal `GUILD_LEAD`, because a bound resolver can't fall back and `GUILD_LEAD`/`GUILD_LEADERSHIP` return `[]` for a lead-less/guild-less shelf → a **silently dropped** request. Trade-off: admins are copied on every guild-cubby request (matches how `CommunityEvent` proposals already route). If Josh wants leads-only with admins *only* on fallback, the alternative is two event keys (`space.cubby_requested`→`GUILD_LEADERSHIP`, plus an admin-routed fallback the model picks when the guild has no leadership) — more surface for marginal benefit. Flagged for a decision.
- **New CSS classes, not new colors.** The three marker classes reuse existing tokens — `--color-success` (available), the `.hub-pill--warn` caution hue `#fbbf24` (maintenance), and `--hub-text-muted` (occupied). Deliberately **not** `--color-tuscan-yellow`: that gold is the primary brand/CTA fill, so a maintenance space in it would read like a primary button. Flagged per house rule, but no new color *value* is introduced.
- **Future-available date** ("A9 available by 9/20") is a display nicety only — deferred; would need an Airtable field this spec won't add (Airtable is read-only).
- **Pan/zoom library vs hand-rolled.** Recommended hand-rolled pointer math (no dependency, small surface) over lazy-loading a pan/zoom lib; revisit only if pinch fidelity on mobile proves fiddly.
- **`meeting_room` / `event_space` reserve flow** is out of scope — the enum values + the disabled "Reserve · coming soon" CTA are the seam; **Spec 2** adds the reservable-resource link, availability/overlap (application-level `clean()`, no Postgres `ExclusionConstraint`), the reserve UI, and event-space→`CommunityEvent.publish()`. Spec 2 must degrade gracefully if built before those hotspots are placed.
- **Airtable `floorplan_ref`** (an unused free-text hook on `Space`) is intentionally left alone — placement lives in `MapHotspot`, not by mutating `Space`.
