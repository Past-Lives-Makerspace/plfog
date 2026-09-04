# Equipment-Owned Orientations — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. One PR (mostly wiring + one migration); ordered build steps in §11.
**Date:** 2026-09-03
**Surface:** FOG hub — equipment detail page (new Orientation section), equipment manage panel (new Orientation tab), orientation booking/respond/checkout flows, orientation emails, orientations dashboard (minimal fix)
**Related:**
- `2026-09-03-equipment-reservations.md` — shipped (migrations 0161/0162). Its locked decision #4 hosted standalone-tool orientations under a house "Makerspace" guild and explicitly deferred the brainstorm's Option B "until reality demands it." It now does: the owner wants equipment orientations **not** gated through guilds at all, bookable on the tool's own page.
- `2026-09-03-equipment-reservations-brainstorm.md` §5 — Option B ("make `OrientationType.guild` nullable and add `OrientationType.equipment`") is what this spec builds, in its **light form**: owner-resolution only, no parallel models, no per-orienter machinery for equipment.
- `2026-06-21-guild-orientations.md`, `2026-08-26-orienter-availability.md`, `2026-08-26-paid-orientations.md` — the shipped orientation stack this spec re-plumbs for a second owner type.

---

## 1. Summary

An equipment's orientation stops being a guild's problem. `OrientationType` gains a second possible owner — the equipment itself — and the entire shipped orientation pipeline (request → confirm/decline/cancel, paid Stripe checkout + automatic refunds, `.ics` invites, signed email links, seat holds, auto-complete) runs unchanged on top of it. Members book the orientation in a slot list **on the equipment's own page**; equipment managers create the orientation types, post slots, and confirm requests from a new **Orientation tab on the equipment manage panel**. The `needs_orientation` blocker banner links straight down the page instead of off to a guild.

### Locked decisions (owner said proceed — do not reopen)

| # | Decision |
|---|---|
| 1 | **Route (a), light form** (brainstorm §5 Option B): `OrientationType.guild` becomes nullable; a nullable `OrientationType.equipment` FK is added (`on_delete=PROTECT`, `related_name="owned_orientation_types"`). Exactly one of guild/equipment is set (CheckConstraint). **No new parallel orientation models** — the whole `membership/orientations.py` pipeline is reused. |
| 2 | **Lifecycle unchanged**: request → confirm/decline flow and paid-checkout support stay exactly as shipped. Equipment managers (the three `can_manage_equipment` tiers) replace guild leads as confirmers/managers/notification audience for equipment-owned types. |
| 3 | **Bookable on the equipment page**: the detail page grows an Orientation section rendering the same slot-list UX as `templates/hub/partials/guild_orientation.html`, adapted (copy references the equipment, not a guild). The blocker banner links to this on-page section (anchor) when the required orientation is equipment-owned; guild-owned required orientations keep today's guild deep link. |
| 4 | **Management on the manage panel**: `equipment_manage.html` gains an Orientation tab — type formset, slot add, pending-request review — mirroring the guild_edit Orientations tab's forms, scoped to equipment permissions. **Per-orienter recurring availability/blocks are OUT of scope for equipment** (slots are added directly). |
| 5 | **No settings model**: no equipment equivalent of `GuildOrientationSettings` enabled/closed switches in v1 — an equipment type's `is_active` flag is the on/off. Owner resolution treats the settings gate as **guild-only**: equipment-owned types skip the `GuildOrientationSettings` checks in `is_bookable` / `bookable()`. |
| 6 | `Equipment.required_orientation` keeps pointing at `OrientationType` (`membership/models.py:10275`) — no data migration; existing guild-hosted required orientations keep working. An equipment's own type can be picked as its `required_orientation` (the manage form offers it). |
| 7 | Denormalized guild FKs become nullable **only where equipment-owned types need rows**: `OrientationSlot.guild` and `OrientationBooking.guild`. `OrientationAvailability.guild` (`:8523`) and `OrientationAvailabilityBlock.guild` (`:8596`) stay required — equipment never creates rows there (decision 4). Every read of `booking.guild` / `slot.guild` is audited in §5 with its owner-aware replacement. |

---

## 2. What already exists (reuse, don't reinvent)

All locations re-verified in the current tree, 2026-09-03.

| Need | Existing thing | Location |
|---|---|---|
| The orientation type (name, duration, **price**, seats, location, `is_active`) | `OrientationType` | `membership/models.py:8432` (guild FK `:8447`, `UniqueConstraint(guild, name)` `:8482`, `__str__` `:8486`, `is_paid` `:8489`) |
| Concrete bookable slot + seat math | `OrientationSlot` (`bookable()` qs `:8782`, `is_bookable` `:8925`, `ensure_bookable_for` `:8942`, `book` `:8975`) | `membership/models.py:8803` |
| The request/record row incl. Stripe fields + refund surface | `OrientationBooking` (denorm guild `:9065`, denorm type `:9071`, unique live-per-type `:9122`, `refund_receipt_context` `:9249`) | `membership/models.py:9046` |
| **The completion gate — needs NO change** | `Member.is_oriented_for_type` filters on `orientation_type` only, owner-agnostic | `membership/models.py:1311` |
| Full lifecycle service: fan-outs, emails, `.ics`, tokens, paid checkout, holds, refunds, auto-complete | `membership/orientations.py` — `request_orientation:242`, `start_orientation_checkout:457`, `finalize_paid_booking:535`, `confirm:820` / `decline:840` / `cancel:860`, `cancel_slot:892`, `complete:913`, `auto_complete:961`, `build_ics:124` | whole module |
| Equipment + its manager set + access gate | `Equipment` (`:10202`), `manager_members()` (`:10346`), `access_state` (`:10375`), `booking_blockers` (`:10409`), `EquipmentStaffMembership` (`:10671`) | `membership/models.py` |
| Equipment permission gates (view_as-aware + role twins) | `can_manage_equipment` (`membership/permissions.py:93`, `Member.can_manage_equipment` `models.py:1117`) | `membership/permissions.py` |
| Equipment-manager notification audience | `equipment_managers` resolver (union of the three tiers, deduped, tagged) | `core/events/resolvers.py:198`, enum `registry.py:107`, eligibility `settings_matrix.py:245`, badge map `settings_matrix.py:343` |
| Slot-list booking UX to adapt (price chips, hold/booking/oriented states, paged slot rows, confirm modals) | `templates/hub/partials/guild_orientation.html` (type anchor + `?type=` highlight `:37-38`, slot rows `:94-145`, hold `:49-62`, live booking `:64-86`, empty state `:182-184`) | template |
| Slot-keyed booking POST (free + paid branches) | `orientation_book` | `hub/views.py:1444` |
| Confirm/decline/cancel review page (incl. refund panel) | `orientation_respond` (`:1619`), `orientation_lead_cancel` (`:1665`) | `hub/views.py` |
| Checkout return/resume/cancel-hold views | `hub/views.py:1719`, `:1774`, `:1808`, `:1839` | |
| Type editor form + delete guard (formset-level, blocks deleting a type with bookings) | `OrientationTypeForm` (`hub/forms.py:1618`), `BaseOrientationTypeFormSet` (`:1682`); the shipped `OrientationTypeFormSet` is `inlineformset_factory(Guild, …)` (`:1698`) | `hub/forms.py` |
| Half-hour time choices + slot-add form shape | `half_hour_time_choices` (`hub/forms.py:72`), `OrientationSlotForm` (`:1869`) | `hub/forms.py` |
| Equipment manage panel tab skeleton + save-per-tab idiom | `equipment_manage.html` tabs (`:11-17`), `hub_equipment_manage` (`hub/equipment_views.py:549`), `_render_manage` (`:511`), `_require_can_manage` (`:73`) | hub |
| Blocker banner + guild deep link to replace | `hub_equipment_detail` (`hub/equipment_views.py:307`, deep link built `:318-323`), banner template (`templates/hub/equipment_detail.html:44-71`) | hub |
| Guild deep-link scroll behavior (stays as-is for guild-owned) | `templates/hub/guild_detail.html:122` | template |
| Editable-list rules (extra=0, + Add clone, real Delete buttons) | FRONTEND.md *Editable Lists & Destructive Actions*; canonical editors in `guild_edit.html` | FRONTEND.md |

