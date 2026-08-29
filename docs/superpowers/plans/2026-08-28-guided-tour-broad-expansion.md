# Guided Tour Broad Expansion + Demo Class Enrichment

**Date:** 2026-08-28
**Goal:** Flesh out all four role tours to cover the full Tuesday demo script, enrich the
fictional full class so it is a believable showcase, and seed the whole thing on production.

## Context

The four auto-navigating tours (`core/tours.py`) shipped in v1.21.0 and work well. For the
Sept 1 demo the presenter wants each tour to walk the *complete* role workflow, not just the
core stops. The demo runs on **production** (`members.pastlives.space`), seeded with the
prod-safe `demo_data` command. The fictional "full class + waitlist" (already seeded by
`_ensure_full_waitlist_class`) is thin (generic description, no images) and only appears
where `demo_data` has been run.

## Decisions (locked with the requester)

| Question | Decision |
|---|---|
| Demo environment | **Production.** Seed `demo_data` on prod (prod-safe: `@pastlives.demo` emails, `demo-` slugs, no Stripe/email). Verify DB host before/after. |
| Tour scope | **Broad expansion** — a guided stop for (nearly) every bullet in the demo workflow lists. |
| Full class | **Enrich but keep standalone** — real title, rich description, hero + gallery images, kept in its own demo category (not tied to Cartographers). Keep 4-confirmed + 3-waitlist state. |

## Tour expansion (target stops per role)

Every new step targets a `[data-help-key="…"]` that must exist in `core/help_registry.py`
**and** in a template (drift guard: `tests/hub/help_keys_spec.py`). New keys get a template
hook in the same PR. Element-less (`target=None`) steps need no hook.

### Member (`member-welcome`)
Existing lap is complete except one gap:
- **Discord sync** — after the calendar-subscribe stop, an element-less note: classes, events,
  and guild meetups mirror to the Past Lives Discord automatically. No new hook (centered step).

### Instructor (`instructor`)
- **Class detail / management landing** (`classes:teach_class_detail`, newest class = the full
  showcase class) — overview stop. New hook `teach.class-overview`.
- **QR flyer** — the printable flyer / QR download control on the class detail page
  (`classes:class_flyer` / `classes:class_qr`). New hook `teach.class-qr`.
- Reword the waitlist stop to name the re-charge-on-promote behaviour for paid classes.

### Guild lead (`guild-lead`)
- **Thank-you email** — the post-orientation thank-you editor
  (`GuildOrientationSettings.thankyou_email_*`). Hook to be placed where it is edited.
- **QR codes** — the guild printable flyer / QR (`hub_guild_flyer` / `hub_guild_qr`). New hook.

### Admin (`admin`)
Broaden from 5 to the fuller admin surface:
- Keep: site-wide announcements, refunds, review queue, discount codes, reconciliation.
- Add: **Orientations Dashboard** (`hub_orientations_dashboard`, key `orientation.dashboard`),
  **event + announcement approvals** (`announcements.review-proposals` and/or the events queue),
  **manage members**, **activity pane**, and **quickstart guides** — each only if a real anchor
  and route exist (drop gracefully otherwise, per the resolver-raises-`ValueError` pattern).

Exact anchors are being mapped read-only before implementation; any stop whose page/anchor does
not exist is dropped rather than faked.

## Demo class enrichment (`_ensure_full_waitlist_class`)

- Believable title (drop the `[DEMO]` display prefix; teardown stays keyed on the `demo-` slug
  and `@pastlives.demo` emails, not the title).
- Rich, real-sounding description (what you make, what is provided, skill level).
- Hero + gallery images that also attach on a **prod** seed (R2 storage). Because the current
  `demo_seed` dir is local + gitignored + DEBUG-gated, the showcase image(s) come from a small
  committed asset set so a prod seed (DEBUG off on Render) can attach them through R2.
- Keep capacity = confirmed count (full) with 3 genuine waitlist rows.

## Prod seeding

Seed via a **Render one-off job** (`python manage.py demo_data`) so DEBUG is off and the
dangerous local-dev extras (global registration questions, ACTIVE demo member, orientation
config pushed onto real guilds) are skipped. Only the prod-safe block runs: personas, classes,
the enriched full class, discount codes. Verify with `demo_data --status` and by loading the
catalog. `demo_data --remove` cleans it all up after the demo.

## Tests

- `tests/hub/help_keys_spec.py` — new keys must round-trip (registry ↔ template).
- `core/spec/tours_spec.py` (or equivalent) — payload builds, resolvers drop gracefully.
- `tests/e2e/guided_tour_spec.py` — the member lap still drives to completion with the added step.
- `tests/template_comment_lint_spec.py` — single-line `{# #}` only.

## Out of scope

- No changes to the tour runtime (`static/js/pl_tour.js`) beyond what new step shapes require.
- No new DB models (tours are repo-authored dataclasses).
- General-info items the presenter narrates live (Discord command list, app-store links) are not
  in-app tour stops unless a natural anchor already exists.
