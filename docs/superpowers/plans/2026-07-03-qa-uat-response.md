# Release 0.20.x — FOG UAT response: overview, build sequence & backlog

**Branch:** `release-0.20.x` (draft PR #118). **VERSION stays `0.20.1`** (one PR = one version).
**Status:** planning + build. Everything below lands in PR #118 unless flagged "own release."
**Source:** Josh's FOG UAT feedback (2026-07-03), grounded in code by four scout passes.

Prod runs the **0.19.x** line; `main` is `0.19.17`. The 5 features already committed to `release-0.20.x`
(tab conformity, orientations Save-Hours + margin, Announcements/Emails tab, staff title badges, Discord
announce-current-version) are **done** and were the original PR #118 scope. This doc adds the UAT work on top.

## Shared engineering conventions (every builder on this branch follows these)

- **Do NOT edit `plfog/version.py`.** The CHANGELOG is curated centrally after commits land (VERSION stays `0.20.1`).
- **Sequential commits.** One coherent feature per commit; build/test/lint green before the next starts. Commit to
  `release-0.20.x` locally; do not push (Josh reviews + pushes). Stage only your own files (`git add <paths>` — never
  `git add -A`); never commit `send_test_dm.py` or unrelated `docs/…/plans/*.md`.
- **Migrations additive & non-destructive.** No backfills that could change existing members' explicit choices. Next
  `membership` migration is `0065` (verify with `ls membership/migrations/`). Run `ruff format` + `git add` new
  migrations together (CI runs `ruff format --check`).
- **Standards:** fat models / skinny views, forms own validation, `TextChoices` + `help_text`, full type hints, BDD
  `*_spec.py` tests to the repo coverage gate, FRONTEND.md components (never hand-rolled markup). See CLAUDE.md.
- **Running tests here:** `docker compose exec` mounts a *different* worktree — run pytest/mypy via
  `docker run -v /home/josh/Code/plfog:/app <plfog image> …` against this tree, or a local run. Confirm against repo memory.

---

## QA feedback → code, verdict, disposition

| # | QA item | Grounding (file:line) | Verdict | Disposition |
|---|---------|------------------------|---------|-------------|
| 3 | Profile edits wiped when photo too large | one ModelForm; oversized photo fails `is_valid()` so nothing saves — `hub/views.py:1333`, `hub/forms.py:246-259`, `core/validators.py:10`; photo-delete is a separate teleported form → reloads DB values | **Real bug, live on prod** | **Build C1** |
| 7 | Profiles should default to *listed* | `show_in_directory default=False` — `membership/models.py:343` | Mismatch w/ QA | **Build C1** (default→True, new members only, no backfill) |
| 4 | Auto-fill Discord info | `discord_handle` manual free-text; OAuth stores only numeric id — `core/events/discord_oauth.py:139`, `membership/models.py:420-429` | Missing | **Build C1** (fill blank handle on link) |
| 2 | First-login "customize your profile" prompt | none in hub; modal component + onboarding flag exist to reuse | Missing | **Build C2** |
| 12 | Rename "FAQ" per guild (e.g. "Ceramics Info") | "FAQ" hardcoded — `guild_detail.html:107,314`; no field | Missing | **Build C3** |
| 13 | FAQ hold PDFs / links / YouTube | PDFs ✅ + YouTube ✅ already; inline links ❌ (`answer` uses `linebreaksbr`, `guild_detail.html:319`); `guild_markdown` filter already exists | Partial | **Build C3** (answers → markdown) |
| 9 | Purpose/help blurb atop pages | Voting has it; Orientations has none; no reusable header component | Missing | **Build C4** (page_header component + rollout) |
| 1 | Landing page inconsistency | login→Calendar (`adapters.py:162`) vs `/`→Voting (`core/views.py:142`) | Bug (consistency) | **Build C5** (consistency); full home = **Spec A** |
| 6a | Theme flips light→dark across Discord hop | per-origin `localStorage` theme + per-surface default — `hub/base.html:13`, `classes/base_public.html:11` | Real UX bug | **Build C5** (cookie-scoped theme) |
| 6b | "profile went hidden" after Discord | NOT a Discord write (`link_discord` uses `update_fields`); it's #3 + #7 | Explained | resolved by C1 |
| 5 | "Connect Discord" not active | coded, gated on `DISCORD_CLIENT_ID/SECRET` absent from `render.yaml` | Config | **Ops** (Josh) |
| 11 | Prison Outreach has no lead | `guild_lead_id` NULL — data, not a bug; `set_guild_lead` / `audit_guild_leads` commands exist | Data | **Ops** (Josh) |
| 10 | "lead came" typo | `about` is guild-editable DB content; "lead came" is correct stained-glass term | Content/no-op | **Ops** (none likely) |
| 8 | Calendar shows classes not in catalog | external iCal events w/o `ClassOffering` (expected) + series-already-started asymmetry (`classes/models.py:136` vs `calendar_service.py:168`) | Mixed | **Spec C** |
| 15 | Org-info page (map, who's-who, code of conduct); replace "Member Guild" link | no "Member Guild" in code (prod data row); `guild_detail` machinery reusable; org info today = Google Docs in `base.html:262-292` | Feature | **Spec B** |
| 14 | Physical map (guilds, restrooms, exits) | nothing exists | Feature | **Spec B** (v1 = annotated floorplan image) |

## Build sequence (sequential commits on `release-0.20.x`)

- **C1 — Profile settings hardening** (#3 + #7 + #4). *In progress.*
- **C2 — First-login "customize your profile" welcome modal** (#2).
- **C3 — Guild FAQ: per-guild label + rich-text/link answers** (#12 + #13).
- **C4 — Page intro blurbs + reusable `page_header` component** (#9, incl. Orientations purpose text).
- **C5 — Cross-surface theme persistence** (#6a only). **NARROWED:** the landing-consistency half (#1) is
  handed to the member-home-dashboard spec (Spec A), which owns the two lines in `adapters.py` + `core/views.py`
  and repoints them at the new home. C5 keeps only the theme cookie fix to avoid double-editing those lines.
  (If Spec A is deferred and C5 ships first, C5 may set both entry points to the Calendar as an interim.)
- **C6 — Catalog/calendar consistency** (#8), from Spec C. Small, no migration; align the calendar's local-class
  sync to the catalog's `bookable()` gate + external-feed microcopy. Sequenced last.

## Specs written (parallel pass, 2026-07-03) — with disposition

- **Spec A** — `2026-07-03-member-home-dashboard.md` (#1 full). **Recommends its OWN release (`0.21.0`), not PR #118** —
  it changes every member's landing page and supersedes C5's landing half. *Josh's call (see Open decisions).*
- **Spec B** — `2026-07-03-org-info-page.md` (#15 + #14). Dedicated `OrgInfoPage` singleton (NOT an `is_org_info`
  guild flag — that pollutes voting/funding querysets) + v1 floorplan image. **Recommends its OWN PR**, not #118
  (3–4 models + migration + nav change + real content entry). Blocks on the "Member Guild" question below.
- **Spec C** — `2026-07-03-catalog-calendar-consistency.md` (#8). Two mechanisms: expected external-feed events
  (microcopy) + a real started-series asymmetry (align the calendar to `bookable()`). → shipped as **C6** above.
- **Guest Guilds Surface v2** — extended `2026-07-01-guest-guilds-surface.md` (§11–§16) with the per-guild public
  URL, the `pastlives.app/g/<slug>` **vanity redirect**, **`segno`** QR (SVG/PNG), and the standardized
  **print-to-PDF flyer** + new `Guild.essential_rules` field. Ships **with/after** the guest surface (Phase 7),
  its own effort — not this batch. Josh chose print-optimized page + vanity URL.

## Open decisions for Josh (from the specs)

1. **Home dashboard + Org-info release sizing** — both specs recommend their **own release** rather than cramming
   into PR #118. Default I'll follow unless told otherwise: build the C1–C6 quick wins into #118 now; treat the
   home dashboard (`0.21.0`) and org-info page as their own follow-up PRs. Say the word to fold them into #118.
2. **"Member Guild"** (#15) — the prod `Guild` row QA wants to replace. Need: its exact slug, and confirmation it's
   safe to retire (no in-flight voting/funding/membership dependence) so we can convert-and-301-redirect it.
3. **Prison Outreach lead** (#11) — who is it? (Then `set_guild_lead`.)
4. **Guest-guilds/flyer minor forks** — vanity path `/g/` vs `/guild/`; QR default SVG vs PNG; admin bulk-print now
   vs later. All have recommended defaults in the spec; no answer needed to proceed on defaults.

## Ops / Josh actions (not code builds)

- **#5** Enable Discord in prod: set `DISCORD_CLIENT_ID` + `DISCORD_CLIENT_SECRET` in Render, register the callback
  redirect URI on the Discord app (already on the DM-channel go-live list).
- **#11** Assign the Prison Outreach guild lead (`manage.py set_guild_lead`) — needs the person's identity from Josh.
- **#10** "lead came" — likely no change (correct term); guild lead can edit their own About if desired.
