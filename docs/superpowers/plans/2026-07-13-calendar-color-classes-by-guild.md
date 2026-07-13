# Color class events by their guild on the Community Calendar — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — Community Calendar `/calendar/` (and, as a ripple, each guild's own calendar tab on `/guilds/<slug>/`).
**Branch of record:** `fix/calendar-feeds-and-picker` (all anchors below are read from there).
**Related:** `plfog/version.py` current line 0.21.x (0.21.15 = "Recurring events show up on the calendar").

---

## 1. Summary

On the Community Calendar, every class event pulled from the catalog shows in the **same purple**, no matter which guild runs it — so a member can't tell Metalworking's classes from Fiber Arts' at a glance, and there's a single "Classes" legend toggle that hides them all together. This change **colors and groups each class by its guild**: a class inherits its guild's calendar color, sits under that guild's legend/filter toggle, and can be shown or hidden per guild. Classes whose category has no guild fall back to a generic "Other classes" color and toggle.

The guild link already lives in the data — `sync_local_class_events` stamps `guild = offering.category.guild` on every class `CalendarEvent` (`hub/calendar_service.py:239-241`). Only the **color routing** ignores it today. This is a routing + legend fix, not new plumbing.

### Locked decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Where does a class's color come from? | Its guild's `calendar_color`, via `source_key`, even though `source="classes"`. |
| `source_key` on both event types? | Yes — `CalendarEvent.source_key` **and** `CalendarEntry.source_key` route a class by guild. |
| Which guilds get a legend/filter entry? | **Every** guild that owns a guild-colored event in the window — including a guild that has class events but **no `calendar_url`** (absent from the legend today). |
| A class with no guild? | Falls back to the generic classes color and keeps a legend entry ("Other classes"). |
| Orientation / community entries? | **Unchanged** — orientation stays amber, community stays blue. The class-by-guild routing must not recolor them. |

---

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Guild link on each class event | `guild = offering.category.guild` set at sync | `hub/calendar_service.py:239-241` |
| Color lookup key per event | `CalendarEvent.source_key` (returns `str(guild_id)` for GUILD, `feed-{id}` for GENERAL, else `self.source` → constant `"classes"`) | `membership/models.py:4417-4424` |
| Same key on synthetic entries | `CalendarEntry.source_key` (returns `self.source`) | `hub/calendar_entries.py:47-49` |
| Color dict consumed by chips | `source_colors` built in `_get_calendar_context` | `hub/views.py:3211-3219` |
| Per-guild color value | `Guild.calendar_color` (default `#4B9FEE`) | `membership/models.py:1127` |
| Generic classes color | `SiteConfiguration.classes_calendar_color` (default `#7C5CBF` purple) | `core/models.py:144-149` |
| Chip renders from key | `--chip-color: {{ source_colors|get_item:src }}` | `templates/hub/partials/calendar_content.html:37,43,89,95` |
| Legend + filter toggles | one "Classes" filter + `{% for guild in guilds_with_calendars %}` | `templates/hub/community_calendar.html:203-224` |
| Default-active filter keys | `default_filters` seeded per surface | `hub/views.py:3309-3317` (community), `hub/views.py:530-533` (guild page) |
| Legend wraps on mobile | `.pl-calendar-filters { flex-wrap: wrap }` | `static/css/calendar.css:112-117` |
| Chip stays readable on any color | tint + left border, text is `var(--hub-text)` (not white-on-fill) | `static/css/calendar.css:310-313` |

**Gaps to close (all small):**
1. `source_key` on both types must return the guild key for a class.
2. `_get_calendar_context` must add **every guild that owns a guild-colored event** to `source_colors` (today only guilds with a `calendar_url` are added — `hub/views.py:3204,3218`).
3. Legend template must iterate that fuller guild set, and the "Classes" toggle must become the no-guild fallback.
4. `default_filters` on **both** surfaces must include those guild keys (or the newly-grouped chips render hidden by default).

No new model, field, migration, color token, or CSS class. `pl-` prefix untouched.

