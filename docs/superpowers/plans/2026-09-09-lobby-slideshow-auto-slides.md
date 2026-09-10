# Lobby Slideshow: its own admin tile, and slides that build themselves

**Status:** ready to build (fog-quick-feature pipeline; passed adversarial UX/verification review)
**Date:** 2026-09-09
**Surface:** FOG hub admin (`/tools/`, a new `/manage/slideshow/`, and Site Settings losing a tab) +
the signage kiosk surface (`slideshow.pastlives.space`, `membership/signage.py`,
`static/css/signage.css`).
**Related:** `2026-07-10-signage-slideshow.md` (built the player, the zones/slides models, and the
Slideshow tab this spec moves), `2026-09-06-admin-tools-alphabetical.md` (the tile grid this spec
adds a card to), `2026-09-09-host-a-workshop-page.md` (the `ClassSettings.teach_page_*` copy the
Host a Workshop slide reuses).

---

## 1. Summary

Two asks, one release.

1. **The Slideshow admin moves out of Site Settings** into its own Admin Tools tile and its own page
   at `/manage/slideshow/`. It is the only Site Settings tab that manages its own models through two
   sibling forms, and it has outgrown a tab.
2. **The lobby screen fills itself in.** Today an admin hand-builds every slide except the
   upcoming-events ones. This adds six more self-building slide blocks — this week's classes, the
   guilds, a month calendar, the funding vote, the member directory, and Host a Workshop — each with
   its own on/off switch, each carrying a QR a passer-by can scan.

The kiosk requirement ("the monitor refreshes once a week and must need nothing else") is **already
met by the player and needs no new code**: the deck is built per request from live data, the player
re-polls every 300 seconds and hard-reloads at 04:00 local. A screen pointed at its zone URL once is
correct forever. There is **no new cron and no weekly job** in this spec — see §7.

### Locked decisions

Decided from the codebase and the request; none of these need the user before building.

| # | Decision | Choice |
|---|---|---|
| a | Where the admin lives | A new full page, `/manage/slideshow/` (`hub_admin_slideshow`), reached from a new Admin Tools tile. Not a tab anywhere. |
| b | Old links | `?tab=slideshow` on Site Settings **302s** to the new page. This exists **only** for bookmarks and `tests/e2e/screenshots_spec.py`. Both editor save views are retargeted directly at the new page (§4.1) and must not lean on the redirect. |
| c | Which settings move | The three `signage_*` fields leave `SiteSettingsForm` for a new `SlideshowSettingsForm`. They are not duplicated. |
| d | Auto-slide switches | Six new `BooleanField`s on `SiteConfiguration`, **all `default=True`**. The user is putting this live and wants it populated on arrival; an admin switches off what they don't want. |
| e | Classes window | Fixed 7 days ("this week"), a module constant. No admin field — the ask is "the week", and the events block already owns the configurable look-ahead. |
| f | Guilds | **One** slide listing every currently visible guild, not one slide per guild. |
| g | Calendar sources | The current calendar month, dotted from **public sources only** (§5.3.3). Never `hub.calendar_entries.community_event_entries`, which returns every guild's private meetings. |
| h | Host a Workshop copy | Reuses `ClassSettings.teach_page_cta_title` / `teach_page_cta_line` (already admin-editable, already short: *"Got Something to Share?"* / *"Tell us what you have in mind and an admin will take it from there."*), falling back to `teach_page_title` / `teach_page_lead`. No new copy fields. |
| i | Ordering | Generated blocks run in a fixed order after the admin's own slides. No per-block sort control (YAGNI). |
| j | Version | `1.49.0`, one new member-facing `CHANGELOG` entry. |
| k | Three carried-over fixes | Moving this page surfaced three defects in the moved code. All three are fixed here because the page is about to carry more traffic, and each is a few lines: the editors' work-losing error state (§4.6), the unguarded cascade delete (§4.4), and the UTC cycle label (§5.3.4). |

---

## 2. What already exists (reuse, do not reinvent)

All verified in the tree on 2026-09-09, and re-verified by review.