**Genuinely net-new:** one migration, the owner-resolution helpers on `OrientationType`, one composed resolver, the equipment Orientation section partial, the manage-panel Orientation tab (3 endpoints + 2 forms), and template/copy swaps from `guild.name` to the owner name. Everything else is a branch inside existing code.

---

## 3. Data model — ONE migration (`membership/migrations/0163_equipment_owned_orientations.py`)

Latest existing migration is `0162_equipment_reservations.py`; this is all `AlterField`/`AddField`/`AddConstraint` — no data migration needed (decision 6), so the reverse is the automatic inverse. Run `manage.py check` after (CI runs it; local pytest skips it — the E034 name-length gotcha).

### 3.1 `OrientationType` (`membership/models.py:8432`)

| Change | Detail |
|---|---|
| `guild` → nullable | `null=True, blank=True` on the existing FK (`:8447`); `on_delete=CASCADE` unchanged. `help_text` updated: "The guild that offers this orientation type. Empty for an equipment-owned type." |
| new `equipment` FK | `ForeignKey(Equipment, null=True, blank=True, on_delete=PROTECT, related_name="owned_orientation_types", help_text="The equipment that owns this orientation type. Empty for a guild-owned type. PROTECT: deleting equipment that owns orientation history must fail loudly.")` |
| new CheckConstraint | exactly one owner: `Q(guild__isnull=False, equipment__isnull=True) \| Q(guild__isnull=True, equipment__isnull=False)`, `name="ck_orienttype_one_owner"` (23 chars, under the 30-char cap). |
| new UniqueConstraint | `fields=["equipment", "name"], condition=Q(equipment__isnull=False), name="uq_orienttype_equip_name"` — the existing `uq_orientationtype_guild_name` (`:8483`) stops covering rows with `guild=NULL` (SQL NULL-distinct semantics), so equipment-owned types need their own name uniqueness. The guild constraint stays untouched. |

### 3.2 `OrientationSlot.guild` (`:8819`) and `OrientationBooking.guild` (`:9065`)

Both become `null=True, blank=True` (CASCADE unchanged); `help_text` gains "Empty for an equipment-owned orientation." Nothing else on either model changes shape. The denorm-on-save in `OrientationBooking.save()` (`:9138-9143`) already tolerates `slot.guild = None` (it assigns whatever the slot carries). The live-per-type UniqueConstraint (`:9122`) is keyed on `(orientation_type, member)` — owner-agnostic, untouched.

`OrientationAvailability` / `OrientationAvailabilityBlock` guild FKs stay `NOT NULL` (decision 7): equipment-owned types never get recurring rules or blocks (decision 4), and `generate_slots` (`orientations.py:976`) iterates rules only — it never sees equipment types.

### 3.3 Factories (`tests/membership/factories.py`)

`OrientationTypeFactory` (`:459`) gains an equipment trait:

- `class Params: equipment_owned = factory.Trait(guild=None, equipment=factory.SubFactory(EquipmentFactory))` (EquipmentFactory exists at `:677`).
- **Wrinkle:** `django_get_or_create = ("guild", "name")` would collapse every `guild=None` type onto one row within a test — extend it to `("guild", "equipment", "name")` so equipment-owned types key correctly.
- `OrientationSlotFactory` (`:524`) / `OrientationBookingFactory` (`:547`): add a matching trait or accept `guild=None` pass-through — slot/booking factories must derive `guild` from the type's owner (`guild=factory.SelfAttribute("orientation_type.guild")` style) so an equipment-owned slot naturally carries `guild=None`.

---

## 4. Owner resolution — small helpers, one source of truth

All on `OrientationType` (fat models), so every call site asks the type, never re-derives:

