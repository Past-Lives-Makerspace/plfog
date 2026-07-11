# Signage Slideshow (`slideshow.pastlives.space`) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-10
**Surface:** New public host `slideshow.pastlives.space` — an unattended, full-screen, auto-advancing digital-signage player that wall monitors around the makerspace point at. Also adds a new **"Slideshow" tab** to Site Settings on the members hub (`pastlives.test` / `members.pastlives.space`) where admins configure everything.
**Related:** `2026-07-01-guest-guilds-surface.md` (the closest precedent — a brand-new public `SurfaceMiddleware` surface with a routing branch, an allowlist gate, a context-processor flag, and a public undecorated view). This spec mirrors its plumbing but is a **read-only kiosk**, not an act-in-place guest site — and its privacy constraint is the *reverse* (see Locked decisions).

---

## 1. Summary

A wall-mounted monitor in the woodshop (or the lobby, or a classroom) opens `slideshow.pastlives.space/woodshop/` once and is never touched again. It shows a slow, full-screen slideshow — upcoming makerspace events, admin-written "tips about the space," image/flyer slides, and a hand-picked guild announcement or two — cycling on its own, each slide sized to be read across a room. Admins build and reorder those slides, create the per-screen "zones" (woodshop vs. lobby vs. classroom), and flip an **emergency takeover** (one banner on every screen) entirely from a new **Slideshow** tab in Site Settings — no code, no touching the monitor. The screen re-polls for fresh content every five minutes and fully reloads itself nightly, so a deploy or a new event appears on its own.

Members experience this as ambient signage in the physical space; the whole *configuration* surface is admin-only. There is no login, no member data, and no interactivity on the screen itself — the only call-to-action is a QR code a passer-by can scan.

### Locked decisions (from the brief — presented as decided; not re-litigated)

| Decision | Choice |
|---|---|
| New surface + host | New `signage` surface on `slideshow.pastlives.space`. A `SurfaceMiddleware` branch mirroring `_handle_guilds_surface` (`core/middleware.py`); new settings `SIGNAGE_HOSTS` (default `["slideshow.pastlives.space"]`), `SIGNAGE_BASE_URL` (`https://slideshow.pastlives.space`), and a tight `SIGNAGE_ALLOWED_VIEW_NAMES` frozenset allowlist. Everything not allowlisted → 404. **Not** added to `PUBLIC_HOSTS`/`GUILDS_HOSTS`. Shared `ROOT_URLCONF` (`plfog.urls`) — **no new urlconf**. |
| Root routing | `/` on the signage surface 302-redirects to the first enabled zone's player (by `sort_order`); if **zero** zones exist, a friendly "No screens configured yet" holding page (HTTP 200, not a redirect loop). |
| Fully public, no login | The player view is an **undecorated `def`** (like `guild_directory`). |
| **Auth-parity privacy (the sharpest constraint)** | The signage host is `slideshow.pastlives.space`, a subdomain of `.pastlives.space`. Prod sets `SESSION_COOKIE_DOMAIN=.pastlives.space`, so an **admin already logged in on the members site arrives at the kiosk URL AUTHENTICATED** in the same browser. Therefore the player must render **byte-identical PUBLIC content regardless of `request.user`** — it **NEVER** branches content or chrome on `user.is_authenticated`. A test proves an authenticated request and an anonymous request to the player produce identical public output (no member names, no PII, no per-user chrome). *(This is the inverse of the guilds surface, which sits on the different registrable domain `.app` and gets host-only cookies — that reasoning does NOT transfer; signage genuinely receives authenticated admins.)* |
| Event-slide privacy | Event slides are **site-wide events only** (`guild__isnull=True`) — never private-guild meetings. **Do NOT reuse** `_get_calendar_context` / the community-calendar context — it merges iCal feed rows + synthetic guild/class/orientation entries with **no privacy filter** (confirmed leak, `hub/views.py:2849`). Query `CommunityEvent` directly. |
| Base template | New **standalone** `<!doctype html>` root `templates/signage/base.html` — see the correction below. Dark, full-bleed, room-legible type. **Does not register the service worker (`sw.js`) or the PWA manifest.** |
| Auto-advance | Alpine `x-data` + timers (no carousel library — build it). Each slide shows for its own `duration_seconds`, falling back to the global default. |
| Freshness | HTMX polls the deck partial every 300s (`hx-trigger="every 300s"`) **and** a scheduled full `location.reload()` nightly (~4am). Exact mechanism in §6.1. |
| Zones | `SlideshowZone(name, slug[unique], is_enabled, sort_order)`, admin-managed. Slide→zone is a **single FK** (`NULL` = show on all zones). Multi-zone-per-slide (M2M) **deferred to v2**. |
| Slides | `SlideshowSlide` with `kind ∈ {custom, announcement}`; date-window scheduling; per-zone or all-zones. Announcement-backed slides render the linked `GuildAnnouncement`'s live title/body and auto-hide when it's unpublished/expired. |
| Emergency takeover | Fields on the `SiteConfiguration` singleton (`signage_alert_active/heading/message`), **not** a slide row. When active, every zone shows **only** the alert, overriding all rotation. |
| Admin config | Admin-only (`@fog_admin_required`), on the new Slideshow Site Settings tab. Follows the exact 6-step "add a tab" recipe. |
| Go-live infra (deferred; §10) | DNS `slideshow.pastlives.space` → Render; add host to `DJANGO_ALLOWED_HOSTS`; set `SIGNAGE_HOSTS`. **No `CSRF_TRUSTED_ORIGINS` entry** needed — the player is read-only (no POST), and the admin config lives on the already-trusted members host. |

### Correction folded in: the signage base is a **standalone root**, not `extends "base.html"`

The original brief said "extend the minimal `templates/base.html`." Reconnaissance showed **`base.html` is not actually bare** — it hardcodes the `sw.js` service-worker registration, the `<link rel="manifest">` PWA manifest, a BETA-badged `<header class="site-header">`, a footer, and the changelog modal **outside any `{% block %}`**, so a child extending it **cannot strip them**. That directly violates the "no service worker / bare full-screen" requirement. The correct approach — and what this spec adopts — is a **standalone `<!doctype html>` document** at `templates/signage/base.html`, the same way `hub/base.html` is its own full document (it extends nothing). It registers **no** service worker, includes **no** manifest, shows **no** BETA header/footer, and loads only Alpine (+ HTMX), its own dark full-bleed CSS, and the fonts. The "extend base.html" wording in the brief was wrong; this standalone-root decision is deliberate and stated here so no one "fixes" it back.

---

## 2. What already exists (reuse, don't reinvent)

The build is mostly assembly. Confirmed in the working tree (line numbers may drift):