| Need | Existing thing | Location |
|---|---|---|
| The deck builder | `build_deck(zone)` → `list[SignageSlideVM]`, plus `deck_hash` | `membership/signage.py:47,68` |
| The slide view-model | `SignageSlideVM` frozen dataclass | `membership/signage.py:33` |
| Generated-slide precedent | `_event_slides` — queries directly, caps, QRs, sorts by occurrence | `membership/signage.py:140` |
| Room-legible URL caption | `_friendly_url` | `membership/signage.py:205` |
| Scalable QR | `membership.qr.qr_svg` | `membership/qr.py:20` |
| Slide rendering | `signage/_deck.html` — one `<section class="pl-sign-slide">` per VM | `templates/signage/_deck.html` |
| Player poll + nightly reload | `hx-trigger="every 300s"` (`:17`) + `scheduleNightlyReload()` 04:00 (`:98`) | `templates/signage/player.html` |
| Kiosk styles | `.pl-sign-slide*`, `clamp()`-scaled, dark-only | `static/css/signage.css` |
| Zones/slides editors (to MOVE) | Block A (timing) + Block B (two sibling `<form>`s) | `templates/hub/admin/site_settings.html:729-1050` |
| The editors' delegated JS | image drop/preview **1149-1220**; reorder **1221-1302** | `templates/hub/admin/site_settings.html` |
| The Run-now spinner (does **NOT** move) | bound to `#site-settings-form` | `templates/hub/admin/site_settings.html:1099-1148` |
| The editors' formsets (`extra=0`) | `SlideshowZoneFormSet`, `SlideshowSlideFormSet` | `hub/forms.py:1113,1211` |
| The editors' save views | `admin_slideshow_zones_save`, `admin_slideshow_slides_save` | `hub/views.py:7285,7307` |
| Their URLs (paths **unchanged**) | `manage/site-settings/slideshow/{zones,slides}/save/` | `hub/urls.py:663,668` |
| Confirm modal, JS mode | `confirm_js` runs inline JS then closes | `templates/components/confirm_modal.html:53-56,95-98` |
| Admin tile grid (at `/tools/`) | `.pl-tools-grid` / `.pl-tool-card`, alphabetical | `templates/hub/admin_tools.html`; view `hub/views.py:3814`; url `hub/urls.py:221` |
| Standalone admin page pattern | `voting_settings` | `hub/views.py:6021`; `templates/hub/admin/voting_settings.html` |
| Public class sessions | `ClassSessionQuerySet.upcoming_public()` (published + non-private + demo gate + **future only**) | `classes/models.py:2606`; manager `:2647` |
| A class's public page URL | `ClassOffering.public_url` | `classes/models.py:961` |
| Visible guilds | `Guild.objects.visible()` | `membership/models.py:2006` |
| Site-wide published events | `CommunityEvent.objects.published().site_wide()` + `occurrences_in` | `membership/models.py:5102,5091,5481` |
| Voting cycle strings | `get_cycle_context()` | `membership/cycle.py:11` |
| Host a Workshop copy | `ClassSettings.load()` + the CTA fields | `classes/models.py:3888,3834,3843` |
| Host URLs | `MEMBER_BASE_URL`, `GUILDS_BASE_URL`, `SIGNAGE_BASE_URL` | `plfog/settings.py:81,132,165` |
| `SIGNAGE_BASE_URL` in templates | already global via the `surface` context processor | `core/context_processors.py:117` |

All five target URL names resolve and share one urlconf, so `reverse()` from the signage host is
safe: `hub_guild_directory` (`hub/urls.py:77`), `hub_community_calendar` (`:545`),
`hub_guild_voting` (`:72`), `hub_member_directory` (`:75`), `classes:teach_overview`
(`classes/urls.py:18`). `hub_guild_directory` is in `GUILDS_ALLOWED_VIEW_NAMES`
(`plfog/settings.py:139-150`), so the guilds QR opens with no login wall.

---

## 3. Where the code lives

```
core/
  models.py                     # +6 signage_show_* BooleanFields on SiteConfiguration
  migrations/00XX_...py         # additive, no data migration
classes/
  models.py                     # ClassSessionQuerySet: extract public(), add public_between()
membership/
  signage.py                    # +6 generators, +SignageCalendarDay, VM gains calendar_days,
                                #   build_deck order + config pass-down, deck_hash covers the grid
  cycle.py                      # get_cycle_context(): local time, not UTC
hub/
  forms.py                      # +SlideshowSettingsForm; SiteSettingsForm sheds 3 signage_* fields
  urls.py                       # +manage/slideshow/  (the two save paths are UNCHANGED)
  views.py                      # +hub_admin_slideshow + _render_slideshow_page; save views re-render
                                #   on invalid; admin_site_settings redirects ?tab=slideshow;
                                #   allowed_tabs loses "slideshow"; hub_admin_tools +tool_slideshow
templates/hub/admin/
  slideshow.html                # NEW
  site_settings.html            # tab button + Block A + Block B + their two JS blocks removed
  admin_tools.html              # +Slideshow tile (alphabetically last)
templates/signage/
  _deck.html                    # kind class for every slide + month-grid branch
static/css/
  signage.css                   # +slide variants, +month grid
  hub.css                       # .pl-slideshow-tab-a/-tab-b → .pl-slideshow-page
tests/                          # see §8
plfog/version.py                # VERSION 1.49.0 + one CHANGELOG entry
```