## 3. Where the code lives

```
membership/models.py          # CalendarEvent.source_key           (edit, ~4417)
hub/calendar_entries.py       # CalendarEntry.source_key            (edit, ~47)
hub/views.py                  # _get_calendar_context               (edit, ~3204-3219)
                              # community_calendar default_filters  (edit, ~3309-3317)
                              # guild_detail guild_cal_filters       (edit, ~530-533)
templates/hub/community_calendar.html  # legend/filter block        (edit, 203-224 + empty-notice 225)
plfog/version.py              # VERSION bump + CHANGELOG entry
```

No CSS file changes (the legend already wraps and the chip already tints + uses `--hub-text`).

## 4. Data model

No schema change. `CalendarEvent.guild` (`membership/models.py:4356`), `Guild.calendar_color`, and `SiteConfiguration.classes_calendar_color` already exist. No migration.

## 5. Business logic (fat models) — the `source_key` routing

Both properties own the color-key decision; the view and template stay dumb.

**`CalendarEvent.source_key`** (`membership/models.py:4417`) — prefer the guild key whenever a guild is set, so a `source="classes"` row with a guild colors by that guild:

```python
@property
def source_key(self) -> str:
    """Color-lookup key. A class or guild event keys by its guild; a general
    feed event keys by its feed; anything else by its raw source."""
    if self.guild_id:
        return str(self.guild_id)
    if self.source == self.Source.GENERAL and self.feed_id:
        return f"feed-{self.feed_id}"
    return self.source
```

*Safe by construction:* on `CalendarEvent`, only GUILD and CLASSES rows carry a `guild` (GENERAL uses `feed`, guild null — `membership/models.py:4364,4372`). So this only newly affects class rows; guild-iCal rows already returned `str(guild_id)`. A class with no guild falls through to `"classes"`.

**`CalendarEntry.source_key`** (`hub/calendar_entries.py:47`) — the synthetic wrappers are the landmine. Class **and** orientation **and** community entries all carry `guild` (see `hub/calendar_entries.py:86,102,140`), so a naïve "guild set → guild key" would recolor orientation (amber) and community (blue) too. **Scope it to classes only:**

```python
@property
def source_key(self) -> str:
    # Only classes route by guild — orientation stays "orientation", community stays "community".
    if self.source == "classes" and self.guild is not None:
        return str(self.guild.pk)
    return self.source
```

> Note the asymmetry: `CalendarEvent` uses `guild_id`; `CalendarEntry` is a dataclass holding a `guild` **object**, so it reads `self.guild.pk`. Do not "unify" these — the guards differ on purpose.

*Scope reality:* `CalendarEntry` class entries are only produced by `guild_calendar_entries`, which the view calls only for a **single guild's** page (`hub/views.py:3179-3182`). The Community Calendar's classes are DB `CalendarEvent` rows, so the community fix rides entirely on the `CalendarEvent` change + §6's context change. The `CalendarEntry` edit keeps the guild page's own class chips consistent (see the guild-page ripple in §6).

**`_get_calendar_context`** (`hub/views.py:3204-3219`) — widen `source_colors` and expose the legend set + fallback flag. Compute from `all_events` (already `select_related("guild")`, so no extra queries), keyed to events that actually render a **guild** chip (this deliberately excludes a guild that only has a community/orientation entry — adding it would make a dead toggle that controls nothing):

```python
# guilds that own a guild-colored chip in this window (class rows have a guild even
# without an iCal URL), unioned with guilds that have a configured calendar_url.
event_guilds = {
    e.guild for e in all_events
    if e.guild is not None and e.source_key == str(e.guild.pk)
}
legend_guilds = sorted(set(guilds_with_calendars) | event_guilds, key=lambda g: g.name)
for g in legend_guilds:
    source_colors[str(g.pk)] = g.calendar_color

# True when a class in this window has no guild → keep the generic fallback toggle.
has_ungrouped_classes = any(e.source_key == "classes" for e in all_events)
```