```python
# membership/models.py, on OrientationType
@property
def is_equipment_owned(self) -> bool: ...          # equipment_id is not None

@property
def owner(self) -> Guild | Equipment: ...          # the non-null one (constraint guarantees exactly one)

@property
def owner_name(self) -> str: ...                   # guild.name or equipment.name

def owner_page_path(self) -> str:                  # relative hub path for redirects / in-app URLs
    # guild-owned  → reverse("hub_guild_detail", args=[guild.slug])
    # equipment    → reverse("hub_equipment_detail", args=[equipment.slug])

def owner_page_url(self) -> str: ...               # _absolute_url(owner_page_path()) — for emails

def orientation_anchor_path(self) -> str:          # the deep link the blocker banner and emails use
    # guild-owned  → owner_page_path() + f"?tab=orientations&type={pk}#guild-orientation"
    # equipment    → owner_page_path() + f"?type={pk}#equipment-orientation"

@property
def is_accepting(self) -> bool:                    # owner-aware "taking new bookings" gate
    # guild-owned  → settings row exists and settings.is_accepting (models.py:8420)
    # equipment    → self.is_active and self.equipment.is_active   (decision 5 — no settings gate)

def default_runner(self) -> Member | None:         # fallback for oriented_by
    # guild-owned  → guild.guild_lead; equipment → None (managers confirm explicitly)
```