---

## 4. Part A — the move

### 4.1 The new page

`hub/urls.py`, beside the other `manage/` routes:

```python
path("manage/slideshow/", views.hub_admin_slideshow, name="hub_admin_slideshow"),
```

The two existing save routes keep their current paths and names **deliberately** — retargeting them
would break both form `action`s and three tests for no gain.

`hub/views.py`, modelled on `voting_settings`:

```python
@fog_admin_required
def hub_admin_slideshow(request: HttpRequest) -> HttpResponse:
    """The Slideshow admin: screens, slides, and what builds itself."""
```

- GET → `_render_slideshow_page(request)` (§4.6).
- POST → bind `SlideshowSettingsForm(request.POST, instance=SiteConfiguration.load())`. Valid:
  save, `messages.success(request, "Slideshow settings saved.")`, redirect to `hub_admin_slideshow`.
  **Invalid: re-render through `_render_slideshow_page(request, settings_form=form)`** so the
  admin's typed values and the field errors survive.

`admin_slideshow_zones_save` / `admin_slideshow_slides_save` change their success redirect from
`f"{reverse('hub_admin_site_settings')}?tab=slideshow"` to `reverse("hub_admin_slideshow")`, and
their invalid branch changes per §4.6.

**Context the new view does NOT need** (do not copy Site Settings' context dict or `extra_head`
wholesale): `SIGNAGE_BASE_URL` (already global via the `surface` context processor), `config`,
`max_upload_image_bytes`, and any rich-editor assets — the slide `body` is a plain `forms.Textarea`
(`hub/forms.py:1161`).

### 4.2 The form

```python
class SlideshowSettingsForm(forms.ModelForm):
    """The Slideshow page's own slice of SiteConfiguration: timing + which blocks self-build."""

    class Meta:
        model = SiteConfiguration
        fields = [
            "signage_default_slide_seconds",
            "signage_event_days_ahead",
            "signage_show_events",
            "signage_show_classes",
            "signage_show_guilds",
            "signage_show_calendar",
            "signage_show_voting",
            "signage_show_directory",
            "signage_show_teach",
            "signage_show_tour",
            "signage_tour_url",
        ]
```

Those first three names are **deleted** from `SiteSettingsForm.Meta.fields` (`hub/forms.py:955-957`),
and the General tab's exclusion chain (`site_settings.html:184`) drops its three `signage_*` clauses.

### 4.3 The template — `templates/hub/admin/slideshow.html`

Extends `hub/base.html`. `<h1 class="hub-page-title">Slideshow</h1>` and a lead line:

> The wall monitors around the space. Point a screen at its URL once; it keeps itself current.

**The page content sits inside a root `<div class="pl-slideshow-page" x-data="{}">`.** This is
load-bearing, not decoration: Alpine only evaluates directives inside an `x-data` tree, and the zone
row's `@click` Copy-URL button carries no `x-data` of its own. Without the root, Copy URL renders,
clicks, and silently does nothing. `.pl-slideshow-page` also supplies the inter-card spacing — it is
the renamed `.pl-slideshow-tab-b` rule (`hub.css:5246-5252`) with `gap` raised to `1.5rem`, because
`.hub-card` carries no margin of its own and the three cards would otherwise butt flush (Rule 18).
Delete the now-orphan `.pl-slideshow-tab-a` rule.

Three `hub-card`s, each its own `<form>` with its Save as the last element **inside that form**:

1. **Screens** — the zones editor, moved from Block B Part 2: the formset, per-row fields through
   `components/form_field.html`, the saved-row setup panel (URL, Copy URL, Preview, QR), the
   margin-spaced `pl-btn--danger` Delete button (now confirmed — §4.4), `+ Add a screen` cloning
   `#zone-empty-template`, and the `No screens yet.` empty state.