Return `legend_guilds` and `has_ungrouped_classes` in the context dict (alongside the existing `guilds_with_calendars`, which the empty-notice still reads).

## 6. UI / UX

### Screen A — Community Calendar `/calendar/`, Calendar tab

- **Templates:** `templates/hub/community_calendar.html:185-228` (legend/filter row); chips render from `templates/hub/partials/calendar_content.html`.
- **Container:** existing `.pl-calendar-filters` row of `.pl-calendar-filter` toggle buttons + the chip grid. No new components; this is a data/legend change, not a form.
- **Legend / filter changes (lines 203-224):**
  - Replace `{% for guild in guilds_with_calendars %}` → `{% for guild in legend_guilds %}`. Each button keeps its existing markup: guild logo (`logo_prefix`) or a solid `.pl-calendar-filter__dot` colored `{{ guild.calendar_color }}`, `--filter-color: {{ guild.calendar_color }}`, `toggleFilter('{{ guild.pk }}')`, `isActive('{{ guild.pk }}')`. A class-only guild (no `calendar_url`) now appears here for the first time.
  - The `{% if classes_enabled %}` "Classes" toggle becomes the **no-guild fallback**: gate it on `{% if has_ungrouped_classes %}`, relabel **"Other classes"**, key stays `'classes'`, color stays `{{ classes_color }}`. When every class has a guild it simply doesn't render (no dead toggle).
  - Empty notice (line 225): swap the guard to `{% if not calendar_feeds and not has_ungrouped_classes and not legend_guilds %}` so it stays accurate with the new sets.
- **Default-active state — the wiring that makes chips visible (`hub/views.py:3309-3317`):** the Alpine `activeFilters` seeds from `default_filters_json` (`community_calendar.html:78`). It must include every guild key or the newly-colored class chips render hidden on first load:

  ```python
  default_filters = ["community"]
  for feed in cal_ctx["calendar_feeds"]:
      default_filters.append(f"feed-{feed.pk}")
  if cal_ctx["has_ungrouped_classes"]:
      default_filters.append("classes")
  for g in cal_ctx["legend_guilds"]:      # was: guilds_with_calendars
      default_filters.append(str(g.pk))
  ```

- **Interplay — echo-dedup:** untouched. The `CommunityEvent.pushed()` exclusion (`hub/views.py:3170`) runs on `events_qs` **before** any `source_key` read and only drops Google-echoed community rows; class rows aren't community echoes, so nothing about coloring interacts with it.
- **Interplay — the per-guild filter:** a guild toggle already hides/shows that guild's iCal chips; it now also governs that guild's class chips, because they share the `str(guild.pk)` key. That's the feature — one toggle per guild covers all of that guild's calendar content.
- **Returning members (localStorage):** `activeFilters` persists in `localStorage['calFilters']`, so a member who visited before this ships keeps their saved set and a **new** class-only guild's key won't be in it → that guild's classes start hidden for them until they enable it. Acceptable (same as any newly-added feed/guild today), but call it out in the CHANGELOG-adjacent QA and note it in Open items; the existing `calCommunityRollout` shim (`community_calendar.html:83-89`) is the precedent if we ever decide to force-add.
- **States:**
  - *Grouped (happy path):* each guild's classes tinted with its color, under its named toggle.
  - *No-guild class:* colors `classes_color`, sits under "Other classes".
  - *Color collision:* `Guild.calendar_color` defaults to `#4B9FEE` for **all** guilds, so guilds that never customized share one blue. The **legend label (guild name) is the disambiguator**, and the chip is a subtle 18% tint + left border over `var(--hub-text)` — never a solid fill with white text — so text stays readable and rows stay distinguishable even when two guilds share a color. Distinct *color* per guild is a content task (leads set `calendar_color`), flagged in Open items — not blocked by this change.
  - *Empty window:* existing `.pl-calendar-empty` copy unchanged.