| Need | Existing thing | Location |
|---|---|---|
| Host → surface routing + allowlist gate | `SurfaceMiddleware.__call__` (guilds branch `:59`, `_handle_guilds_surface` `:79`) | `core/middleware.py:48` |
| Surface settings pattern to mirror | `GUILDS_HOSTS` / `GUILDS_BASE_URL` / `GUILDS_ALLOWED_VIEW_NAMES` frozenset | `plfog/settings.py:104`, `:109`, `:116` |
| Surface flags for templates | `surface()` context processor (`is_guilds_surface`, `guilds_page_base`, `parent_template`, exposes `*_BASE_URL`) | `core/context_processors.py:50` |
| Public, undecorated, pre-login view precedent | `guild_directory` (undecorated `def`); vanity redirect lives in **core**, not hub (hub is `@login_required` by convention) | guest-guilds spec §5 / §12 |
| Site-wide event query (the privacy-safe path) | `CommunityEvent.objects.published().upcoming().filter(guild__isnull=True)` — all three chain (`CommunityEventQuerySet`); `.site_wide()` is the same filter | `membership/models.py:2421`, `.published():2458`, `.upcoming():2424`, `.site_wide():2447` |
| Next-occurrence expansion (recurring events) | `_next_occurrence(event, frm, to, now)` loop + `CommunityEvent.occurrences_in(frm, to) -> list[datetime]` | `hub/home.py:106` & `:146`; `membership/models.py:2681` |
| Event display string + link | `CommunityEvent.when_display` (e.g. `Sat, Jul 12 · 6:00 PM – 8:00 PM`); `.absolute_url` (→ `hub_community_calendar`, member host) | `membership/models.py:2748`, `:2765` |
| Announcement "still-live" gate to mirror | `GuildAnnouncement.objects.published()` (`moderation_state == PUBLISHED`) + `.active()` (`expires_at` null OR `>= today`) | `membership/models.py:1777`–`1788`; model `:1804`; `expires_at` is a **DateField** `:1825` |
| Published-announcement picker source | `GuildAnnouncement.objects.published()` | `membership/models.py:1782` |
| Image upload stack (upload → validate → resize → orphan-cleanup, R2 in prod) | `ImageField(upload_to=…, validators=[validate_image_size])` + `save()` calling `delete_orphan_on_replace(self, "image")` then `normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_*)` | `GuildImage` `membership/models.py:1508`–`1528`; helpers `core/validators.py:10`, `core/images.py:69`, `core/files.py:8`; sizes `settings.py:280`/`:287`/`:288` |
| QR generation (pure-Python, no Pillow/native) | `segno.make(url, error="m").save(buf, kind="svg", scale=1, xmldecl=False, svgns=True)` / `kind="png", scale=10, border=2` | `Guild.qr_svg`/`qr_png_bytes` `membership/models.py:1252`–`1271`; `segno` already in `requirements.txt` |
| Absolute per-URL property pattern | `Guild.vanity_url` (`settings.MEMBER_BASE_URL + reverse(...)`) | `membership/models.py:1242` |
| Site config singleton + global toggles | `SiteConfiguration` (pk=1), `load()` | `core/models.py:100`, `.load():264` |
| Site Settings form (ModelForm over `SiteConfiguration`) | `SiteSettingsForm` — explicit `Meta.fields=[…]` (`:530`) + `widgets` dict (`:547`) | `hub/forms.py:520` |
| Site Settings view + save + tab allow-set | `admin_site_settings` (`@fog_admin_required`, `hub_admin_site_settings`, `hub/urls.py:308`); `_save_site_settings` validates `SiteSettingsForm` + `CalendarFeedFormSet(prefix="feeds")`, redirects `?tab={submitted_tab}`; `active_tab` allow-set | `hub/views.py:4180`, `:4158`, allow-set `:4193` |
| Admin-only decorator | `fog_admin_required` | `hub/view_as.py:205` |
| Repeating editable-row formset precedent | `CalendarFeedFormSet = modelformset_factory(CalendarFeed, form=CalendarFeedForm, extra=0, can_delete=True)`; "skip blank +Add rows" save idiom | `hub/forms.py:567`; save `hub/views.py:~4170` |
| Ordered editable-list model precedent | `CalendarFeed` (sort_order, core); `GuildFAQItem` / `GuildLink` (membership) | `core/models.py:271`; `membership/models.py:1531`/`1594` |
| Canonical editable-list **editor UI** (Add / Delete / Save) | FAQ + Links editors — `<template>` of `empty_form`, "+ Add" clone JS, hidden `{{ f.DELETE }}` + real `pl-btn pl-btn--danger pl-btn--sm` Delete (`margin-top:0.75rem`, `requestSubmit()`), multipart form | `templates/hub/guild_edit.html:347`–`473` |
| Separate-form-on-the-same-tab precedent | The Announcements tab is its own `<form>` (line `:404`), sibling to the shared `#site-settings-form` (`:134`) | `templates/hub/admin/site_settings.html` |
| Field/toggle components | `components/form_field.html` (auto-toggles checkboxes; wraps in `.pl-form-group`), `components/toggle.html`, `components/confirm_modal.html`, `components/modal.html` | `templates/components/*` |
| Standalone-doc base precedent (own `<!doctype>`, no sw.js) | `hub/base.html` (546 lines, extends nothing); public base wraps it | `templates/hub/base.html`; `templates/guilds/base_public.html` |

### Genuine gaps to close (kept minimal)

1. **A fourth surface, `"signage"`.** `SurfaceMiddleware` knows `guilds`/`public`/`members`. Add `SIGNAGE_HOSTS` + a `"signage"` branch with `_handle_signage_surface` (root → first-zone / holding page; allowlist gate).
2. **A standalone kiosk base + player + partials + CSS.** `templates/signage/base.html`, `player.html`, `_deck.html`, `no_zones.html`; `static/css/signage.css`. The player is the one genuinely new screen — there is no full-screen kiosk template today.
3. **Two new models + `SiteConfiguration` fields.** `SlideshowZone`, `SlideshowSlide` (in `membership`, see §3 for why), plus seven `SiteConfiguration` fields (global + emergency alert).
4. **A render-time deck builder** (`membership/signage.py`) that merges visible slides + generated event slides — the only real logic, kept in a service so the view stays thin.
5. **The Slideshow admin tab** — two formsets (zones, slides), two dedicated save views, tab wiring (the 6-step recipe).

---

## 3. Where the code lives

```
core/
  middleware.py            # + "signage" surface branch + _handle_signage_surface (root→first zone / holding page; allowlist gate)
  context_processors.py    # surface(): + is_signage_surface, signage_page_base, expose SIGNAGE_BASE_URL
  views.py                 # + signage_player, signage_deck  (PUBLIC, undecorated; surface-guarded; core, not hub)
  urls.py                  # + signage routes, appended LAST (see §6.0 collision note)
  spec/
    middleware/surface_middleware_spec.py   # + signage routing/gate/root/holding cases
    context_processors_spec.py               # + signage flags
    views/signage_player_spec.py             # NEW — auth-parity, zone 404, empty/holding, alert takeover, event filter
plfog/
  settings.py              # + SIGNAGE_HOSTS, SIGNAGE_BASE_URL, SIGNAGE_ALLOWED_VIEW_NAMES
  version.py               # bump VERSION + CHANGELOG entry (last phase)
membership/
  models.py                # + SlideshowZone, SlideshowSlide (+ managers/querysets); reuses GuildAnnouncement/CommunityEvent/image stack in-app
  signage.py               # NEW service — build_deck(zone) → ordered slide view-models (visible slides + generated event slides)
  migrations/00XX_slideshow_models.py        # additive CreateModel (reverse = automatic RemoveModel)
  spec/
    models/slideshow_spec.py                 # NEW — visible(), for_zone(), player_url, qr_svg, __str__, defaults
    signage_spec.py                          # NEW — build_deck: site-wide-only event filter, horizon/cap, duration fallback, announcement pull/auto-hide
core/models.py             # + SiteConfiguration signage_* fields (global + alert)
core/migrations/00XX_siteconfiguration_signage.py   # additive AddField (reverse = automatic RemoveField)
hub/
  forms.py                 # SiteSettingsForm.Meta.fields += signage_* ; + SlideshowZoneForm/FormSet + SlideshowSlideForm/FormSet
  views.py                 # admin_site_settings: "slideshow" in active_tab allow-set; + admin_slideshow_zones_save, admin_slideshow_slides_save (@fog_admin_required)
  urls.py                  # + hub_admin_slideshow_zones_save, hub_admin_slideshow_slides_save
  spec/
    forms/site_settings_form_spec.py         # + signage fields present/saved
    views/admin_slideshow_spec.py            # NEW — tab renders, formset add/delete/save, image upload, gating
templates/
  signage/
    base.html              # NEW standalone <!doctype> kiosk root — NO sw.js, NO manifest, NO BETA chrome; Alpine (+ HTMX); signage.css; fonts; forced dark
    player.html            # NEW — extends signage/base.html; the rotating deck
    _deck.html             # NEW — the slides OR the emergency alert (shared by first paint + the 300s HTMX poll)
    no_zones.html          # NEW — "No screens configured yet" holding page (extends signage/base.html)
  hub/admin/
    site_settings.html     # + Slideshow tab button, x-show section (global+alert from shared form; zones + slides editor forms), exclusion-chain entries
static/css/
  signage.css              # NEW — dark full-bleed kiosk CSS, PLAYER ONLY (self-contained token block, like guild-flyer.css). NOT loaded on the hub.
  hub.css                  # + admin Slideshow-tab editor rules (pl-slideshow-*). The tab renders under hub/base.html (loads hub.css + components.css), NOT signage.css.
```

Home apps: `core` (routing/plumbing + public player views + `SiteConfiguration` fields), `membership` (the two new models + the deck service — see below), `hub` (admin tab form/views). No new Django app.