2. **Slides** — the slides editor, moved from Block B Part 3: summary rows, reorder buttons and
   grip, Edit disclosure, Delete, the image drop zone with its `.pl-help` size tooltip,
   `+ Add a slide`, and the `No slides yet.` empty state.
3. **Automatic Slides** — `SlideshowSettingsForm`: a lead line, the six `signage_show_*` toggles via
   `components/form_field.html` (which renders booleans as `pl-toggle` switches — never a raw
   checkbox), then a `Timing` sub-heading with `signage_default_slide_seconds` and
   `signage_event_days_ahead`.

**Card order and the Save rule.** Every Save is the last element of its own form, which is what
Rule 21 governs. It is *not* true that nothing renders below a Save on this page: the Screens card's
Save has the Slides card beneath it. That is the established stacked-editor pattern
(`templates/hub/guild_edit.html`) and is accepted here. The order runs concrete-to-ambient — the
screens exist, then the slides that play on them, then the switches that fill the gaps. Each Save
button reads **Save**; the card heading directly above says what is being saved.

Carry across with the markup, unchanged: the `pl-slideshow-*` / `pl-slide-*` classes (they live in
`hub.css`, which `hub/base.html:12` loads on every hub page, so **no CSS moves**), both **delegated**
`#slide-rows` script blocks (source lines 1149-1220 and 1221-1302), and the `+ Add` inline `onclick`
handlers. A cloned `<template>`'s `innerHTML` never executes its own scripts (Rule 16), so the
delegation must survive intact. Do **not** move lines 1099-1148 (the Run-now spinner, bound to
`#site-settings-form`). The editors' `class="site-settings-form"` may be dropped: its theming lives
in an inline `<style>` that stays behind, and every visible field is themed by `.pl-form-group` in
`components.css` regardless.

Two hints earn their place, because they answer the two questions this page raises:

> Under **Screens:** A screen re-checks for new slides every five minutes and restarts itself at 4am.
> Once a monitor is on this URL it needs nothing else.

> Under **Automatic Slides:** These apply to every screen.

Every heading is Title Case (Rule 22). No multi-line `{# #}` comments — use `{% comment %}`
(Rule 17; `tests/template_comment_lint_spec.py` fails the suite on a violation).

### 4.4 Deleting a screen is confirmed

`SlideshowSlide.zone` is `on_delete=models.CASCADE` (`membership/models.py:10232-10239`), so deleting
a screen silently destroys every slide pinned to it. Today that is one unguarded click. Route it
through `components/confirm_modal.html` in **JS mode**: `confirm_js` checks the row's hidden
`DELETE` input and calls `requestSubmit()` on the zones form — the same two statements the button
runs today. One modal per saved zone, `confirm_id="delete-zone-{{ f.instance.pk }}"`, rendered in a
second loop **after** the zones `</form>` (the established sibling idiom), with copy that names the
consequence:

> **Delete this screen?** Every slide pinned to this screen is deleted too. Slides set to show on
> every screen are not affected.

The trigger button keeps its `pl-btn--danger pl-btn--sm` styling and `margin-top:0.75rem`.

### 4.5 Site Settings loses the tab

In `templates/hub/admin/site_settings.html`, delete: the `Slideshow` tab button, the Block A pane,
the whole Block B `<div x-show="tab === 'slideshow'">`, and the two slide-editor `<script>` blocks
(1149-1302). Do not disturb the shared Save's `x-show="tab !== 'announcements' && tab !== 'emails'"`
(`:746`) — it never mentioned slideshow — or the `submitted_tab` hidden input (`:160`), which simply
stops being able to carry the value.

In `admin_site_settings`, drop `"slideshow"` from `allowed_tabs` and, **before** the whitelist falls
back to `general`, redirect the old link:

```python
if request.GET.get("tab") == "slideshow":
    return redirect("hub_admin_slideshow")
```

### 4.6 One render path, so a validation error stops eating work

Today both editor save views **redirect** on invalid with `messages.error(…, "check the highlighted
fields.")` — pointing at highlights that are never rendered, and discarding every row the admin just
typed or added. That is the work-losing failure this pipeline's gate exists to catch, and moving the
page is the moment to fix it.

Extract one helper both the page view and the save views use:

```python
def _render_slideshow_page(
    request: HttpRequest,
    *,
    settings_form: SlideshowSettingsForm | None = None,
    zone_formset: Any = None,
    slide_formset: Any = None,
) -> HttpResponse:
    """Render /manage/slideshow/, reusing any BOUND form/formset passed in so a failed
    save re-renders with the admin's values and errors instead of discarding them."""
```

Unbound arguments default to fresh instances over the same querysets and prefixes the page uses
today. Each save view's invalid branch calls it with its own bound formset (and returns 200, not a
redirect); the valid branch still redirects. The generic `messages.error` stays — now it is true.

### 4.7 The tile

`hub_admin_tools` gains `"tool_slideshow": is_admin`. `admin_tools.html` gains a card gated on it,
placed **last** (the grid is alphabetical and `Site Settings` sorts before `Slideshow`). Href
`{% url 'hub_admin_slideshow' %}`, a monitor/TV feather icon matching the others' 22px stroke-2
style, title `Slideshow`, description:

> The wall monitors: screens, slides, and what shows automatically.

---

## 5. Part B — the slides that build themselves

### 5.1 Config

Six `BooleanField`s on `SiteConfiguration`, beside the existing `signage_*` block, every one
`default=True` with a `verbose_name` and admin-facing `help_text`:

| Field | verbose_name | help_text |
|---|---|---|
| `signage_show_classes` | Show this week's classes | Add a slide for each class or workshop happening in the next seven days. |
| `signage_show_guilds` | Show the guilds | Add a slide listing every guild in the space, with a QR to the guild directory. |
| `signage_show_calendar` | Show the month calendar | Add a slide with this month's calendar, marking the days that have something on. |
| `signage_show_voting` | Show the funding vote | Add a slide about the monthly guild funding vote and when it closes. |
| `signage_show_directory` | Show the member directory | Add a slide with a QR that opens the member directory. |
| `signage_show_teach` | Show Host a Workshop | Add a slide inviting members to run their own workshop or class. |
| `signage_show_tour` | Show Book a Tour | Add a slide inviting visitors to book a walkthrough of the space, with a QR to the booking page. |

One additive migration in `core/migrations/`. No data migration.

### 5.2 The view-model change

```python
@dataclass(frozen=True)
class SignageCalendarDay:
    """One cell of the signage month grid. ``day`` is 0 for padding cells outside the month."""

    day: int
    event_count: int
    is_today: bool


@dataclass(frozen=True)
class SignageSlideVM:
    ...
    calendar_days: tuple[SignageCalendarDay, ...] = ()  # non-empty only on the month-calendar slide
```

`deck_hash` must fold the grid in, or a day gaining its first event never triggers a swap. Add the
digest **into the existing per-VM `"|".join([...])` list**, not as a separate top-level `parts`
entry, so the function keeps the per-slide grouping it documents:

```python
",".join(f"{d.day}:{d.event_count}" for d in vm.calendar_days)
```

`is_today` needs no hashing — `str(timezone.localdate())` is already `parts[0]`.

### 5.3 The generators

All six live in `membership/signage.py` beside `_event_slides`, all typed, all with a docstring, all
taking `(config: SiteConfiguration, default: int)` and returning `list[SignageSlideVM]` — empty when
there is nothing to show, so an empty block never renders a hollow slide. Lazy imports inside each
function, matching the file's style. `build_deck` loads `SiteConfiguration` once and passes it down;
the two `load()` calls that happen *inside* `ClassSession`/`Guild` querysets are accepted as-is.

`build_deck` appends the blocks **after** the admin's own configured slides, each behind its switch:

```
configured slides → classes → events (existing) → guilds → calendar → voting → directory → teach → tour
```

The holding-slide fallback still applies only when the whole deck is empty.

**All datetime handling is local.** Every bucketing, formatting and boundary decision uses
`timezone.localtime(dt)` / `timezone.localdate()`. Stored datetimes are UTC-aware, so a naive
`.day`/`.date()` puts a 6pm-Portland event (01:00Z the next day) on the wrong calendar cell and can
push a last-of-month event out of the grid entirely.

**1. `_class_slides` — this week's classes.** `SIGNAGE_CLASS_DAYS = 7`, `SIGNAGE_CLASS_CAP = 6`.

```python
ClassSession.objects.upcoming_public().filter(
    starts_at__lt=timezone.now() + timedelta(days=SIGNAGE_CLASS_DAYS)
).select_related("class_offering").order_by("starts_at")
```