`OrientationType.__str__` (`:8486`) becomes `f"{self.owner_name} — {self.name}"`. `OrientationSlot.__str__` (`:8875`) and `OrientationBooking.__str__` (`:9135`) switch from `self.guild.name` to `self.orientation_type.owner_name` (booking's denorm guild may be None; the type is always set).

### 4.1 `bookable()` queryset (`membership/models.py:8782-8800`)

Today it hard-requires `guild__orientation_settings__is_enabled=True, is_closed=False` plus the departed-orienter filter — an equipment-owned slot (guild=None) is silently excluded. Replace the filter with an owner-aware OR:

```python
guild_gate = Q(
    orientation_type__guild__isnull=False,
    guild__orientation_settings__is_enabled=True,
    guild__orientation_settings__is_closed=False,
) & (Q(orienter__isnull=True) | Q(orienter_id=F("guild__guild_lead_id")) | Exists(still_on_staff))
equipment_gate = Q(
    orientation_type__equipment__isnull=False,
    orientation_type__equipment__is_active=True,
    orienter__isnull=True,       # equipment slots never carry an orienter (decision 4)
)
return self.upcoming().filter(orientation_type__is_active=True).filter(guild_gate | equipment_gate)
```

### 4.2 `is_bookable` (`:8925-8940`)

The per-slot twin gets the same branch: after the shared cancelled/started/full/type-active checks, an equipment-owned slot returns `self.orientation_type.is_accepting` (equipment `is_active` gate) and **skips** the orienter-leadership check and the `GuildOrientationSettings` lookup. Guild-owned path byte-identical.

`ensure_bookable_for` (`:8942`) and `book()` (`:8975`) need no logic change — the duplicate guards are already type-scoped; `book()` passes `guild=self.guild` which is now legally None.

`confirm()` (`:9168-9174`) and `mark_completed()` (`:9191-9198`) default `oriented_by` via `self.guild.guild_lead` — crashes on None. Replace both fallbacks with `self.slot.orienter or self.orientation_type.default_runner()`. For equipment-owned bookings the respond view always passes the acting member (`hub/views.py:1636`), so `oriented_by` lands correctly; the None fallback only covers token/system paths.

`refund_receipt_context` (`:9249-9262`): `item_title` → `f"{self.orientation_type.owner_name} orientation: {self.orientation_type.name}"`; `manage_url` → `_absolute_url(self.orientation_type.owner_page_path())`; `in_app_url` → `self.orientation_type.owner_page_path()`.

---

## 5. Service layer audit — every `booking.guild` / `slot.guild` read (`membership/orientations.py`)

| Site | Today | Owner-aware replacement |
|---|---|---|
| `build_ics` `:142` summary, `:149` description | `booking.guild.name` | `booking.orientation_type.owner_name` |
| `_context` `:163-166` | `"guild": booking.guild`, `"guild_url": …hub_guild_detail…` | keep `"guild"` (may be None); add `"owner_name"`, `"owner_url"` (via `owner_page_url()`), `"owner_page_label"` ("guild page" / "equipment page"). Templates switch to the owner keys (§8.4). |
| `_emit_member_email` `:211` in-app `url` | `reverse("hub_guild_detail", …)` | `booking.orientation_type.owner_page_path()` |
| `_fan_out_request` `:234` subject | `booking.guild.name` | `owner_name` |
| `start_orientation_checkout` `:481` denorm, `:491` `product_name` | `guild=slot.guild`; `slot.guild.name` | denorm unchanged (None ok); `slot.orientation_type.owner_name` |
| `_request_audience` `:766-779` | orienter+lead or `guild.leadership_members()` | **equipment-owned branch first**: `return booking.orientation_type.equipment.manager_members()` (`models.py:10346`). Guild path unchanged. |
| `_emit_lead_request` `:798` `primary_responder` | `slot.orienter or guild.guild_lead` | equipment-owned → `None` (`make_action_token` already accepts `recipient=None`; an email-link decline is then unattributed, same as today's lead-less guild). Guild path unchanged. |
| `_emit_lead_request` `:807` resolver context | `{"guild": booking.guild, "slot": …}` | equipment-owned → `{"equipment": booking.orientation_type.equipment, "slot": booking.slot}`; guild path unchanged. See §6. |
| `_emit_lead_request` `:808`, `:813` subject / in-app body | `booking.guild.name` | `owner_name` |
| `confirm_orientation` `:832`, `:836`; `decline_orientation` `:852`, `:856`; `cancel_orientation` `:872`, `:876`, `:886` subjects / bodies | `booking.guild.name` | `owner_name` |
| `cancel_orientation` second emit `:884` context | `{"guild": …, "slot": …}` | same equipment/guild context switch as `_emit_lead_request` |
| `complete_orientation` `:919` settings lookup, `:926` subject, `:953` `guild_name` | `GuildOrientationSettings.objects.filter(guild=booking.guild)`, `booking.guild.name` | guard: equipment-owned bookings skip the settings lookup entirely (`settings_obj = None` path — the standard thank-you copy sends, which is the desired v1 behavior); both name reads → `owner_name`. |
| `finalize_paid_booking` `:535` | no direct guild read | unchanged — fan-out flows through the audited helpers above |
| `auto_complete` `:961`, `generate_slots` `:976` | rules/settings are guild-only | unchanged (`auto_complete` calls `complete_orientation`, already covered) |

`member_joined_guild` / welcome-email helpers (`:1093+`) are guild-surface only — untouched.

---

## 6. Notification audience wiring

`orientation_requested` currently routes to `Recipients.GUILD_ORIENTERS` (`core/events/registry.py:303`; resolver `guild_orienters` at `core/events/resolvers.py:287`, which `_require`s a `guild` — `resolvers.py:82` fails loudly on a missing key, so an equipment emit through it would 500 the fan-out).

**Compose, don't union** (house style — the `guild_leadership_or_class_approvers` precedent):

1. New enum value `Recipients.GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS` (`registry.py`, near `:88`); map `"orientation_requested"` to it (`registry.py:303`).
2. New resolver `guild_orienters_or_equipment_managers` (`resolvers.py`, registered in the map at `:614`): context carries `equipment` → delegate to `equipment_managers(context)` (`:198`, already tagged/deduped across the three tiers); else delegate to `guild_orienters(context)` (personal-slot narrowing preserved). Never a union.
3. `settings_matrix.py` `_eligible_for` (`:215`): new row = `GUILD_ORIENTERS` eligibility (`:236`) OR the `EQUIPMENT_MANAGERS` eligibility (`:245`) — leadership, orienter role, per-equipment staff row, or the EQUIPMENT capability. `_CAPABILITY_BY_RECIPIENT` (`:336-345`): add the new recipient → `"equipment"` so capability holders get their row badge. The event stays in the Staff & leadership section; channels/defaults unchanged.

No new event keys. `orientation_update` (member-side, `Recipients.REGISTRANT`, `registry.py:304`) is already owner-agnostic. The activity-kind map (`registry.py:361`) is untouched. Copy rule check: the request event's per-recipient copy may greet, and no broadcast channel is declared for it — nothing to add, but §12 tests assert no `[missing:]`/unresolved placeholders render for the equipment emit context.

---

## 7. Permissions

- **Model tier (exists, reused):** `Member.can_manage_equipment` (`models.py:1117`) and `Equipment.manager_members()` (`:10346`) already define the manager set: `EquipmentStaffMembership` MANAGER rows ∪ owning-guild leadership ∪ EQUIPMENT capability ∪ full admins.
- **Request tier (exists, reused):** `can_manage_equipment(request, equipment)` (`permissions.py:93`) — view_as-aware admin leg, preview-independent capability leg.
- **New helper — booking-scoped gate**, in `hub/views.py` next to `_require_can_manage_orientations` (`:696`):

```python
def _require_can_manage_booking(request, booking) -> HttpResponse | None:
    """403 unless the request may run this booking's orientation, whichever owner type."""
    orientation_type = booking.orientation_type
    if orientation_type.is_equipment_owned:
        return None if can_manage_equipment(request, orientation_type.equipment) else HttpResponse("Forbidden", status=403)
    return _require_can_manage_orientations(request, booking.guild)
```

Call sites that switch from `_require_can_manage_orientations(request, booking.guild)` to it: `orientation_respond` (`hub/views.py:1625`) and `orientation_lead_cancel` (`:1673`). The three new manage-tab endpoints (§9) gate on `_require_can_manage` (`hub/equipment_views.py:73`) like every other manage endpoint. Never gated on `is_staff` / `fog_role` / `member_type`.

`can_manage_orientations` (`permissions.py:61`) itself stays guild-typed and untouched — nothing may pass it a None guild.

---

## 8. URLs, views, and redirects

### 8.1 Reused member endpoints (owner-aware redirect only)

`orientation_book` (`hub/views.py:1444`) is reused verbatim for equipment slots — free branch and paid branch both. The only change is the redirect target. New private helper in `hub/views.py`:

```python
def _owner_redirect(orientation_type) -> HttpResponse:
    return redirect(orientation_type.orientation_anchor_path())   # §4 — guild page or equipment page + anchor
```

Every `redirect("hub_guild_detail", slug=booking.guild.slug)` in the orientation flow switches to it — audited sites: `orientation_book` (`:1453`, `:1468`), `orientation_cancel_mine` (`:1702`), `orientation_checkout_cancelled` (`:1792`, `:1803`), `orientation_checkout_cancel_hold` (`:1822`, `:1834`), `orientation_checkout_resume` (`:1860`, `:1870`); `orientation_checkout_return` (`:1719`) renders in place and links via the card partial — cover it in the §9.4 template sweep instead. Guild-owned bookings keep byte-identical destinations (the anchor path for a guild type is today's `?tab=orientations&type=…#guild-orientation` link).

`orientation_respond` (`:1619`) / `orientation_lead_cancel` (`:1665`): gate swap per §7; the "Back to" link in `templates/hub/orientation_respond.html:75` becomes the owner page (`owner_page_path` + owner name; also `:3` title and `:16` row → `owner_name`). `orientation_action` (`:1873`) needs no gate change (signed token is the authority); its template `orientation_action.html:17`, `:26` → `owner_name`.

### 8.2 New equipment endpoints (`hub/urls.py`, all `@login_required` + `equipment_feature_required` + `_require_can_manage`)

| Path | Name | Method | Does |
|---|---|---|---|
| `/equipment/<slug>/manage/orientation/types/save/` | `hub_equipment_orientation_types_save` | POST | Save the type formset (create/edit/retire/delete). Mirrors `guild_orientation_types_save` (`hub/views.py:1040`) minus the slot regeneration (no rules exist for equipment). Redirect back to `?tab=orientation`. |
| `/equipment/<slug>/manage/orientation/slots/add/` | `hub_equipment_orientation_slot_add` | POST | Add a one-off MANUAL slot (guild=None, orienter=None). Mirrors `guild_orientation_slot_add` (`:1393`). |
| `/equipment/<slug>/manage/orientation/slots/<pk>/cancel/` | `hub_equipment_orientation_slot_cancel` | POST | `orientations.cancel_slot(slot, reason=…)` (`orientations.py:892`) — full per-booking cancel fan-out + hold release, already owner-aware after §5. Slot must belong to one of this equipment's owned types (404 otherwise). |

Booking, respond, checkout return/resume/cancel-hold, and cancel-mine all reuse the existing routes — zero new member-facing URLs.

### 8.3 Equipment detail view (`hub/equipment_views.py:307`)

- Banner link (`:318-323`): replace the hand-built guild URL with `orientation_type.orientation_anchor_path()` — equipment-owned required types now resolve to the equipment page (usually this same page) + `#equipment-orientation`; guild-owned keep the guild deep link (`guild_detail.html:122` behavior untouched).
- New context: `orientation_sections` for this equipment's renderable types (§9.1 — active types plus any inactive type the member holds a live booking/hold on), built with the same per-type shape `guild_detail` builds at `hub/views.py:570-636` (completed/live/hold maps from `member.orientation_bookings.filter(orientation_type__in=types)`, slots from `OrientationSlot.objects.bookable()` filtered to the types, sliced [:30]) — **factor the section-building loop into a shared helper** (`hub/views.py`, e.g. `_orientation_sections(types, member, slots)`) so `guild_detail` and `hub_equipment_detail` cannot drift; the guild view keeps its extra blocks/custom-request context on top. No blocks, no custom requests, no orienter labels for equipment (all empty/absent).
- `_equipment_queryset` (`:68`): prefetch `owned_orientation_types` to keep the page flat on queries.

### 8.4 Emails

All 12 files in `templates/membership/emails/orientation_*.{html,txt}` switch `{{ guild.name }}` → `{{ owner_name }}` and `{{ guild_url }}` → `{{ owner_url }}` (context supplied per §5); CTA copy "View guild page" (e.g. `orientation_request.html:18`) becomes "View the {{ owner_page_label }}" ("guild page" / "equipment page"). Subject-noun rule holds: the owner name links to the owner page. `.txt` and `.html` stay in lockstep; no dashes enter any member copy. `orientation_lead_request.{html,txt}` copy is audited for "guild lead" phrasing → neutral "you can confirm or decline below" wording that reads correctly for both audiences.

### 8.5 EquipmentForm (`hub/forms.py:3542`)

- `required_orientation` choices (`:3584-3593`): include this equipment's own active types **first** (labelled by the new `__str__`, e.g. "CNC Router — Operator Basics"), then the guild-scoped set as today. **Bug fix while here:** the base queryset is `.active()` (`:3585`), so an equipment whose currently-selected required type has been deactivated fails validation on every later Details save (the saved value falls out of the choices). The queryset becomes `OrientationType.objects.filter(Q(is_active=True) | Q(pk=self.instance.required_orientation_id))` — the current selection always remains choosable (and clearable), inactive alternatives stay hidden. This fixes the pre-existing guild-type variant of the same bug too.
- `clean()` (`:3607-3615`): the guild-match rule gains an equipment leg — a picked type is valid when `type.guild_id == guild.pk` **or** `type.equipment_id == self.instance.pk`; anything else keeps the "Pick an orientation offered by the chosen guild." error (message extended: "…or one of this equipment's own orientations.").

---

## 9. UI / UX — every screen

Member copy: plain ELI14, short sentences, **no dashes in any copy string**. All new classes `pl-` prefixed in `hub.css`; verify both themes on every screen (theme tokens only — `--hub-*`, `--color-tuscan-yellow`; never raw hex in templates).

### 9.1 Equipment detail — Orientation section (new `templates/hub/partials/equipment_orientation.html`)

Rendered between the requirements banner (`equipment_detail.html:71`) and the schedule include (`:74`), when the equipment has ≥1 **renderable** owned type. A type is renderable when it is active **or the viewing member holds a live booking (REQUESTED/CONFIRMED) or a PENDING_PAYMENT hold on it** — a manager retiring a type mid-flow must never make a member's Cancel / Resume payment controls vanish; the inactive group renders its state block only (no slot list, no Request buttons; "This orientation is paused." replaces the empty-slots line). The section-builder helper (§8.3) therefore takes `types = active types ∪ types of the member's live bookings/holds`. `<section id="equipment-orientation" class="hub-card">` — the anchor the banner and emails target. Heading: **"Get Oriented for the {{ equipment.name }}"** (Title Case, rule 22) with the same `.pl-help` primary-email bubble as the guild partial (`guild_orientation.html:26-29`).

Per renderable type, one group (reuse `pl-orient-type` / `pl-orient-heading` / `pl-orient-slots__*` classes so both themes come for free), `id="orientation-type-{{ pk }}"` with the `?type=` highlight exactly like `guild_orientation.html:37-38`:

- **Header row:** type name, `pl-price-chip` when `is_paid` (`:41`), duration, description.
- **Oriented state:** "✓ You've completed this orientation."
- **Pending-payment hold:** "Finishing Your Booking" + **Resume payment** (primary sm) + **Cancel** (danger sm behind `confirm_modal`) — same endpoints as `guild_orientation.html:49-62`; redirects now land back here (§8.1).
- **Live booking state:** date/time/location line; "Requested. Waiting for a manager to confirm. This isn't official yet." or the green "Confirmed. See you there!"; paid line "Paid {{ amount }}. Automatic refund if this is declined or cancelled."; **Cancel my orientation** behind `confirm_modal` (mirrors `:64-86`).
- **Request state (slot list):** the paged 5-at-a-time slot rows (`:94-145`) minus the orienter column (equipment slots carry none): Date · Time · **Request** button → `confirm_modal`. Free copy: "We'll send your request to the equipment managers to confirm. You can cancel any time." Paid copy: "You'll pay {{ price }} now through our secure checkout. Your booking is a request until a manager confirms it. If they decline, or it's cancelled, you get an automatic full refund." POST → the existing `hub_orientation_book` (`hub/views.py:1444`).
- **Empty state (no bookable slots):** "No orientation times are posted yet. Check back soon." Managers additionally see a link: "Add times from the manage panel." → `?tab=orientation` on the manage page.
- No closed banner (decision 5 — a type toggled inactive simply drops out for members without a live booking/hold on it, per the pinning rule above; when no renderable types remain the whole section disappears), no custom-time request, no availability blocks.
- **Mobile:** slot rows already stack via the `pl-orient-slots__*` grid; nothing new. **Dark + light:** inherited from the reused classes; the only new CSS is the section anchor offset if needed (`scroll-margin-top`).

### 9.2 Requirements banner (equipment_detail.html:44-71)

The `needs_orientation` state gains owner-aware link targets **and a paused variant** (`access_state`/`booking_blockers` never check the required type's `is_active` — `models.py:10392-10399`, `:10423-10425` — so today a deactivated required type is an unresolvable blocker whose "Book the Orientation" button would point at a `#equipment-orientation` anchor that no longer renders):

- Equipment-owned required type (the common case: its own): **"Book the Orientation"** button scrolls to `#equipment-orientation` on this page (full URL + anchor also covers the cross-equipment case).
- Guild-owned required type: today's guild deep link, byte-identical.
- **Paused variant:** when the member is not oriented, has no live booking/hold, and `required_orientation.is_accepting` is False (§4 — covers a deactivated type, retired owning equipment, and a guild's closed/disabled settings alike), the banner shows "You need the {{ name }} orientation before you can book time here. Orientation bookings for this tool are paused. Check back soon." with the book button **suppressed** — never a dead link. `hub_equipment_detail` (`hub/equipment_views.py:307`) passes `required_orientation_paused` alongside `orientation_url`; `booking_blockers`/`access_state` themselves stay unchanged (the gate is real; only the CTA is honest about it).
- Booked/pending sub-states (`:51-56`) still link their anchor — the §9.1 pinning rule guarantees the target group renders even on an inactive type. **Copy fix:** the pending sub-state at `:56` hard-codes "The guild will confirm a time." — it becomes owner-aware: "A manager will confirm a time." when the required type is equipment-owned, guild copy unchanged.

### 9.3 Manage panel — Orientation tab (`templates/hub/equipment_manage.html`)

Fifth tab button in the `pl-tabs` nav (`:12-17`), server whitelist in `hub_equipment_manage` (`hub/equipment_views.py:555-557`) gains `"orientation"`. Tab content, top to bottom (immediate-effect controls none; one batch form per card, Save last, rule 21):

1. **Pending Requests card** — first, because it's the action item. Each REQUESTED booking on this equipment's types: member name + avatar, type name, slot date/time, paid chip when `amount_paid_cents`, and a **Review** `pl-btn pl-btn--primary pl-btn--sm` linking to `hub_orientation_respond` (`hub/views.py:1619`, now equipment-gated per §7) — one review surface for confirm/decline/refunds, no duplicated modal logic. Empty state: "No pending requests."
2. **Orientation Types card** — `EquipmentOrientationTypeFormSet = inlineformset_factory(Equipment, OrientationType, form=OrientationTypeForm, formset=BaseOrientationTypeFormSet, fk_name="equipment", extra=0, can_delete=True)` (`hub/forms.py`, beside `:1698`) — the existing row form (name, description, length, price, seats, location, sort, Active toggle) and the booking-history delete guard (`:1682-1695`) verbatim. Per FRONTEND *Editable Lists*: **"+ Add Orientation Type"** button clones the hidden `<template>` of `empty_form` (swaps `__prefix__`, bumps `TOTAL_FORMS`); saved rows get a real **Delete** `pl-btn pl-btn--danger pl-btn--sm` with `margin-top:0.75rem` that flips the hidden `DELETE` field and `requestSubmit()`s; cloned rows get **Remove** (DOM removal only). One visible **"Save"** primary button, last element in the card's form, POSTing `hub_equipment_orientation_types_save`, ≥1.5rem clearance before the next card (rule 18). Formset delete-guard errors surface inline: the existing booking-history guard ("This orientation has booking history and can't be deleted. Turn off Active to retire it instead."), **plus a new gating guard** — `BaseOrientationTypeFormSet.clean` (`hub/forms.py:1689-1695`) today checks `bookings.exists()` only, so deleting a type that some equipment's `required_orientation` points at sails past the form and 500s on the FK's PROTECT (`models.py:10275-10284`). Extend `clean()` with `form.instance.gated_equipment.exists()` (the FK's `related_name`, `models.py:10280`) raising: "This orientation is required by {equipment names}. Clear that requirement first, or turn off Active to retire it." The guard lives on the shared base formset, so the guild editor's types list (`OrientationTypeFormSet`, `:1698`) gets the same fix for free — a guild type gating equipment currently has the identical 500. Empty state above the + Add button: "No orientation types yet. Add one to start taking bookings on the equipment page."
3. **Upcoming Slots card** — list of upcoming uncancelled slots across the equipment's types (date · time · type · seats taken/total, "1 seat held by a checkout in progress" note via `with_pending_hold_count`, `models.py:8768`); per-row **Cancel** `pl-btn pl-btn--danger pl-btn--sm` (margin-spaced) behind `confirm_modal` ("Cancel this orientation time? Everyone booked on it is notified and paid bookings are refunded.") → `hub_equipment_orientation_slot_cancel`.
   **Attendee rows:** under each slot, its seat-holding real bookings (REQUESTED + CONFIRMED, via the slot's `bookings.active()`, `models.py:9021`) render as indented sub-rows — member name, status chip ("Requested" amber / "Confirmed" green), paid chip when `amount_paid_cents`, each linking to the equipment-gated `hub_orientation_respond` page (§7) where the manager can confirm, decline, or cancel that booking. Without this, a **confirmed** booking is invisible and unactionable for a pure equipment manager (kept off the orientations dashboard by design, §10) — the Pending Requests card only shows REQUESTED. Pending-payment holds stay a count note, never a named row. No attendees: no sub-rows (the seat count already says 0).
   Below the list, an **"+ Add a Time"** reveal-form: new `EquipmentOrientationSlotForm` — type select (this equipment's active types, `empty_label=None`), date (rule 14 picker treatment copied from `OrientationSlotForm`, `hub/forms.py:1878-1884`), start time (`half_hour_time_choices`, rule 20), duration dropdown, seats, location; **"Add"** submit → `hub_equipment_orientation_slot_add` → Django message "Time added." **Reveal state mirrors the Staff tab's bound-form pattern** (`equipment_manage.html:68-69`): `x-data="{ showAdd: {{ slot_add_form.is_bound|yesno:"true,false" }} }"` — an invalid POST re-renders via `_render_manage(…, slot_add_form=bound_form, active_tab="orientation")` with the form **open and its errors visible**, never collapsed inside the `x-show` block. Empty state: "No upcoming times. Add one so members can book." All controls under `.hub-form-group` (rule 13); no orienter field (decision 4).
4. Spacing on the 8px grid throughout (`0.75rem` control gaps, `1.5rem` card gaps). Every POST re-renders the tab with bound errors on invalid (extend `_render_manage`, `hub/equipment_views.py:511`, with the three new context pieces + `active_tab="orientation"`); every success is a Django message + redirect to `?tab=orientation`. Breadcrumb back-link already exists (`equipment_manage.html:5-7`).
- **Mobile:** formset rows and the slot list stack single-column (existing manage-panel patterns); the tab strip already wraps. **Dark + light:** all form controls ride `.hub-form-group` + existing `pl-btn`/`hub-card` tokens; verify the price chip and danger buttons in both themes.

### 9.4 Errors / success / edge states (site-wide sweep)

- Race / guard failures on booking surface as today's friendly `OrientationError` messages (unchanged copy) and land back on the equipment page anchor.
- A member hitting a retired equipment's orientation link: `hub_equipment_detail` already 404s non-managers on inactive equipment (`hub/equipment_views.py:311-312`); `bookable()`/`is_bookable` fail closed (§4.1-4.2), so a crafted `orientation_book` POST gets "This orientation slot is not available to book."
- Checkout return card (`templates/hub/partials/orientation_checkout_return_card.html`) and page: audit for `guild` reads → `owner_name` / `owner_url` keys, same sweep as §8.4.

---

## 10. Orientations dashboard — minimal fix only (no redesign)

Equipment-owned bookings (guild=None) flow into the dashboard's base queryset (`hub/views.py:1952-1966`) — they must neither crash nor silently vanish:

- **Crash fix:** `templates/hub/orientations_dashboard.html:32` and `:93` render `b.guild.name` → `b.orientation_type.owner_name`. (`:153` is a block row — blocks stay guild-only, untouched.)
- **Who sees the rows:** admins see them (base queryset, no guild filter). That is the v1 answer: admins get full visibility; **equipment managers' surface is the manage panel's Orientation tab (§9.3), not the dashboard** — `_can_access_orientations` (`:1893`) and the "Mine" scope (`_filter_orientations` `:1922-1923`, guild-joined, excludes guild=None rows) are deliberately left untouched. A guild lead who is *also* an equipment manager sees the rows in the unfiltered admin-visible lists only if they're an admin; their "Mine" filter correctly shows only their guild's rows. Document this in the tab's help copy if needed; widening dashboard access for pure equipment managers is out of scope (§13).
- `_manageable_slots` (`:1902-1913`, the add-member-to-slot picker) already includes equipment slots for admins and correctly excludes them for leads (guild=None fails the lead filter). `orientation_add_member` (`:2059`) therefore works for admins on equipment slots with zero changes — its fan-out is owner-aware after §5. No further scoping change.
- Guild filter dropdown: guild=None rows simply never match a guild id (`:1920-1921`) — acceptable; "All" shows them.

---

## 11. Build order (ONE PR)

1. **Migration + model layer**: §3 fields/constraints, §4 owner helpers + `__str__`s, `bookable()`/`is_bookable`/`confirm`/`mark_completed`/`refund_receipt_context` branches. Factories. Model specs green. `manage.py check`.
2. **Service sweep**: §5 replacements in `membership/orientations.py`; §6 resolver + registry + settings-matrix wiring. Service/event specs green.
3. **Permission + view wiring**: §7 `_require_can_manage_booking`, §8.1 `_owner_redirect` sweep, §8.5 form changes.
4. **Member surface**: shared section-builder helper, equipment detail context + `equipment_orientation.html` partial + banner link change (§8.3, §9.1-9.2).
5. **Manage surface**: Orientation tab, 2 forms, 3 endpoints (§8.2, §9.3).
6. **Email template sweep** (§8.4) + dashboard/respond/action template `owner_name` fixes (§10, §8.1).
7. **Housekeeping**: `plfog/version.py` `VERSION` 1.31.4 → **1.32.0** (minor — net-new member-facing feature) + changelog. Run `template_comment_lint_spec`, `ruff format/check`, `manage.py check`, targeted suites.

**Changelog entry** (stamped at 1.32.0; the Discord announce fires automatically on merge when VERSION changes — curate before merging; plain language, NO dashes anywhere):

> *"Tool orientations on the tool's page: Some equipment needs an orientation before you can reserve it. You can now book that orientation right on the tool's own page. Pick a time, request it, and pay there if it has a fee. Equipment managers post the times and confirm requests from the tool's manage panel."*

(House changelog rule: if the equipment-reservations line 1.31.x is still unreleased on production at merge time, fold this into that feature's entry instead of adding a second one — check prod version first.)

---

## 12. Testing (BDD `*_spec.py`, `describe_*`/`it_*` ONLY — `context_*` blocks are silently skipped)

Homes: `tests/membership/` and `tests/hub/` (root tests tree). Extend the existing files where the subject lives: `tests/membership/orientation_models_spec.py`, `orientations_service_spec.py`, `orientation_checkout_spec.py`, `orientation_refunds_spec.py`, `equipment_spec.py`, `equipment_permissions_spec.py`; `tests/hub/equipment_views_spec.py`, `guild_orientation_tab_spec.py`, `orientation_booking_spec.py`, `orientation_checkout_views_spec.py`, `orientations_dashboard_spec.py`. New file for the manage tab: `tests/hub/equipment_orientation_manage_spec.py`. Time fixtures at `now + timedelta(days=2)` (the tz day-window gotcha). Fast-merge gate applies (targeted suites, not the mutation job).

**Constraint / model (`orientation_models_spec.py`, `equipment_spec.py`)**
- Exactly-one-owner: guild-only OK, equipment-only OK; both set → IntegrityError; neither → IntegrityError.
- `uq_orienttype_equip_name`: duplicate name on one equipment rejected; same name on two different equipment OK; guild constraint still enforced for guild-owned.
- Owner helpers: `owner` / `owner_name` / `owner_page_path` / `orientation_anchor_path` / `is_accepting` / `default_runner` per owner type; `__str__` for type/slot/booking with guild=None.
- `is_accepting` equipment path: active type + active equipment True; retired equipment False; inactive type False; **no `GuildOrientationSettings` row ever consulted** (assert zero queries against it, or a settings row for an unrelated guild changes nothing).
- `bookable()` truth table: equipment slot included with active equipment/type, excluded when equipment retired, type inactive, slot past/cancelled; guild slots byte-identical to today (regression block re-running the existing filters).
- `is_bookable` equipment branch skips the orienter-leadership check; `book()` produces a booking with `guild is None`; duplicate/oriented/hold guards fire per type as before.
- `confirm()` / `mark_completed()` on an equipment booking with no explicit `oriented_by` → no crash, runner None (and the passed-member path stamps correctly).
- `refund_receipt_context` on an equipment booking: item title carries the equipment name, `manage_url`/`in_app_url` point at the equipment page.
- PROTECT: deleting equipment that owns a type raises ProtectedError.

**Service end-to-end (`orientations_service_spec.py`, `orientation_checkout_spec.py`, `orientation_refunds_spec.py`)**
- Free flow: request → manager confirm → auto_complete → `Member.is_oriented_for_type` True → `Equipment.booking_blockers` empties (`models.py:10424`) — the full unlock loop.
- Paid flow: `start_orientation_checkout` on an equipment slot (product name = equipment), webhook `finalize_paid_booking` fires the fan-out, decline auto-refunds, receipt context owner-correct.
- `.ics` summary/description carry the equipment name; `.txt`/`.html` email parity; every rendered channel body for the equipment emit context contains **no** unresolved `{{ … }}` and no `[missing:]` holes.
- `cancel_slot` on an equipment slot: bookings cancelled + refunded, holds released, no guild reads crash.
- `complete_orientation` equipment path sends the standard thank-you with the equipment name; guild path with custom copy unchanged.

**Notification audience (§6)**
- Equipment emit context resolves to exactly `manager_members()` (staff row + owning-guild leadership + capability holder, deduped), no guild broadcast, correct tags; guild context still resolves orienters/personal-slot narrowing (regression); missing both keys fails loudly.
- Settings matrix: the `orientation_requested` row visible to an equipment staff-row holder and an EQUIPMENT capability holder; invisible to a plain member.

**Views (`tests/hub/…`)**
- `orientation_book` on an equipment slot redirects to the equipment page + `#equipment-orientation` (free and paid-error paths); guild slot redirect unchanged.
- Detail page: Orientation section renders per state — slot list with price chip, Request modal copy, oriented ✓, pending request, resume-payment hold, empty "No orientation times are posted yet."; section absent with no renderable types; `?type=` highlight lands.
- **Inactive-type pinning (§9.1):** a member with a CONFIRMED booking on a since-deactivated type still sees the group with its Cancel button; a PENDING_PAYMENT hold still shows Resume payment / Cancel; the inactive group renders no slot list or Request buttons; a member with no booking on the inactive type doesn't see it at all.
- Banner: equipment-owned required type → anchor link; guild-owned → guild deep link (both asserted on rendered HTML). **Paused variant (§9.2):** required type deactivated (and separately: owning equipment retired) → "paused" copy renders, no "Book the Orientation" button, no dead anchor link; with a live booking on the paused type the booked sub-state and its anchor link render instead. **Owner-aware pending copy:** equipment branch shows "A manager will confirm a time.", guild branch keeps "The guild will confirm a time." (`equipment_detail.html:56`).
- Manage tab: types formset save creates an equipment-owned type (guild None); + Add / Delete flow; booking-history delete guard message; **gated-type delete guard** — deleting a type referenced by any `Equipment.required_orientation` re-renders with the "required by {equipment}" message and no ProtectedError (asserted for the equipment formset AND the guild editor's formset, which shares the base class); slot add creates guild=None orienter=None MANUAL slot; **bound slot-add form re-renders revealed** (invalid POST → response contains the open form with field errors, `showAdd` initialized true, tab context `orientation`); slot cancel fans out; pending list links respond.
- **Attendee rows (§9.3):** a slot's REQUESTED and CONFIRMED bookings render under it with member name + status chip + respond link; PENDING_PAYMENT holds appear only as the held-seat count; an `EquipmentStaffMembership` manager can open the linked respond page for a confirmed booking and cancel it (permission edge already covered), while a random guild lead 403s on it.
- **Inactive-selected `required_orientation` (§8.5):** an equipment whose saved required type is now inactive round-trips a Details save unchanged (no invalid-choice error); the inactive type is absent from the choices of an equipment that doesn't already have it selected.
- **Permission edges (crafted POSTs):** a plain member and a random guild lead each 403 on all three manage endpoints and on `orientation_respond` for an equipment booking; an `EquipmentStaffMembership` MANAGER, the owning guild's lead, and an EQUIPMENT capability holder each succeed; view_as preview respected on the admin leg.
- `EquipmentForm`: own type offered and saves as `required_orientation`; cross-owner mismatch still errors.
- Dashboard: an equipment-owned booking renders (owner name shown, no 500) for an admin; "Mine" scope for a lead excludes it; guild filter excludes it; `orientation_add_member` as admin on an equipment slot works.
- Changelog-renders-everywhere check: no new UI-copy string collides with existing negative assertions.

---

## 13. Out of scope (explicit punts)

- **Per-orienter recurring availability / availability blocks for equipment** — slots are added directly on the manage tab (decision 4); `OrientationAvailability`/`OrientationAvailabilityBlock` stay guild-only and guild-required.
- **A `GuildOrientationSettings` equivalent for equipment** (enabled/closed switches, info page, custom thank-you copy) — `is_active` on the type is the switch; the standard thank-you copy sends.
- **Custom-time requests for equipment orientations** — a guild-settings feature (`allow_custom_requests`); revisit with the settings equivalent.
- **Dashboard redesign / dashboard access for pure equipment managers** — the minimal §10 fix only; their surface is the manage tab.
- **Migrating the house "Makerspace" guild's existing tool types onto equipment ownership** — data can be moved by hand later (set `equipment`, clear `guild`); no code or migration does it.
- **Orienter assignment on equipment slots** — equipment slots are always "any manager"; a with-a-person label returns only if demand appears.

## 14. Done criteria

- A member on a gated tool's page books its orientation, pays if priced, gets confirmed by an equipment manager, is auto-completed, and comes back to a green banner and a bookable schedule — never touching a guild page.
- Every guild-owned orientation surface (guild tab, dashboard, emails, checkout, refunds) behaves byte-identically to before — the regression blocks in §12 prove it.
- No code path reads `.guild.` off a slot/booking without an owner-aware guard; the §5 table is fully applied.
- Exactly one of guild/equipment on every `OrientationType`, DB-enforced.
- Equipment managers — and only the three tiers — can save types, post/cancel slots, and confirm/decline requests; every gate is `can_manage_equipment`-derived.
- One migration, one VERSION bump to 1.32.0, one curated no-dash changelog entry.

> Spec only — do not build until approved.