- **Readability (dark + light):** no hardcoded colors added. Chip text is `var(--hub-text)`; the legend dot is a solid guild hex on a themed card (already how feeds/iCal-guilds render). Nothing here inlines `background`/`color` on a form control (there are no form controls). Verify **both** themes: the new class-only-guild chips and their legend dots on Obsidian and Slate.
- **Mobile:** `.pl-calendar-filters` already `flex-wrap: wrap` (`calendar.css:112-117`), so the extra guild toggles reflow onto new rows with the 0.5rem gap — no horizontal scroll, no CSS change. Verify at ~360px that the longer legend wraps cleanly.

### Screen B — Guild calendar tab `/guilds/<slug>/` (ripple, must not regress)

`guild_detail` calls `_get_calendar_context(request, guild=guild)` (`hub/views.py:528`) and its own `guild_cal_filters` (`hub/views.py:530-533`). Because `CalendarEntry.source_key` now keys that guild's class entries to `str(guild.pk)`, the guild-page defaults must include that key or the guild's own classes vanish from its own tab by default:

```python
guild_cal_filters = ["orientation", "community"]     # "classes" only if a no-guild class shows here
if calendar.get("has_ungrouped_classes"):
    guild_cal_filters.append("classes")
for g in calendar["legend_guilds"]:
    guild_cal_filters.append(str(g.pk))
```

`legend_guilds` on a guild page already includes this guild (its class entries produce `str(pk)` chips), so its color also lands in `source_colors` via §5 — the guild's classes color correctly even when it has no `calendar_url`. No template change needed on the guild page; it renders the same legend block.

## 7. Notifications / emails / activity

None. No email, notification, `emit()`, or `SiteActivity` touched.

## 8. Build order (each phase ships green)