Dedupe by `class_offering_id` keeping the earliest session, then cap. `upcoming_public()` already
enforces published + non-private + the demo gate. Per slide: `kind="class"`, `title` = the
offering's title, `meta` = the local start rendered room-legibly (`"Tue, Sep 15 · 6:00 PM"`, built
from `timezone.localtime` through `django.utils.formats` — never a hand-rolled `strftime` on a UTC
value), `qr_svg` of `offering.public_url`, `url_display` = `_friendly_url` of the same, `body = ""`.

**2. `_guilds_slide` — one slide, every guild.** `Guild.objects.visible().order_by("name")`; return
`[]` when empty. `kind="guilds"`, `title = "The Guilds Of Past Lives"`, `body` = the names joined
with `" · "`, QR + `url_display` for `settings.GUILDS_BASE_URL + reverse("hub_guild_directory")`.

**3. `_calendar_slide` — the current month.** `kind="calendar"`, `title` = e.g. `"September 2026"`,
`calendar_days` = the flattened cells of
`calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)` (that helper yields `0` for
padding days, which is exactly the `day=0` contract), QR + `url_display` for
`settings.MEMBER_BASE_URL + reverse("hub_community_calendar")`. Month bounds and `is_today` come
from `timezone.localdate()`.

Day counts come from **public sources only** — this is the privacy rule the original signage spec
exists to protect and the easiest thing here to get wrong:

- site-wide published community events: `CommunityEvent.objects.published().site_wide()`, expanded
  through `occurrences_in(first_of_month, last_of_month)`, each occurrence bucketed by
  `timezone.localtime(occ).date()`;
- public class sessions **across the whole month, not just the future** (see below).

Do **not** call `hub.calendar_entries.community_event_entries` or `_get_calendar_context`: with
`guild=None` the former returns every guild's meetings, and a private guild meeting on a lobby wall
is the exact leak `_event_slides` was written to avoid.

`site_wide()` does include Guild Lead Meetings (`membership/serializers.py:136-141` forces
`guild=None` for `COMMUNITY` and `LEAD_MEETING`). That is **intended**: the grid renders a dot and a
count, never a title, so a lead-meeting day is indistinguishable from any other busy day, and those
days already show on the member community calendar.

`ClassSessionQuerySet.upcoming_public()` is future-only, so reusing it here would leave the elapsed
half of the month blank and read as broken. Extract the shared gate rather than duplicating it:

```python
class ClassSessionQuerySet(...):
    def public(self) -> ClassSessionQuerySet:
        """Sessions whose offering is publicly visible (published + non-private + demo gate)."""

    def upcoming_public(self) -> ClassSessionQuerySet:
        return self.public().filter(starts_at__gte=timezone.now())

    def public_between(self, start, end) -> ClassSessionQuerySet:
        """Public sessions starting inside [start, end] — past ones included."""
```

`upcoming_public()`'s behaviour and docstring are unchanged; its body just delegates.

**4. `_voting_slide`.** From `get_cycle_context()`. `kind="voting"`,
`title = "Guild Funding Vote"`, `meta = f"{current_cycle_label} · Voting closes {cycle_closes_on}"`,
`body` = *"Every member has a say in how this month's funding pool is split between the guilds."*,
QR to `MEMBER_BASE_URL + reverse("hub_guild_voting")`.

`membership/cycle.py:11-27` currently formats off `timezone.now()` (UTC), so on the last evening of a
month it names next month's cycle and a closing date in the wrong month. Wrong on the voting page
already; on a lobby wall it is wrong in public. Fix it at the source: derive `now` from
`timezone.localtime()`. Verify `tests/hub/guild_voting_spec.py` still passes and add an assertion.

**5. `_directory_slide`.** `kind="directory"`, `title = "Member Directory"`, `body` = *"See who else
is in the space, what they make, and what they can teach you."*, QR to
`MEMBER_BASE_URL + reverse("hub_member_directory")`.

**6. `_teach_slide`.** `ClassSettings.load()`; `title = cta_title or teach_page_title`,
`body = cta_line or teach_page_lead`; return `[]` only if both fall through to blank. QR to
`MEMBER_BASE_URL + reverse("classes:teach_overview")`.

A QR pointing at a members-only page is intended: the audience is members standing in the building,
and the login wall is one tap.

