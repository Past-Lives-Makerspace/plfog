# Site Settings Demo-Visibility Toggle

**Date:** 2026-08-28
**Goal:** Let an admin seed demo data on production but keep it hidden from real members
until a live demo, via two Site Settings switches.

## Why

The demo showcase class and the example (Cartographers) guild are enriched to look real.
If they sit on the live catalog, a member could register or pay for a fake class, or find
the example guild in the directory. Two default-off toggles gate all member-facing
surfaces, so demo content can be seeded now and revealed only for the demo.

## Design

- **`SiteConfiguration.display_demo_classes`** (BooleanField, default False). Gated at the
  single choke-point `ClassOfferingQuerySet.public()`: when off, exclude
  `slug__startswith="demo-"`. Because `bookable()` calls `public()` and every public class
  surface (catalog, class detail, register, community calendar, guild calendars, Discord
  class posts, hero counts, the member tour target) routes through one of them, the one
  edit covers them all. Admin/teaching querysets (`.all()`, `editable_by`, `for_instructor`,
  `hosted_by`) do NOT use `public()`, so staff always see and manage demo classes.

- **`SiteConfiguration.display_demo_guild`** (BooleanField, default False). New
  `GuildManager.visible()` = active guilds, plus the example guild (by
  `EXAMPLE_GUILD_SLUG`) when the flag is on. Only the two display surfaces route through it:
  `GuildManager.directory()` and the hub sidebar list (`_get_hub_context`). Voting, the
  ballot, and every other `is_active` filter are deliberately left untouched, so the
  example guild never enters funding regardless of the flag (its safety contract holds).

- **Site Settings UI:** both fields added to `SiteSettingsForm.Meta.fields`, rendered as
  toggles in the Features tab of `templates/hub/admin/site_settings.html`, and added to the
  General-tab exclusion list so they render once.

- `EXAMPLE_GUILD_SLUG` moves to `membership/models.py` (the manager needs it) and is
  re-exported from `membership/example_guild.py` for existing importers.

## Tests

`tests/hub/demo_visibility_flags_spec.py`: default-off hides demo classes from
`public()`/`bookable()` and the example guild from `directory()`/`visible()`; on reveals
both; the example guild never appears in `is_active`-filtered lists even when on; the base
class queryset is never gated (admins keep managing demo classes).

## Operations

Ship this first, then seed prod with `demo_data` (content lands hidden). For the demo, flip
both toggles on in Site Settings > Features; turn them off (or run `demo_data --remove`)
after.

## Out of scope

No change to voting, the guild detail page (already reachable by direct URL), or how demo
data is seeded.