1. **Routing + context.** Edit both `source_key` properties, widen `source_colors`, add `legend_guilds` + `has_ungrouped_classes` to `_get_calendar_context`. Update `default_filters` (community) and `guild_cal_filters` (guild page). Backend-only; full suite + `ruff` + `mypy` green.
2. **Template.** Legend iterates `legend_guilds`; "Classes" → "Other classes" gated on `has_ungrouped_classes`; fix empty-notice guard. Verify both themes + mobile in the live dev container.
3. **Housekeeping.** Bump `plfog/version.py` `VERSION` → `0.21.16`; add a new member-facing CHANGELOG entry at the top (new net-new feature, not a fold into 0.21.15's recurring-events entry), `screenshot: "community-calendar"`. Draft copy:
   > **Guild colors on the calendar** — Class events on the Community Calendar now take on each guild's own color and sit under that guild's filter, so you can tell one guild's classes from another's at a glance — and show or hide any guild's classes with a tap.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image, ≥98% gate.

- **`membership/spec/models/calendar_event_spec.py` — `describe_source_key`:**
  - `it_returns_the_guild_key_for_a_class_with_a_guild` — `CalendarEvent(source="classes", guild=G)` → `str(G.pk)`.
  - `it_falls_back_to_classes_when_a_class_has_no_guild` — `source="classes", guild=None` → `"classes"`.
  - `it_still_keys_a_guild_ical_event_by_its_guild` and `it_still_keys_a_general_event_by_its_feed` — guard the reorder didn't regress GUILD/GENERAL.
- **`hub/spec/calendar_entries_spec.py` — `describe_CalendarEntry` → `describe_source_key`:**
  - `it_keys_a_class_entry_by_its_guild`, `it_keeps_orientation_amber` (`source="orientation", guild=G` → `"orientation"`), `it_keeps_community_blue` (`source="community", guild=G` → `"community"`). The last two are the anti-regression that protect existing colors.
- **`hub/spec/views/calendar_spec.py` (or the existing calendar view spec) — `describe__get_calendar_context`:**
  - `it_adds_the_color_of_a_guild_that_only_has_class_events` — a guild with a class in-window but no `calendar_url` appears in `source_colors[str(pk)]` == its `calendar_color`.
  - `it_lists_guilds_with_class_events_in_legend_guilds` — that guild is in `legend_guilds`.
  - `it_omits_a_guild_that_only_has_a_community_event` — a guild whose only in-window event is a `community` entry is **not** in `legend_guilds` (no dead toggle).
  - `it_flags_has_ungrouped_classes_when_a_class_has_no_guild`, and `it_is_false_when_every_class_has_a_guild`.
- **`describe_community_calendar` (view) — `it_seeds_guild_keys_into_default_filters`:** a class-only guild's `str(pk)` is present in the rendered `default_filters_json` (assert on the JSON island / context, not on visible chip text — the "what's new" widget echoes CHANGELOG copy).
- **Gotchas:** seed a `MembershipPlan` before any member-gated request (signal skips `Member` creation otherwise). Anchor sessions inside the 4-week window via the same `timezone.now()`-relative dates the view uses; keep class `starts_at` between `now` and the window end so `bookable()` + the window filter both keep them.

## 10. Open / deferred

- **Shared default color.** All guilds default to `#4B9FEE`, so grouping alone won't make un-customized guilds *color*-distinct — only *label*-distinct. Deferred: a per-guild color palette / auto-assign, or a nudge on the guild edit page to set `calendar_color`. Out of scope here; the legend name carries disambiguation in the meantime.
- **No single "hide all classes" switch anymore.** Class visibility is now per-guild (plus "Other classes"). Intentional tradeoff of grouping; a combined control isn't planned (YAGNI).
- **localStorage lag for returning members.** A brand-new class-only guild's toggle starts off for members with a saved filter set, until they enable it — matching how any new feed/guild behaves today. Force-adding via a rollout shim (like `calCommunityRollout`) is possible but not proposed.

## 11. Review addendum — fold in before building

An adversarial UX review confirmed the community-page design + `source_key` scoping are sound (orientation/community keep their color; no dead toggle from the `event_guilds` path; no N+1; the no-guild fallback is safe). But it's built on a wrong-template premise for the guild page. Fix before building:

1. **The guild tab uses a DIFFERENT legend template than the spec targets — this is the load-bearing gap.** §6 Screen B claims "no template change needed" — false. The guild tab renders `templates/hub/partials/guild_calendar_app.html` (legend at lines 68-87), a separate hardcoded block, NOT `community_calendar.html`. As drawn: the hardcoded `'classes'` toggle controls nothing (a dead toggle on every guild page); a class-only guild with no `calendar_url` gets NO per-guild toggle (it's gated `{% if guild.calendar_url %}`); and returning members' saved `guildCalFilters-<pk>` lacks the new `str(pk)` key, so their guild's own classes render HIDDEN with no toggle to restore them. Apply the same legend rework here: iterate the real guild set, drop/relabel the hardcoded `'classes'` chip, and un-gate the per-guild toggle from `calendar_url`.
2. **The event LIST still shows classes as purple "Classes".** `calendar_event_item.html:34-41` hardcodes the class branch to the `'classes'` color + the literal "Classes" (never the guild name), while the iCal-guild branch two lines down shows `event.guild.name`. So the grid chip becomes guild-blue but the list item stays purple/"Classes". Spec this file too (note the class branch also carries a "Register →" link, so it's not a trivial branch-merge).
3. **The returning-member localStorage regression is a ship-day mass event, not a hypothetical (supersedes the §10 "lag" note).** Every existing class-only guild's classes go default-hidden for the whole returning-member base at once (saved `calFilters` has `'classes'`, not the guilds' `str(pk)`). A one-time migration shim (like the existing `calCommunityRollout`, `community_calendar.html:83-89`) is effectively required, not optional — migrate `'classes'` → add the current `legend_guilds` keys.
4. **No test guards the Screen B guild-page regression** (the highest-risk ripple). Add a test that a guild's own classes stay visible + toggleable on its own guild tab.
5. **Headline oversold for the default-color majority.** All guilds default to `#4B9FEE` and the grid chip shows no guild name/logo, so two un-customized guilds look identical on the grid — only the legend disambiguates. Either soften the CHANGELOG "tell them apart at a glance" copy or add a nudge for leads to set a distinct `calendar_color`.