### 5.4 Rendering

`templates/signage/_deck.html` currently hardcodes a class for two kinds only. Replace both branches
with one kind class emitted for **every** slide, so per-kind styling is possible at all:

```
class="pl-sign-slide pl-sign-slide--{{ s.kind }}{% if forloop.first %} is-active{% endif %}"
```

`.pl-sign-slide--event` and `.pl-sign-slide--holding` keep working unchanged; `--classes`,
`--guilds`, `--calendar`, `--voting`, `--directory`, `--teach`, `--tour` become addressable.

Then add one branch, before the existing body block:

```
{% if s.calendar_days %} … month grid … {% endif %}
```

The grid is a `.pl-sign-calendar` with seven `.pl-sign-calendar__dow` headers (Mon…Sun) and one
`.pl-sign-calendar__day` per cell; `day == 0` renders `.pl-sign-calendar__day--pad` (empty),
`is_today` adds `--today`, `event_count > 0` adds `--has-events` plus a dot (or the count when above
one). Body text keeps its autoescaped `linebreaksbr` — do not introduce `|safe` on `body`.

`static/css/signage.css` gains the grid and the variants. The stage is `100vw × 100vh` with
`overflow:hidden`, so the grid must fit without scrolling — size it in `clamp()`/`vmin` like the rest
of the file. `.pl-sign-slide--guilds .pl-sign-slide__body` needs a wider `max-width` than the
`24ch` default (`signage.css:99-100`), which will not hold a dozen guild names. The signage surface
is dark-only by design; `hub.css` changes are checked in both themes.

---

## 6. UI / UX completeness

**`/manage/slideshow/` (new)**

- Root `x-data="{}"` so every moved Alpine directive (Copy URL, `$dispatch` toast, the confirm
  modals) actually runs.
- Three cards, each a form with a visible **Save** as its last element, wired to that form. Card
  seams get `1.5rem` from `.pl-slideshow-page`; the Delete buttons keep their top margin (Rule 18).
- Both editors keep **`+ Add a screen` / `+ Add a slide`** and **per-row Delete buttons** — real
  `pl-btn--danger` buttons, never toggles (Rules 3 and 11). Deleting a *screen* is confirmed through
  `confirm_modal.html` with copy naming the cascade (§4.4); deleting a *slide* stays a direct button
  (it destroys only itself).
- **Empty states:** `No screens yet…`, `No slides yet…`, both carried over. A zone with no slides
  still plays: generated blocks, then the branded holding slide.
- **Error state (now real):** an invalid formset or settings form re-renders the page with the bound
  values and `pl-field-error` messages plus the existing `messages.error` (§4.6) — typed rows are no
  longer discarded. The image field keeps its "re-attach the file" hint, since a file input genuinely
  cannot be repopulated.
- **Success state:** `messages.success` on each save.
- The six switches render as `pl-toggle` switches through `components/form_field.html`, each showing
  its `help_text`, under a line saying they apply to every screen.
- **Mobile:** cards stack, `.pl-slideshow-daterow` already collapses under 640px, the zone QR/actions
  panel wraps. No horizontal scroll on the page body.
- **Themes:** every control comes from the component library and hub tokens; no inline
  `background`/`color` on any input (Rule 13). Check both themes.

**Admin Tools** — the new tile matches the others exactly and lands last. Non-admins never see it.

**Site Settings** — the tab is gone; `?tab=slideshow` redirects rather than 404ing or silently
landing on General.

**The kiosk** — every generated slide carries a QR and a readable `Learn more` URL, or neither (the
existing `{% if s.qr_svg or s.url_display %}` guard). An empty block contributes nothing rather than
an empty slide. The month grid fits `100vh` at 1080p landscape and on a portrait tablet.

---

## 7. What this spec deliberately does not build

- **No cron, no weekly job, no "regenerate slides" command.** Slides are computed per request from
  live models; the player re-polls every 300s and reloads at 04:00. Persisting generated slides
  would add a staleness bug and a job to babysit for no gain.
- **No per-block ordering or per-block duration.** Fixed order, shared duration.
### Addendum (added after the first review pass, at Jo's request)