**Why the models live in `membership`, not `core`:** `SlideshowSlide` needs an FK to `membership.GuildAnnouncement`, and the deck builder reuses `CommunityEvent` and the `GuildImage` image stack — all in `membership`. Placing the models in `core/models.py` would add a **`core → membership` model FK edge**, and `membership` already has a `core` edge (`membership.CalendarEvent.feed → core.CalendarFeed`), creating an app-graph cycle and migration-ordering smell. Putting the new models in `membership` keeps the `GuildAnnouncement` FK **intra-app** and touches no new cross-app edges. The seven global/alert flags stay on `core.SiteConfiguration` (they're plain config, no FK).

---

## 4. Data model

CLAUDE.md patterns throughout: `TextChoices`, `help_text` on every field, meaningful `__str__`, `default=dict` never `{}`, fat querysets. All migrations additive; reverse = Django's automatic `RemoveModel`/`RemoveField` (no custom `RunPython`).

### 4.1 `SlideshowZone` (new — `membership/models.py`)

One row per physical screen location.

| Field | Type | Note |
|---|---|---|
| `name` | `CharField(max_length=100, help_text="Where this screen lives, e.g. 'Woodshop' or 'Lobby'.")` | Human label. |
| `slug` | `SlugField(max_length=100, unique=True, help_text="Used in the screen's URL: slideshow.pastlives.space/<slug>/. Point the monitor here once.")` | The set-and-forget per-monitor URL segment. Editable so admins control the URL; auto-suggested from `name` via `slugify` in the form's `clean` if left blank. |
| `is_enabled` | `BooleanField(default=True, help_text="Turn a screen's URL on or off. A disabled zone's URL returns 404.")` | Gates routing (`get_object_or_404(..., is_enabled=True)`). |
| `sort_order` | `PositiveIntegerField(default=0, help_text="Lower numbers sort first. The root URL redirects to the first enabled zone.")` | Drives root-redirect target + editor order. |

`Meta.ordering = ["sort_order", "name"]`. `__str__` → `self.name`.

**Fat-model helpers (one source of truth for the setup URL + its QR):**

```python
@property
def player_url(self) -> str:
    """Absolute, set-and-forget URL to point a monitor at, e.g. https://slideshow.pastlives.space/woodshop/."""
    from django.urls import reverse
    return f"{settings.SIGNAGE_BASE_URL}{reverse('signage_player', args=[self.slug])}"

def qr_svg(self) -> str:
    """Inline SVG QR of this zone's player_url — shown on the admin tab so staff can point a monitor at it."""
    import io, segno
    buf = io.BytesIO()
    segno.make(self.player_url, error="m").save(buf, kind="svg", scale=1, xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")
```

### 4.2 `SlideshowSlide` (new — `membership/models.py`)

```python
class SlideshowSlide(models.Model):
    class Kind(models.TextChoices):
        CUSTOM = "custom", "Custom slide"
        ANNOUNCEMENT = "announcement", "Guild announcement"
```

| Field | Type | Note |
|---|---|---|
| `kind` | `CharField(max_length=20, choices=Kind.choices, default=Kind.CUSTOM, help_text="Custom = your own title/body/image. Guild announcement = mirror a published announcement.")` | Row fields switch on this (§6.2). |
| `zone` | `ForeignKey(SlideshowZone, null=True, blank=True, on_delete=CASCADE, related_name="slides", help_text="Show only on this screen. Leave blank to show on every screen.")` | `NULL` = all zones. |
| `title` | `CharField(max_length=200, blank=True, default="", help_text="Headline for a custom slide.")` | Custom only. |
| `body` | `TextField(blank=True, default="", help_text="Body text for a custom slide — a tip about the space, a reminder, etc.")` | Custom only; TextField → wrap in `.pl-form-group` (Rule 13). |
| `image` | `ImageField(upload_to="signage/slides/", blank=True, null=True, validators=[validate_image_size], help_text="Optional full-bleed image or flyer (JPG/PNG).")` | Reuses the `GuildImage` stack in `save()` (below). |
| `link_url` | `URLField(blank=True, default="", help_text="Optional link — shown as a QR code when 'Show QR' is on.")` | Custom only. |
| `show_qr` | `BooleanField(default=False, help_text="Render a scannable QR of the link on this slide.")` | Custom only; toggle. |
| `announcement` | `ForeignKey(GuildAnnouncement, null=True, blank=True, on_delete=CASCADE, related_name="+", help_text="The published announcement to mirror. Only used for 'Guild announcement' slides.")` | Announcement only; picker = `published()`. |
| `duration_seconds` | `PositiveIntegerField(null=True, blank=True, help_text="How long this slide shows. Leave blank to use the global default.")` | Blank → `SiteConfiguration.signage_default_slide_seconds`. |
| `starts_on` | `DateField(null=True, blank=True, help_text="Optional: don't show before this date.")` | Lower bound. |
| `ends_on` | `DateField(null=True, blank=True, help_text="Optional: stop showing after this date.")` | Upper bound. |
| `is_enabled` | `BooleanField(default=True, help_text="Turn this slide on or off without deleting it.")` | Toggle. |
| `sort_order` | `PositiveIntegerField(default=0, help_text="Lower numbers show first in the rotation.")` | Deck order. |

`Meta.ordering = ["sort_order", "id"]`. `__str__` → `self.title or f"Announcement: {self.announcement}" if announcement else f"Slide {self.pk}"`.

**Image stack in `save()`** (verbatim reuse of the `GuildImage` pattern — full-bleed wall art wants the larger long-edge):

```python
def save(self, *args, **kwargs):
    delete_orphan_on_replace(self, "image")
    normalize_field_if_uploaded(self, "image", settings.IMAGE_MAX_LONG_EDGE_HERO)  # 2400px — wall-sized
    super().save(*args, **kwargs)
```

### 4.3 `SlideshowSlideQuerySet` / manager (fat model — the visibility rules live here)

```python
class SlideshowSlideQuerySet(models.QuerySet):
    def visible(self, today=None):
        """Enabled slides inside their date window; announcement slides also require
        their linked announcement to be published AND still active (auto-hide)."""
        today = today or timezone.localdate()
        window = (
            Q(is_enabled=True)
            & (Q(starts_on__isnull=True) | Q(starts_on__lte=today))     # lower bound
            & (Q(ends_on__isnull=True) | Q(ends_on__gte=today))         # upper bound
        )
        live_announcement = (
            ~Q(kind=SlideshowSlide.Kind.ANNOUNCEMENT)                    # custom slides pass this clause
            | (
                Q(announcement__moderation_state=GuildAnnouncement.ModerationState.PUBLISHED)
                & (Q(announcement__expires_at__isnull=True) | Q(announcement__expires_at__gte=today))
            )
        )
        return self.filter(window & live_announcement)

    def for_zone(self, zone):
        """Slides pinned to this zone plus the all-zones (zone IS NULL) slides."""
        return self.filter(Q(zone__isnull=True) | Q(zone=zone))
```

`objects = SlideshowSlideQuerySet.as_manager()`.

**Note on the date window:** `GuildAnnouncement.active()` is a **one-sided** gate (only `expires_at`); there is no start-date precedent to copy. `visible()` deliberately adds the **lower bound** (`starts_on`) as well, so slides can be scheduled to appear in the future. Comparisons use `timezone.localdate()` against `DateField`s (Portland/project tz), consistent with `active()`.

### 4.4 `SiteConfiguration` new fields (`core/models.py` — global + emergency alert)

Added to the singleton; declared in the house style (`verbose_name`/`help_text`), added to `SiteSettingsForm.Meta.fields`.

| Field | Type | Note |
|---|---|---|
| `signage_default_slide_seconds` | `PositiveIntegerField(default=12, help_text="Default seconds each slide shows, unless a slide overrides it.")` | Global fallback duration. |
| `signage_show_events` | `BooleanField(default=True, help_text="Automatically add slides for upcoming site-wide events.")` | Turns generated event slides on/off. |
| `signage_event_days_ahead` | `PositiveIntegerField(default=30, help_text="How many days ahead to pull upcoming events for the slideshow.")` | Deck horizon. |
| `signage_event_qr` | `BooleanField(default=False, help_text="Add a QR code to the community calendar on auto event slides.")` | Off by default (the calendar it points to currently requires login — §10). |
| `signage_alert_active` | `BooleanField(default=False, help_text="EMERGENCY: when on, every screen shows ONLY the alert below until you turn it off.")` | The takeover switch. |
| `signage_alert_heading` | `CharField(max_length=120, blank=True, default="", help_text="Big alert headline, e.g. 'Building Closed'.")` | Alert title. |
| `signage_alert_message` | `TextField(blank=True, default="", help_text="Alert details shown under the headline.")` | Alert body (Textarea widget → `.pl-form-group`). |

**Migrations:** one `CreateModel` migration in `membership` (both models) and one `AddField` migration in `core` (the seven fields). Both plain additive; reverse is Django's automatic `RemoveModel`/`RemoveField` — no data migration, no `RunPython`.

---

## 5. Business logic (fat models / service — views stay thin)

### 5.1 `membership/signage.py` — the deck builder (the only real logic)

A small service, because it orchestrates three models (`SlideshowSlide` + `CommunityEvent` + `SiteConfiguration`) — cross-model orchestration belongs in a service, not a view or template.

```python
@dataclass(frozen=True)
class SignageSlideVM:
    kind: str                 # "custom" | "announcement" | "event" | "holding"
    title: str
    body: str
    image_url: str | None
    qr_svg: str | None        # inline SVG when a QR should render
    duration_seconds: int
    meta: str = ""            # e.g. an event's when_display / location

SIGNAGE_EVENT_CAP = 8

def build_deck(zone: SlideshowZone) -> list[SignageSlideVM]:
    """Ordered slides for one zone: configured slides (by sort_order) then generated
    event slides (soonest first, capped). Emergency alert is handled in the VIEW, not here."""
    config = SiteConfiguration.load()
    default = config.signage_default_slide_seconds

    deck: list[SignageSlideVM] = []
    for slide in SlideshowSlide.objects.for_zone(zone).visible().select_related("zone", "announcement"):
        deck.append(_slide_vm(slide, default))

    if config.signage_show_events:
        deck.extend(_event_slides(config, default))

    if not deck:
        deck.append(_holding_vm(default))   # branded fallback — never an empty screen
    return deck
```

- `_slide_vm(slide, default)` — for `kind == custom`: use the slide's own `title/body/image`; render `qr_svg` from `link_url` (segno) when `show_qr` and `link_url`. For `kind == announcement`: pull the **linked announcement's live `title`/`body`** (the FK is guaranteed published+active by `visible()`), no image, no QR. `duration = slide.duration_seconds or default`.
- `_event_slides(config, default)` — the **privacy-safe, site-wide-only** generator, copying the `hub/home.py` expansion pattern:
  ```python
  now = timezone.now(); today = now.date()
  horizon = today + timedelta(days=config.signage_event_days_ahead)
  vms = []
  for event in CommunityEvent.objects.published().upcoming().filter(guild__isnull=True):
      occ = _next_occurrence(event, today, horizon, now)   # first occurrence >= now within window
      if occ is None:
          continue
      qr = _qr_svg(event.absolute_url) if config.signage_event_qr else None
      vms.append(SignageSlideVM(kind="event", title=event.title, body="",
                                image_url=None, qr_svg=qr, duration_seconds=default,
                                meta=f"{event.when_display}" + (f" · {event.location}" if event.location else "")))
  vms.sort(key=lambda v: v._start)   # sort by occurrence; keep the start on the VM for sorting
  return vms[:SIGNAGE_EVENT_CAP]
  ```
  `_next_occurrence(event, frm, to, now)` mirrors `hub/home.py:146` (self-contained here so signage doesn't import a private home.py helper): iterate `event.occurrences_in(frm, to)` and return the first `>= now`, else `None`. **Never** calls `_get_calendar_context` (the leak).
- `_holding_vm(default)` — the branded "Past Lives Makerspace" fallback slide (logo/wordmark), so an empty deck still shows something.
- `deck_hash(deck, config) -> str` — a stable hash of the alert state + each slide's identity/content + today's local date (so a scheduled slide dropping in or out changes the hash). The player renders it as `data-deck-hash`; the 300s poll uses it to skip a no-op swap (§6.1).

Deck order is deliberate and predictable for admins: **configured slides first (by `sort_order`), then generated event slides.** (Interleaving is a v2 nicety, §10.)

### 5.2 Views (`core/views.py` — public, undecorated, surface-guarded)

```python
def signage_player(request, zone_slug):
    if getattr(request, "surface", None) != "signage":
        raise Http404                                   # scope to the signage surface (see §6.0 collision note)
    zone = get_object_or_404(SlideshowZone, slug=zone_slug, is_enabled=True)   # unknown/disabled → 404
    config = SiteConfiguration.load()
    deck = build_deck(zone)
    ctx = {"zone": zone, "config": config, "deck": deck, "deck_hash": deck_hash(deck, config)}
    return render(request, "signage/player.html", ctx)  # NEVER branches on request.user

def signage_deck(request, zone_slug):                   # the 300s HTMX poll target
    if getattr(request, "surface", None) != "signage":
        raise Http404
    zone = get_object_or_404(SlideshowZone, slug=zone_slug, is_enabled=True)
    config = SiteConfiguration.load()
    deck = build_deck(zone)
    current = deck_hash(deck, config)
    if request.GET.get("h") == current:                 # nothing changed since the wall last rendered
        resp = HttpResponse(status=204)
        resp["HX-Reswap"] = "none"                       # HTMX skips the swap → no jump, no blank frame
        return resp
    return render(request, "signage/_deck.html",
                  {"zone": zone, "config": config, "deck": deck, "deck_hash": current})
```

- **No `@login_required`, no user branching.** The emergency-alert takeover is decided in the template from `config.signage_alert_active` (so it also swaps in on the next poll — see §6.1), keeping both views a straight render.
- The root-redirect / holding-page logic lives in the middleware helper (§6.0), because `/` is shared with the members home in the common urlconf.

### 5.3 Admin save views (`hub/views.py` — `@fog_admin_required`)

`admin_slideshow_zones_save` and `admin_slideshow_slides_save` — mirror `hub_guild_faq_save` / `hub_guild_links_save`: instantiate the formset (`prefix="zones"` / `prefix="slides"`, the latter with `request.FILES`), validate, `save()`, add a Django success message, redirect to `hub_admin_site_settings + "?tab=slideshow"`. Skip blank `+Add` rows the same way `_save_site_settings` skips blank `feeds` rows. No business logic beyond the formset save.

---

## 6. UI / UX ← completeness checklist applied per screen

Two screens: **(A)** the full-screen public **player** (read-only kiosk), and **(B)** the admin **Slideshow tab** (all the editing). Every state is described, both themes are addressed, and mobile/odd-aspect behavior is specified.

---

### 6.0 Surface plumbing (no UI, but the routing decisions the screens depend on)

**Settings — `plfog/settings.py`** (mirror the guilds block at `:96`–`:128`):

```python
SIGNAGE_HOSTS = [h.strip().lower() for h in os.environ.get("SIGNAGE_HOSTS", "slideshow.pastlives.space").split(",") if h.strip()]
SIGNAGE_BASE_URL = os.environ.get("SIGNAGE_BASE_URL", "https://slideshow.pastlives.space").rstrip("/")
SIGNAGE_ALLOWED_VIEW_NAMES: frozenset[str] = frozenset({"signage_player", "signage_deck"})
```

**Middleware — `core/middleware.py`** (new branch, placed alongside the guilds branch at `:59`):

```python
signage_hosts = set(getattr(settings, "SIGNAGE_HOSTS", []))
if host in signage_hosts:
    request.surface = "signage"
    short_circuit = self._handle_signage_surface(request)
    if short_circuit is not None:
        return short_circuit
    return self.get_response(request)
```

`_handle_signage_surface(request)`:
- `if request.path == "/":` query `SlideshowZone.objects.filter(is_enabled=True).order_by("sort_order").first()` (lazy import). If found → `HttpResponseRedirect(f"/{zone.slug}/")`. If none → `render(request, "signage/no_zones.html")` (200). *(A single indexed query on a rarely-hit root path — acceptable in middleware, and the only way to keep the dynamic root behavior on a shared urlconf.)*
- Otherwise: `resolve(request.path)` (catch `Resolver404` → `Http404`); allow only if `match.view_name in SIGNAGE_ALLOWED_VIEW_NAMES`, else `Http404`. (No `account_*` prefix pass — there is no login on this surface.)

**URL collision (important):** the player URL shape is a bare `/<zone-slug>/` at root, but `ROOT_URLCONF` is shared. Register the signage routes **last** in `plfog.urls` (append after every other include) so any real member route wins, and single-segment paths only fall through to `signage_player` when nothing else matched:

```python
# core/urls.py (appended last in plfog.urls)
path("<slug:zone_slug>/", views.signage_player, name="signage_player"),
path("<slug:zone_slug>/deck/", views.signage_deck, name="signage_deck"),
```

Two containment guards keep this from leaking onto other hosts: (1) the middleware allowlist gate only *permits* these names on the signage surface, and (2) both views raise `Http404` unless `request.surface == "signage"` (§5.2). So on the members host, `members.pastlives.space/woodshop/` resolves to `signage_player` but immediately 404s — it never renders the player off its surface.

**Context processor — `core/context_processors.py surface()`** (extend the existing dict at `:64`):

```python
is_signage = value == "signage"
# ...
"is_signage_surface": is_signage,
"SIGNAGE_BASE_URL": getattr(settings, "SIGNAGE_BASE_URL", "https://slideshow.pastlives.space"),
"signage_page_base": "signage/base.html",
```

(Member chrome is suppressed inherently — the player uses the standalone `signage/base.html`, which pulls in no sidebar/topbar/bell/welcome/PWA. `is_signage_surface` is available for any shared partial that needs to branch, and for the admin tab's setup-URL rendering.)

**Standalone base — `templates/signage/base.html`** (own `<!doctype html>`, extends nothing):
- `<html lang="en" data-theme="dark">` — forced dark; the dark token values apply.
- `<head>`: charset/viewport, `<title>`, Google Fonts (Lato/Inter), `signage.css`. **No** `<link rel="manifest">`, **no** favicon-PWA metas, **no** GA requirement.
- `<body class="pl-sign-body">`: `{% block content %}{% endblock %}`, then Alpine (and HTMX). **No** `navigator.serviceWorker.register`, no site-header, no BETA, no footer, no changelog modal.
- Blocks children fill: `title`, `extra_head`, `content`, `extra_js`.

---

### 6.1 The full-screen **Player** — `templates/signage/player.html` + `_deck.html`

The one screen a monitor shows. Extends `signage/base.html`; fills `content` with the Alpine-driven rotator.

- **Layout & container:** a single full-viewport stage. Slides are absolutely positioned, `width:100vw; height:100vh`, one visible at a time via an `.is-active` class (opacity/z-index fade — **not** `x-show` inline `display`, per Rule 12). Structure:
  ```html
  <div class="pl-sign-stage" x-data="signagePlayer()" x-init="init()" @htmx:after-swap.window="rescan()">
    <div id="pl-sign-deck" data-deck-hash="{{ deck_hash }}"
         hx-get="{% url 'signage_deck' zone.slug %}" hx-trigger="every 300s"
         hx-vals='js:{h: document.getElementById("pl-sign-deck").dataset.deckHash}'
         hx-swap="innerHTML">
      {% include "signage/_deck.html" %}
    </div>
  </div>
  ```
- **`_deck.html`** (shared by first paint **and** the 300s poll) branches once, at the top, on the alert:
  ```django
  {% if config.signage_alert_active %}
    <section class="pl-sign-slide pl-sign-alert is-active" data-duration="3600">
      <h1 class="pl-sign-alert__heading">{{ config.signage_alert_heading }}</h1>
      <p class="pl-sign-alert__message">{{ config.signage_alert_message|linebreaksbr }}</p>
    </section>
  {% else %}
    {% for s in deck %}
      <section class="pl-sign-slide{% if forloop.first %} is-active{% endif %}{% if s.kind == 'event' %} pl-sign-slide--event{% endif %}" data-duration="{{ s.duration_seconds }}">
        {% if s.image_url %}<img class="pl-sign-slide__img" src="{{ s.image_url }}" alt="">{% endif %}
        {% if s.title %}<h1 class="pl-sign-slide__title">{{ s.title }}</h1>{% endif %}
        {% if s.meta %}<p class="pl-sign-slide__meta">{{ s.meta }}</p>{% endif %}
        {% if s.body %}<div class="pl-sign-slide__body">{{ s.body|linebreaksbr }}</div>{% endif %}
        {% if s.qr_svg %}<div class="pl-sign-slide__qr">{{ s.qr_svg|safe }}</div>{% endif %}
      </section>
    {% endfor %}
  {% endif %}
  ```
- **Alpine `signagePlayer()`** (in `player.html`'s `extra_js`, ~35 lines, no library):
  - **A no-op poll skips the swap entirely — this is what stops the every-5-min jump-to-slide-0 and the blank flash.** The container carries `data-deck-hash="{{ deck_hash }}"` and the poll sends it back (the `hx-vals` above). `signage_deck` compares it to the freshly-built deck's hash; **unchanged → HTTP 204 + `HX-Reswap: none`**, so HTMX performs **no swap** — no DOM churn, no reset to slide 0, no blank frame, rotation continues untouched. Only a genuinely changed deck (a new event, an edited slide, the alert flipping) renders `_deck.html` (with a fresh `data-deck-hash`) and swaps.
  - `init()` → `rescan()` + `scheduleNightlyReload()`.
  - `rescan()` → read all `.pl-sign-slide` nodes + their `data-duration`; **resume at the current index clamped to the new length** (`index = Math.min(index, n - 1)`), *not* 0; immediately apply `.is-active` to that node (no blank frame); re-arm the timer. Called on load and on `@htmx:after-swap` — which now fires only when the deck actually changed.
  - `show(i)` → **cross-fade** `.is-active` onto node `i` (CSS opacity transition, so an old→new hand-off never flashes empty); `setTimeout(() => show((i + 1) % n), duration_i * 1000)`. A single-slide deck re-arms on itself.
  - `scheduleNightlyReload()` → ms to the next local **04:00** → `setTimeout(() => location.reload(), ms)` (picks up deploys; sheds accumulated DOM/timers — memory hygiene).
  - **First paint doesn't depend on JS:** `_deck.html` server-renders `is-active` on the **first** rotating slide (and on the alert section), so slide 1 is visible before Alpine boots or if it's slow — the wall is never blank waiting on JS.
  - Net: a wall left alone gets a cheap 204 every 5 min with zero visual change; it re-renders only on real content changes, resuming near where it was and cross-fading rather than blanking. Emergency-alert latency stays ≤ one poll interval (a config knob if instant takeover is ever wanted — §10).
- **States:**
  - **Empty** (no slides + no events, or `signage_show_events` off with no slides): `build_deck` guarantees a **branded holding slide** ("Past Lives Makerspace" wordmark/logo) — the screen is *never* blank.
  - **Loading:** server-rendered; slide 1 paints immediately. The 300s poll is silent (no spinner — it's an unattended wall).
  - **Error:** unknown/disabled zone → 404 (a bad monitor URL is caught at setup, not in production rotation). A slow/failed poll leaves the current deck rotating (HTMX failure doesn't clear the DOM).
  - **Success/feedback:** n/a — read-only, no mutations, no toasts.
  - **No dead ends:** it's a kiosk; the only "action" is a slide's QR code. There is no navigation to get stuck in.
- **Forms / lists / destructive:** none on this screen — checklist §1/§2/§3 are **N/A** (stated explicitly; the player has zero controls).
- **Dark + light:** the surface is **always dark/full-bleed** (forced `data-theme="dark"`). Colors still come from a **token block** — `signage.css` declares its own `:root` `--sign-*` tokens (bg, text, muted, gold accent, navy, alert-bg/text) with the Obsidian/gold values from FRONTEND.md, self-contained the way `guild-flyer.css` carries the print palette (this standalone base doesn't load `hub.css`, so the tokens are declared locally — a deliberate, flagged duplication, not hardcoded hex sprinkled through the markup). No form controls exist here, so Rule 13's white-box trap can't occur.
- **Mobile / odd aspect ratio (monitors vary):** all type uses **`clamp()` + viewport units** (e.g. `font-size: clamp(2rem, 6vw, 6rem)`) so a headline scales from a 1080p TV to a portrait tablet; the stage is `100vw × 100vh` with `overflow: hidden` (never a horizontal scrollbar); images use `object-fit: cover` (full-bleed) or `contain` (flyers) within the stage; the QR is sized in `vmin` so it stays scannable at any ratio. Spacing on the 8px grid.

---

### 6.2 The admin **Slideshow tab** — `templates/hub/admin/site_settings.html`

Everything an admin needs, following the exact 6-step "add a tab" recipe. Admin-only via `@fog_admin_required` (the whole Site Settings page).

**The 6 wiring steps:**
1. **Model fields** — the seven `SiteConfiguration` signage fields (§4.4).
2. **Form** — add each of the seven field names to `SiteSettingsForm.Meta.fields` (`hub/forms.py:530`); add `widgets["signage_alert_message"] = forms.Textarea(attrs={"rows": 3})`. Once in `fields`, they save automatically via `_save_site_settings`.
3. **General-tab exclusion** — extend the `{% if field.name != '…' and … %}` chain (`site_settings.html:141`) with all seven names, so they don't double-render on the General tab.
4. **Tab button + TWO tab sections (critical — do not merge into one block).** Add the button (mirroring `:96`):
   ```html
   <button type="button" @click="tab = 'slideshow'" :class="{ 'vote-tab--active': tab === 'slideshow' }" class="vote-tab">Slideshow</button>
   ```
   The tab's content is split across **two** `x-show="tab === 'slideshow'"` blocks, because Part 1 must live *inside* the shared `#site-settings-form` (opens `:134`, **closes `</form>` at `:401`**) while Parts 2 & 3 are their **own** `<form>`s and must not nest inside it:
   - **Block A — Part 1 (global + emergency-alert fields), placed BEFORE the shared form's `</form>` at `:401`**, beside the other in-form tab sections (general / calendar / features / discord). It contributes fields to the shared form and is saved by its existing "Save settings" button (`:398`). Layout via a class (`pl-slideshow-tab-a`) — no inline `display` (Rule 12): `<div x-show="tab === 'slideshow'" x-cloak class="pl-slideshow-tab-a"> …Part 1… </div>`.
   - **Block B — Parts 2 & 3 (zones + slides editors), placed AFTER `:401`**, beside the Announcements block (`:404`) — i.e. *outside* the shared form. Each editor is its own `<form>` (Part 3 multipart). `<div x-show="tab === 'slideshow'" x-cloak class="pl-slideshow-tab-b"> …Parts 2 & 3… </div>`.

   This mirrors `guild_edit.html` exactly: the FAQ/Links forms live in a **separate** `x-show` section *outside* the page's main form (`guild_edit.html:345` comment: "each its own form, outside the main form — you can't nest forms"). **A single merged block is the orphaned-Save defect:** placed before `:401` it nests the zones/slides `<form>`s inside `#site-settings-form` → invalid HTML → the browser closes the outer form early → "Save zones"/"Save slides" submit **nothing** (the exact nested-form bug `guild_edit.html` warns about); placed after `:401` it drops Part 1's fields *outside* the settings form → "Save settings" never persists them. Two blocks are mandatory.
5. **View allow-set** — add `"slideshow"` to the `active_tab` allow-set (`hub/views.py:4193`), else `?tab=slideshow` silently falls back to General after save.
6. **Formset handlers** — two dedicated `@fog_admin_required` POST views (§5.3) + URL names, mirroring `hub_guild_faq_save`/`hub_guild_links_save`.

**Tab layout — TWO `x-show` blocks, three logical parts** (Block A = Part 1, *inside* the shared `#site-settings-form`, before its `</form>` at `:401`; Block B = Parts 2 & 3, a sibling of the Announcements form, *after* `:401`; see wiring step 4). This matches how Announcements is its own form sibling to `#site-settings-form`.

**Part 1 (Block A) — Global settings + emergency takeover (inside the shared `#site-settings-form`).**
These are plain `SiteConfiguration` fields, so they ride the existing shared form and its **"Save settings"** button (`:398`). Rendered on the tab via `form_field.html`:
- `signage_default_slide_seconds`, `signage_event_days_ahead` (number inputs), `signage_show_events`, `signage_event_qr` (toggles via `form_field.html`/`toggle.html`).
- **Emergency takeover** grouped in its own `pl-slideshow-alert-controls` card with a loud label: the `signage_alert_active` toggle carries `field_hint="When on, every screen shows ONLY this alert until you turn it off."`; `signage_alert_heading` (text) and `signage_alert_message` (Textarea → wrapped in `.pl-form-group` by `form_field.html`, Rule 13). It's a reversible toggle, not a destructive delete, so no confirm modal — the hint is the guardrail.
- Save → full-page POST → Django message ("Settings saved") on the redirect back to `?tab=slideshow`.

**Part 2 (Block B) — Zones editor (a separate `<form>`, its own save view).**
Mirrors the Links editor exactly (`guild_edit.html:415`). `SlideshowZoneFormSet = modelformset_factory(SlideshowZone, form=SlideshowZoneForm, extra=0, can_delete=True)`, `prefix="zones"`, posting to `hub_admin_slideshow_zones_save`.
- Each row: `name`, `slug` (hint "used in the screen URL"), `is_enabled` (toggle), `sort_order` — each via `form_field.html`; hidden `{{ f.id }}`.
- **Per-row Delete:** hidden `{{ f.DELETE }}` + a real `pl-btn pl-btn--danger pl-btn--sm` button, `margin-top:0.75rem` (clears the field above it), `onclick` flips DELETE and `requestSubmit()` (verbatim FAQ/Links pattern). **Never a toggle.**
- **"+ Add a zone":** a `hub-btn hub-btn--sm` button with `margin-top:1rem` that clones a `<template id="zone-empty-template">` of `empty_form`, swaps `__prefix__`, bumps `id_zones-TOTAL_FORMS` (verbatim clone JS). Cloned rows get a DOM-remove "Remove" button (`margin-top:0.75rem`).
- **Save:** `pl-btn pl-btn--primary` "Save zones", with `margin-top:1rem` so it clears the last row / the +Add button above it.
- **Setup helpers (per saved zone)** — the point of the whole tab for staff pointing a monitor. Below each **saved** row (they reflect the *saved* slug, so they appear only once the row exists), a `pl-slideshow-zone__setup` block (spacing on the 8px grid: `margin-top:1rem`, internal `gap:0.75rem`) showing:
  - the full player URL as selectable text: `{{ SIGNAGE_BASE_URL }}/{{ zone.slug }}/`, with a one-line note "Save first — the URL and QR use the *saved* slug." (so an unsaved slug edit doesn't look broken),
  - a **Copy** button styled `hub-btn hub-btn--sm` (theme parity with the rest of the tab): `@click="navigator.clipboard.writeText('{{ zone.player_url }}'); $dispatch('show-toast', {message:'URL copied', type:'info'})"`,
  - a small inline QR: `{{ zone.qr_svg|safe }}` (so staff scan it straight off the screen),
  - a **Preview** link → `{{ zone.player_url }}` (`target="_blank"`), styled `hub-btn hub-btn--sm`,
  - a muted caveat: "Preview and the QR point at `slideshow.pastlives.space` — they won't resolve until the signage host goes live (§10 go-live infra)." (so the block doesn't read as broken pre-launch).

**Part 3 (Block B) — Slides editor (a separate multipart `<form>`, its own save view).**
Mirrors the FAQ editor (`guild_edit.html:347`, which already has `enctype="multipart/form-data"` + a file field). `SlideshowSlideFormSet = modelformset_factory(SlideshowSlide, form=SlideshowSlideForm, extra=0, can_delete=True)`, `prefix="slides"`, `<form … enctype="multipart/form-data">` posting to `hub_admin_slideshow_slides_save`.
- **Kind-switching row:** each row is `x-data="{ kind: '{{ f.kind.value|default:'custom' }}' }"`. The `kind` `<select>` binds via a widget attr (`SlideshowSlideForm` sets `widgets["kind"] = forms.Select(attrs={"x-model": "kind"})`). Sub-groups reveal by kind — with layout in a **class**, never inline `display` (Rule 12):
  - `<div x-show="kind === 'custom'" class="pl-slide-fields">` → `title`, `body` (Textarea, wrapped by `form_field.html` in `.pl-form-group`), `image` (file), `link_url`, `show_qr` (toggle).
  - `<div x-show="kind === 'announcement'" class="pl-slide-fields">` → `announcement` picker.
  - Always visible (both kinds): `zone` (dropdown; blank = all screens — the field's empty label reads "All screens"), `duration_seconds`, `starts_on`/`ends_on`, `is_enabled` (toggle), `sort_order`.
- **Announcement picker:** `announcement = forms.ModelChoiceField(queryset=GuildAnnouncement.objects.published(), required=False)`. Because **only an admin reaches this tab**, this is the privacy-safe, admin-curated opt-in the design requires — guild leads never set a slide. Form `clean()` requires `announcement` when `kind == announcement`, and requires a `title` or `image` when `kind == custom` (friendly error: "Pick an announcement" / "Give the slide a title or an image").
- **Native date inputs (`starts_on`/`ends_on`) — no bespoke CSS needed.** They render through `form_field.html` → `.pl-form-group`, and `components.css` **already** themes `.pl-form-group input[type="date"]` for both modes via `color-scheme: dark` (`components.css:508`) + a `[data-theme="light"]` override (`:972`) — verified. So do **not** add a `filter: invert` rule (it would fight `color-scheme`) and do **not** put any date CSS in `signage.css` (which the hub never loads). The **only** addition is opening the picker from the whole control, via widget attrs: `SlideshowSlideForm.widgets["starts_on"|"ends_on"] = forms.DateInput(attrs={"type": "date", "@click": "try { $event.currentTarget.showPicker() } catch (e) {}"})`. (These render on the dual-theme members hub — verify both themes there, even though the player is always dark.)
- **Image field (Rule 10 — the editor UI, not just the backend stack):**
  - On a **saved** slide row that has an image, show a **thumbnail preview** of the current image above the file input (mirroring how the FAQ editor surfaces its saved document affordance), so the admin can see what's set.
  - A hint under the input: "Choosing a new file replaces the current image. If the form bounces on a validation error, re-attach the file — it isn't kept across a failed submit."
  - A **themed remove control.** Django's default `ClearableFileInput` renders a raw, un-themed "Clear" checkbox — a stray bare checkbox on the dark hub. Either (a) set `SlideshowSlideForm.widgets["image"]` to a widget that suppresses the default clear checkbox and add a real `pl-btn pl-btn--danger pl-btn--sm` "Remove image" button (`margin-top:0.75rem`) that submits with the image cleared, or (b) render the clear checkbox hidden and drive it from that button. **Removing the image must NOT require deleting the slide.**
  - The file input itself themes via `.pl-form-group` (from `form_field.html`); the R2 / normalize / orphan-cleanup stack runs in `save()`.
- **Per-row Delete / "+ Add a slide" / Save (explicit spacing — clashing margins are the canonical hygiene bug):** the per-row Delete is `pl-btn pl-btn--danger pl-btn--sm` with `margin-top:0.75rem`; "+ Add a slide" is `hub-btn hub-btn--sm` with `margin-top:1rem` (clones `<template id="slide-empty-template">` of `empty_form`); "Save slides" is `pl-btn pl-btn--primary` with `margin-top:1rem`. Every control clears the element above it.
- **States:**
  - **Empty:** no zones → a `pl-slideshow-empty` note "No screens yet. Add your first zone to get a URL for a monitor." with the +Add button; no slides → "No slides yet. Add a custom slide or pull in a published announcement." Never a bare region.
  - **Loading:** full-page POST → global loading bar.
  - **Error:** formset/`clean()` errors render inline under the offending row (`form_field.html` shows `.pl-field-error`); a friendly message, not a 500.
  - **Success:** Django message toast "Zones saved" / "Slides saved" on redirect back to `?tab=slideshow`.
  - **No dead ends:** each form has a visible Save; each row an Add/Delete; the tab always returns to itself.
- **Dark + light:** every control is scoped by `form_field.html` (`.pl-form-group` input tokens) or rendered as a `toggle.html` switch — **no inline `background`/`color`** on any `<select>`/`<input>`/`<textarea>` (Rule 13), and `select option { background; color }` covered by the shared component CSS. Verify both themes. The zone-QR and copy-button block uses `--hub-*` tokens. **All bespoke `pl-slideshow-*` editor rules live in `hub.css`** — the tab renders under `hub/base.html`, which loads `hub.css` + `components.css`; it does **not** load `signage.css` (player-only), so any editor CSS placed there would silently no-op.
- **Mobile:** the tab and its formset rows reflow to a single column (the Site Settings page is already responsive); the zone-setup URL wraps and the QR shrinks; tap targets are real buttons on the 8px grid.

---

## 7. Notifications / emails / activity

**None.** The signage feature sends no email and no in-app/push notification — it's a passive display plus an admin config screen. No `emit()` calls, no new `core/triggers.py` entries, no email templates.

*(Optional, deferred — §10: log a `SiteActivity` entry when the emergency alert is toggled on/off, for an audit trail. Not required for v1 and not built now.)*

---

## 8. Build order (phased; each phase ships green — full suite + `ruff` + `mypy`)

1. **Surface plumbing + standalone base + holding player.** `SIGNAGE_HOSTS`/`SIGNAGE_BASE_URL`/`SIGNAGE_ALLOWED_VIEW_NAMES` settings; `SurfaceMiddleware` signage branch + `_handle_signage_surface` (allowlist gate; root logic that will call the zone model — stubbed to the holding page until Phase 2); `surface()` flags; `templates/signage/base.html` (standalone, no sw.js/manifest/BETA), `no_zones.html`, a `player.html` that renders the **branded holding slide** with no DB; `signage.css`; `core/views.py` `signage_player`/`signage_deck` (surface-guarded) + routes appended last. Specs: middleware routing/gate/`Resolver404`, context flags, surface-guard 404. *(Green; visible = a dark holding screen + a 404 for anything off-allowlist.)*
2. **Models + migrations + querysets + real zone routing.** `SlideshowZone`, `SlideshowSlide`, managers (`visible()`, `for_zone()`), `player_url`/`qr_svg`; `SiteConfiguration` seven fields; migrations (membership CreateModel + core AddField); wire root → first-enabled-zone redirect / holding page; `signage_player` 404s unknown/disabled zone. Specs: field defaults, `visible()` two-sided window + announcement-live gate, `for_zone()` null-OR-match, ordering, zone routing/404, root redirect vs. holding page.
3. **Auto event slides rendering + the live player.** `membership/signage.py` `build_deck` + `_event_slides` (**site-wide-only** filter, occurrence expansion, horizon/cap, duration fallback) + holding fallback + `deck_hash`; `player.html`/`_deck.html` real deck (first rotating slide server-rendered `is-active`) + the Alpine `signagePlayer()` rotation (resume-at-index, cross-fade, no jump-to-0) + nightly reload + the 300s HTMX poll with **no-op-skips-swap** (`?h=` hash matches → 204 + `HX-Reswap: none`). Specs: `build_deck` **excludes guild events** / includes site-wide (privacy), respects horizon/cap, duration fallback; **auth-parity** (authed admin vs. anon → identical content); does not call `_get_calendar_context`; poll with a matching hash → 204/no-swap, changed → renders a fresh `_deck.html`.
4. **Admin tab — global settings + emergency takeover.** `SiteConfiguration` fields → `SiteSettingsForm.Meta.fields` (+ Textarea widget); General-tab exclusion-chain entries; the Slideshow tab button + **Block A** (Part 1) *inside* the shared `#site-settings-form` (before its `</form>` at `:401`); `"slideshow"` in the `active_tab` allow-set; alert-takeover rendering in `_deck.html`. Specs: fields save via the shared form; `signage_alert_active=True` → player shows **only** the alert; tab renders in both themes.
5. **Zones + slides editors with image upload (Block B).** `SlideshowZoneForm`/`FormSet`, `SlideshowSlideForm`/`FormSet`; two dedicated save views + URLs; **Block B** (Parts 2 & 3) placed *after* the shared form closes (`:401`), each its own `<form>` (Part 3 multipart); editor UI (Add/Delete/Save with explicit margins, image upload stack **+ thumbnail preview + themed remove-image control + re-attach hint**, kind-switching rows, date inputs via `.pl-form-group` + `showPicker()`, zone setup URL/QR/copy/preview); all `pl-slideshow-*` editor CSS in **hub.css**. Specs: formset add/delete/save (rendered-HTML: Delete is a `<button>`, not a toggle; the zones/slides `<form>`s are **not** nested inside `#site-settings-form`), image upload runs the normalize/orphan stack, remove-image clears the field without deleting the slide, kind-switch shows/hides the right fields, `@fog_admin_required` gates the save views.
6. **QR + announcement-backed + scheduled slides (polish + wire-through).** `show_qr` → inline segno SVG on custom slides; announcement slides pull the linked announcement's **live** title/body and auto-hide when it's unpublished/expired (via `visible()`); `starts_on`/`ends_on` honored end-to-end; `signage_event_qr` adds the calendar QR to event slides. Specs: announcement slide hidden when its announcement is unpublished/expired and shown when live; QR SVG present when `show_qr`; scheduled window boundaries.
7. **Housekeeping (last).** Bump `plfog/version.py` `VERSION` + the member-facing CHANGELOG entry (draft in §10). Infra ticket (separate, §10): Render custom domain + DNS + `SIGNAGE_HOSTS` + `DJANGO_ALLOWED_HOSTS`.

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy, run in the `plfog-web` Docker image, ≥98% coverage gate (`--no-cov` for subsets while iterating). Exercise the surface with `HTTP_HOST=slideshow.pastlives.space` + a `SIGNAGE_HOSTS`/`SIGNAGE_BASE_URL` override so the middleware branch actually runs. Assert against **rendered HTML** for privacy/gating and for the formset controls (per the nested-form lesson — HTML-structure bugs escape context-only assertions). New factories: `SlideshowZoneFactory`, `SlideshowSlideFactory`.

- **`core/spec/middleware/surface_middleware_spec.py` — `describe_signage_surface`:** host in `SIGNAGE_HOSTS` → `request.surface == "signage"`; `/` with an enabled zone → 302 to `/<slug>/` (the first by `sort_order`); `/` with **zero** zones → 200 holding page (`no_zones.html`, no redirect loop); allowlisted names (`signage_player`, `signage_deck`) pass; **denied** names 404 (e.g. `hub_home`, `hub_guild_edit`, `/settings/…`, `/billing/…`); `Resolver404` path → 404.
- **`core/spec/context_processors_spec.py`:** `surface()` on a signage request → `is_signage_surface` True, `signage_page_base == "signage/base.html"`, `SIGNAGE_BASE_URL` present; members/public/guilds requests → `is_signage_surface` False.
- **`core/spec/views/signage_player_spec.py` (the critical privacy suite):**
  - **Auth-parity (the sharpest test):** seed a `MembershipPlan` + an admin member; enabled zone with a few slides. GET `/<slug>/` as an **anonymous** client, then log the admin in and GET the same URL on the signage host. Assert the two response bodies are **identical** (or, minimally: no member `display_name`/email/PII, no "Admin View"/persona chrome, no per-user CSRF-bearing markup differs). Proves the shared `.pastlives.space` session cookie leaks nothing.
  - **Surface guard:** `signage_player` on the **members** host (`request.surface == "members"`) → 404, even for a valid enabled zone slug.
  - **Zone routing/404:** enabled zone → 200; **disabled** zone → 404; unknown slug → 404.
  - **Empty/holding:** a zone with no visible slides and `signage_show_events` off → body still contains the **branded holding slide** (never blank).
  - **Emergency takeover:** `signage_alert_active=True` → body contains the alert heading/message and **omits** the rotating deck markup (rotation `.pl-sign-slide` for real slides absent); each rendered slide otherwise carries a `data-duration`.
  - **No sw.js / no manifest / no BETA:** the signage base output contains no `serviceWorker.register`, no `rel="manifest"`, no BETA header.
  - **First paint + no-op poll:** `_deck.html` renders `is-active` on the **first** rotating slide (assert present, so slide 1 shows pre-JS); GET `signage_deck` with `?h=<current deck_hash>` → **204** with `HX-Reswap: none` (no-op poll — no swap), and with a stale/absent `h` → **200** rendering `_deck.html` carrying a fresh `data-deck-hash`.
- **`membership/spec/signage_spec.py` — `describe_build_deck`:**
  - **Site-wide-only event filter (privacy):** create a **guild** event (guild set) and a **site-wide** event (guild=None), both published+upcoming; `build_deck(zone)` includes the site-wide event and **excludes** the guild event. (Guards the leak.)
  - Respects `signage_event_days_ahead` horizon and `SIGNAGE_EVENT_CAP`; `signage_show_events=False` → no event slides.
  - Duration fallback: a slide with `duration_seconds=None` → uses `signage_default_slide_seconds`.
  - Deck order: configured slides (by `sort_order`) precede generated event slides.
  - Announcement slide pulls the linked announcement's **live** title/body; a custom slide with `show_qr` + `link_url` → a `qr_svg` present.
- **`membership/spec/models/slideshow_spec.py`:**
  - `visible()`: `starts_on` in the future → excluded; `ends_on` in the past → excluded; both null + enabled → included; `is_enabled=False` → excluded; **announcement**-kind slide whose announcement is unpublished OR expired → excluded; announcement published+active → included. (tz: `timezone.localdate()` vs. `DateField`.)
  - `for_zone(zone)`: a `zone=None` slide appears for any zone; a `zone=A` slide appears for A, not B.
  - `SlideshowZone.player_url == SIGNAGE_BASE_URL + "/<slug>/"`; `qr_svg()` returns non-empty `<svg …>` encoding `player_url`; `__str__`s.
- **`hub/spec/forms/site_settings_form_spec.py`:** the seven `signage_*` fields are in `SiteSettingsForm.Meta.fields` and round-trip a save onto the singleton; `signage_alert_message` uses a Textarea widget.
- **`hub/spec/views/admin_slideshow_spec.py`:**
  - Tab renders under `@fog_admin_required`; anon/non-admin → redirect/403; `?tab=slideshow` preserved after each save.
  - **Nested-form guard (rendered HTML):** the zones and slides `<form>`s are **not** nested inside `#site-settings-form` — Block A's signage global/alert inputs live *within* the settings form; Block B's editor `<form>`s come *after* its `</form>`. Assert `#site-settings-form` contains `id="id_signage_alert_active"` (etc.) but **no** `action="…slideshow_zones_save…"` / `…slides_save…` form tag between `:134` and `:401`.
  - **Formset add/delete (rendered-HTML):** the zones and slides editors render an `empty_form` `<template>`, a "+ Add" button, and a per-row Delete that is a real `pl-btn pl-btn--danger pl-btn--sm` **`<button>`** (assert it is a button, **not** an `<input type="checkbox">` toggle) with `margin-top:0.75rem` and a hidden `{{ form.DELETE }}`.
  - POST with a DELETE-flagged row removes it and preserves the others; POST with a cloned new row creates it; a blank +Add row is skipped (no validation error).
  - Image upload: POST a slide with an image → the `save()` normalize/orphan stack runs and the file persists. **Remove-image:** POST with the remove control set clears `image` and the slide row still exists (not deleted).
  - Kind-switch: the rendered row exposes both `x-show="kind === 'custom'"` and `x-show="kind === 'announcement'"` groups with layout in a class (no inline `display`); the announcement picker's queryset is `published()` only.
- **Gotchas:** member-gated auth-parity test must seed a `MembershipPlan` before login (else the signal skips `Member` creation); date-window tests are tz-sensitive (`localdate()`); always pass `HTTP_HOST=slideshow.pastlives.space` + the `SIGNAGE_*` overrides so hosts and `player_url`/QR resolve deterministically; assert the event-slide sort with events at distinct occurrence times.

---

## 10. Open / deferred

1. **Go-live infra (separate ticket).** DNS `slideshow.pastlives.space` → Render custom domain; add the host to **`DJANGO_ALLOWED_HOSTS`**; set **`SIGNAGE_HOSTS=slideshow.pastlives.space`** (and optionally `SIGNAGE_BASE_URL`). **Do NOT** add it to `PUBLIC_HOSTS`/`GUILDS_HOSTS`. **No `CSRF_TRUSTED_ORIGINS` entry** — the player never POSTs, and the admin config lives on the already-trusted members host. Until DNS + `SIGNAGE_HOSTS` land, no request reaches the signage branch (safe empty hook).
2. **Multi-zone per slide (M2M) — v2.** Slide→zone is a single FK (`NULL` = all). A slide that should show on *some but not all* zones needs the row duplicated today. An M2M (`zones = ManyToManyField`, empty = all) is the v2 upgrade; the FK migrates forward cleanly.
3. **Event-slide QR target (decide at build time).** `signage_event_qr` encodes `event.absolute_url`, which currently points at `hub_community_calendar` on the **members host** (login-gated) — a wall scanner who isn't a member hits a login wall. That's why the toggle **defaults off**. A truly public calendar surface (or pointing the QR at the public guilds/book pages) is a follow-up decision, not built now.
4. **Weather widget — dropped.** Explicitly out of scope (no third-party weather integration on the kiosk).
5. **Per-screen analytics — deferred.** No "which slide was shown / for how long" telemetry in v1 (it's a passive display; adding beacons would undercut the read-only simplicity).
6. **Interleaving events among custom slides — deferred.** v1 deck is configured slides then event slides (predictable). A weighted/interleaved rotation is a later nicety.
7. **Emergency-alert audit log — deferred.** Optional `SiteActivity` entry when the alert is toggled (§7). Not required for v1.
8. **Poll interval / alert latency — config knob, deferred.** The 300s poll means an alert can take up to ~5 min to appear without touching the screen. Fine for v1; a shorter interval (or a push mechanism) is a later option if staff want instant takeover.
9. **Release number — decide at build time.** A brand-new public subdomain reads as a minor feature; likely `0.21.x`/`0.22.0` on the matching release branch. One curated, member-facing CHANGELOG entry (draft below), stamped at the bumped `VERSION` in the housekeeping phase.

**Draft CHANGELOG entry (net-new member-facing feature → new grouped entry at the top, stamped at the bumped VERSION):**

> **Title:** "Screens around the space now show a live slideshow"
> - The monitors around the makerspace now run a slow, full-screen slideshow of what's coming up — upcoming events, handy tips about the space, flyers, and the occasional guild announcement — updating on their own throughout the day.
> - Admins can build and reorder slides, set up a screen for each area (woodshop, lobby, classroom), and even put up a single "building closed"-style alert on every screen at once, all from the new Slideshow tab in Site Settings.
> - Some slides show a QR code you can scan to jump straight to the details on your phone.

---

Spec only — do not build until approved.