**7. `_tour_slide`.** A Book a Tour invitation with a QR to the booking page. This is the one
generated slide whose destination is NOT an internal `reverse()` — tours are booked on the
marketing site (`https://www.pastlives.space/tours`), so the URL is a new admin-editable
`SiteConfiguration.signage_tour_url` (that default; `blank=True`) rather than a constant. A
hardcoded pastlives.space URL would contradict the brand block's "one deployment is one
organization" rule, and deriving it from `org_website_url + "/tours"` assumes a path another
org would not have. Blank drops the slide, exactly as a blanked Host a Workshop CTA does.
Fixed copy, no admin fields: title *"Book a Tour"*, body *"New here? Book a walkthrough and a
member will show you the shops, the tools, and how to join."* Appended last in the block order
so the existing order is unchanged. No CSS variant — it is a title/body/QR slide, like the
voting and directory slides, both of which use the default styling.

- **No new copy fields.** The Host a Workshop slide reads the copy admins already edit.
- **No changes to the zones/slides models**, the player JS, the surface middleware, or the
  `SIGNAGE_HOSTS` go-live wiring.

---

## 8. Tests

BDD, `*_spec.py`, `describe_*` blocks only (`context_*` is silently skipped — never use one).
factory-boy for data. 100% coverage gate.

**`tests/membership/signage_spec.py`** — one `describe_` per generator:

- classes: appears inside the window, absent past it; deduped to one slide per offering; capped at
  `SIGNAGE_CLASS_CAP`; a private/unpublished class never appears; toggle off removes the block.
- guilds: lists visible guilds; no slide when there are none; toggle off removes it.
- calendar: `calendar_days` covers the month with `day=0` padding; a site-wide event dots its day;
  **a guild-only meeting does not** (the privacy assertion — mirror the existing
  `it_includes_site_wide_events_and_excludes_guild_meetings`); **an event at 6pm local on the last
  day of the month dots that day** (the timezone assertion); a class earlier this month still dots
  its day (the `public_between` assertion); toggle off removes it.
- voting / directory / teach: each renders with the expected title and a QR; each toggle off removes
  its slide; teach falls back to the page title/lead when both CTA fields are blank.
- `deck_hash`: two decks differing only in a day's `event_count` hash differently.

**`tests/classes/`** — `ClassSessionQuerySet.public_between` includes a past-but-this-month session
and still excludes private/unpublished/demo; `upcoming_public` behaviour is unchanged.

**`tests/hub/guild_voting_spec.py`** — `get_cycle_context` names the local month on the last evening
of a month.

**`tests/hub/admin_slideshow_spec.py`** — retarget from the tab to the page. These existing
assertions die and must be rewritten, not deleted wholesale: `"tab = 'slideshow'"`,
`html.count("tab === 'slideshow'") >= 2`, `"pl-slideshow-tab-a"`, `"pl-slideshow-tab-b"`,
`it_puts_the_global_signage_fields_inside_the_settings_form_but_not_the_editor_forms` (it parses
`<form id="site-settings-form">` for `name="signage_default_slide_seconds"`, which is no longer on
that form — re-point it at the new page's settings form), and the two
`"tab=slideshow" in resp["Location"]` redirect assertions. Add: anonymous redirect, non-admin 403,
both editors render with `+ Add` and Save, both save views persist and redirect to
`hub_admin_slideshow`, **an invalid save re-renders with the typed value still present** (§4.6), the
settings form saves the six switches, `?tab=slideshow` 302s to the new page, the delete-screen
confirm modal is present, and the root `x-data` wrapper exists so Copy URL is live. Keep the
structural assertion that the editor `<form>`s are siblings, never nested.

**`tests/hub/admin_tools_spec.py`** — the `Slideshow` tile shows for an admin, not for a non-admin.

**`tests/e2e/signage_admin_spec.py`** — point at `hub_admin_slideshow`, drop the tab click, and
replace `get_by_role("button", name="Save slides", exact=True)` with a locator scoped inside the
slides card (three buttons now read `Save`, so an unscoped lookup is a strict-mode violation).

**`tests/e2e/screenshots_spec.py`** — the `?tab=slideshow` entry becomes the new URL.

Sweep for any other spec asserting the tab button or `allowed_tabs`. Run
`tests/template_comment_lint_spec.py` after touching templates and `python manage.py check` after the
migration (CI runs system checks local pytest skips).

---

## 9. Version & changelog

`VERSION = "1.49.0"`. One new `CHANGELOG` entry at that version: member-facing, plain language, no
dashes, no jargon. It covers what now appears on the lobby screens on its own, and closes with one
line for admins that Slideshow is now its own tile.
