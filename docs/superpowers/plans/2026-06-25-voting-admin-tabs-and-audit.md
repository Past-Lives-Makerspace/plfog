# Voting (admin) — Tabbed IA + Auditable Snapshots — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-25
**Surface:** FOG hub `pastlives.test` — the admin **Voting** surface (renamed from "Voting Dashboard"); routes under `/manage/voting/…`. Admin-only.
**Related:**
- **Spec 2 of 2** (written in parallel) — Voting *Settings + automation + emails*. Spec 1 (this doc) only leaves the hooks: a **Settings** tab shell, and a marked placeholder on **Overview** where Spec 2's "Results are in — review & send" banner will mount.
- Background: `docs/superpowers/plans/2026-04-09-funding-snapshot-overhaul.md` (raw_votes / analyzer rationale).
- Tab/IA precedent: `docs/superpowers/plans/2026-06-21-registrations-admin-tab.md`.

---

## 1. Summary

Today an admin's voting tooling is split across two surfaces with two different visual languages: the native hub **Voting Dashboard** (`/manage/voting/`, stats + leaders) and a **Snapshot Analyzer** living in jarring Django-admin chrome (`/admin/snapshots/…`, per-member audit + filters + take/delete). Spec 1 unifies them into a single **tabbed, native-hub "Voting" page** so an admin can, in one place: see the current cycle at a glance (**Overview**), browse and audit every past funding snapshot down to the individual vote (**Funding History**), watch the live vote state and commit a new snapshot with a dry-run preview (**Snapshots**), and reach a settings home that Spec 2 will fill (**Settings**). The per-member audit becomes a transparent, immutable record (driven by the snapshot's frozen `raw_votes`), filterable by *current* role and paying status, and snapshot deletion now also cleans up the Airtable mirror so deletions are honest end-to-end.

This is the **transparency half**. No automation, no emails, no settings content — those are Spec 2.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Surface name | Rename **"Voting Dashboard" → "Voting"** everywhere (page `<title>`, `<h1>`, sidebar label). |
| IA | One tabbed hub-admin page, native styling. Tabs in fixed order: **Overview · Funding History · Snapshots · Settings**. |
| Tab transport | **Server-rendered tab pages** (one route per tab), not Alpine client-side sections — because filters submit via GET (full reload) and Funding History drills into a per-snapshot detail URL. Tab nav = link-style `.vote-tab` anchors keyed off a server `active_tab` (the same `<a class="vote-tab">` pattern guild_edit uses for its "Meeting Notes"/"Orientations" tabs). |
| Filter dimensions | **Current roles only:** member type, FOG role, **guild-lead**, **guild-staff**, plus the tri-state **paying** filter. **"Former officers/leads" is dropped entirely** — no role history exists and none is built. |
| Snapshot delete | **Hard delete + Airtable cleanup.** `FundingSnapshot` currently has **no** `delete()` override and there is **no** `delete_snapshot_from_airtable` helper, so the snapshot's Airtable row is orphaned today. Spec 1 closes that gap. Behind a `confirm_modal.html` + `.hub-btn--sm .hub-btn--danger`. |
| "Recalc" | There is no recalc-in-place. Re-running the numbers = **take a NEW snapshot**. Existing snapshots are immutable. |
| Auth | Admin-only, reusing the existing `@fog_admin_required` gate on `admin_voting_dashboard`. No new permission. |
| `is_auto` badge | **Defer the persisted field to Spec 2.** Spec 1 ships the badge column/pill now, reading a graceful-degrade contract (see §4) that resolves to "Manual" until Spec 2 lands the real flag. |
| Member-facing history | **Keep `/guilds/voting/history/` untouched** (different audience; `publish_results()` deep-links members there). Not folded into the admin tabs. |
| Snapshot delete location | **Per-row Delete on the Funding History list (6.2)** *and* on the snapshot detail header (6.3) — same wiring, same outcome. Both → `confirm_modal.html` → POST `hub_admin_voting_snapshot_delete` → hard delete + Airtable → redirect to the Funding History list with a success message. The **Snapshots tab has no delete** (it shows live votes, not snapshots). |
| URL-name convention | **One prefix: `hub_admin_voting_*`** (extends the live `hub_admin_voting_dashboard`). Names: `…_overview`, `…_history`, `…_history_detail`, `…_snapshots`, `…_settings`, `…_snapshot_take`, `…_snapshot_delete`. The rename touches `hub/base.html:161` in **two** spots — the `{% url 'hub_admin_voting_dashboard' %}` href *and* the `{% active_nav 'hub_admin_voting_dashboard' %}` argument string — both → `hub_admin_voting_overview`. |
| Orphaned consumers of the retired `admin_snapshot_*` routes | **Repoint, don't break.** Removing `admin_snapshot_detail`/`draft` would 500 four admin surfaces. Repoint every consumer in `membership/admin.py` (×2) and `templates/admin/index.html` (×2) to the new hub routes — keep the affordances (see §3 + §5). |

> **Cross-spec lock (do not rename):** these names are authoritative for Spec 2 — templates `voting_overview.html`, `voting_history.html`, `voting_history_detail.html`, `voting_snapshots.html`, `voting_settings.html`, `_voting_tabs.html`; the `voting_settings` view at route `hub_admin_voting_settings` = `/manage/voting/settings/` is the shell Spec 2 extends.

---

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Overview stats (participation, paying, projected pool, leaders, last snapshot) | `dashboard_callback()` → `context["stats"]` | `plfog/dashboard.py:25` |
| Current cycle label / close date / next cycle | `get_cycle_context()` | `membership/cycle.py:11` |
| Live per-member vote rows (draft mode) | `_serialize_live_votes()` | `plfog/admin_views.py:113` |
| Filter rows by member_type / fog_role / paying | `_apply_filters()` + `_parse_is_paying()` | `plfog/admin_views.py:152`, `:143` |
| Minimum-pool parse + default | `_parse_minimum_pool()` + `DEFAULT_MINIMUM_POOL` | `plfog/admin_views.py:170`, `:110` |
| Shared draft/stored render + dry-run recalc | `_render_analyzer()` | `plfog/admin_views.py:183` |
| Take a snapshot (freezes raw_votes, runs calc, publishes) | `FundingSnapshot.take(title=, minimum_pool=)` | `membership/models.py:1394` |
| Immutable per-member audit record | `FundingSnapshot.raw_votes` (JSON) | `membership/models.py:1371` |
| Per-guild allocation math | `calculate_results()` → `{total_pool, contributed_pool, minimum_pool, total_points, votes_cast, results[]}` | `membership/vote_calculator.py:25` |
| Per-guild allocation text | `FundingSnapshot.allocation_summary()` | `membership/models.py:1471` |
| Snapshot list (history) | `FundingSnapshot.objects.order_by("-snapshot_at")` | used in `hub/views.py:286` |
| Member-is-a-lead / is-staff flags (for the two new filters) | `Member.is_guild_lead`, `Member.is_guild_staff` (properties) | `membership/models.py:514`, `:519` |
| Admin gate + hub context pattern | `@fog_admin_required` + `_get_hub_context(request)` | `hub/views.py:2022`, `hub/view_as.py` |
| Tab nav CSS (link/button) | `.vote-tab` / `.vote-tab--active` | `static/css/hub.css:7` |
| Buttons | `.hub-btn`, `.hub-btn--primary`, `.hub-btn--danger`, `.hub-btn--sm`, `.hub-btn--ghost` | `static/css/hub.css:904` |
| Theme input/surface tokens | `--hub-input-bg/-border`, `--hub-card-bg`, `--hub-surface`, `--hub-border`, `--hub-text(-muted)`, `--hub-link`, `--color-tuscan-yellow` | `static/css/hub.css:41–126` |
| Confirm dialog | `components/confirm_modal.html` | per FRONTEND.md |
| HTMX/full-page success feedback | Django `messages` (full-page POST→redirect) / `trigger_toast()` (HTMX) | `hub/toast.py` |
| Airtable **vote** delete (pattern to mirror) | `delete_vote_from_airtable()` + `VotePreference.delete()` override | `airtable_sync/service.py:174`, `membership/models.py:1328` |
| Airtable **snapshot** upsert | `sync_snapshot_to_airtable()` (uses `VOTING_SESSIONS_TABLE_ID`) | `airtable_sync/service.py:214` |
| Page-scoped CSS link pattern | `<link>` inside `{% block extra_head %}` (never an inline `<style>` — lint guard) | `templates/classes/public/register.html:7`; guard `scripts/check_no_inline_style_in_extra_head.py` |
| Member-admin "Historical Votes" links (consumer to repoint) | `_member_snapshot_rows()` → `reverse("admin_snapshot_detail", …)` | `membership/admin.py:349` |
| FundingSnapshot changelist "analyzer" link (consumer to repoint) | `FundingSnapshotAdmin.analyzer_link()` (in `list_display`) → `reverse("admin_snapshot_detail", …)` | `membership/admin.py:379` |
| Unfold admin dashboard buttons (consumers to repoint) | `{% url 'admin_snapshot_draft' %}` (Take Snapshot) + `{% url 'admin_snapshot_detail' … %}` (last-snapshot link, with inline `style="color:#EEB44B;"`) | `templates/admin/index.html:222`, `:236` |

### Gaps to close (kept minimal)

1. **Airtable cleanup on snapshot delete** — add `delete_snapshot_from_airtable(record_id)` to `airtable_sync/service.py` (mirror of `delete_vote_from_airtable`, but `get_table(VOTING_SESSIONS_TABLE_ID).delete(record_id)`), and a `FundingSnapshot.delete()` override that mirrors `VotePreference.delete()` (capture `airtable_record_id` → `super().delete()` → conditionally delete the AT row, honoring `_skip_airtable_sync`).
2. **Two new audit dimensions** — `raw_votes` rows don't carry guild-lead / guild-staff status yet. Add `is_guild_lead` and `is_guild_staff` keys to the freeze (`FundingSnapshot.take()`) and the live serializer (`_serialize_live_votes()` → moves to `membership/vote_analyzer.py`). JSONField → **no migration**. Stored snapshots predating this read `.get(key, False)` and simply never match those two filters (graceful).
3. **Extract the analyzer logic out of `plfog/admin_views.py`** so the hub views stay thin and the code stays inside coverage scope (it leaves the Django-admin module entirely). New pure-function module `membership/vote_analyzer.py` (see §3).
4. **`is_auto` badge contract** — see §4 (a read-only stub, no migration).
5. **Repoint the four orphaned consumers (BLOCKERS)** — removing the `admin_snapshot_*` routes without this 500s the FundingSnapshot changelist, every Member admin page with vote history, and the Unfold dashboard home. Repoint `membership/admin.py` (×2) and `templates/admin/index.html` (×2) — detailed in §3 + §5. There is **no compatibility redirect** for the old `/admin/snapshots/*` URLs; they simply 404 (acceptable — admin-chrome-only, no external links).

---

## 3. Where the code lives

```
membership/
  vote_analyzer.py            NEW — pure helpers lifted from plfog/admin_views.py:
                                serialize_live_votes(), apply_filters(), parse_is_paying(),
                                parse_minimum_pool(), build_analyzer_context(raw_votes, *, snapshot, GET)
                                (+ the two new is_guild_lead/is_guild_staff keys)
  models.py                   FundingSnapshot.take() freeze gains 2 keys; +FundingSnapshot.delete()
                                override (Airtable cleanup); + read-only `source_label` stub property
  admin.py                    REPOINT (BLOCKER 1) — _member_snapshot_rows() (~:349) and
                                FundingSnapshotAdmin.analyzer_link() (~:379): both
                                reverse("admin_snapshot_detail", …) → reverse("hub_admin_voting_history_detail", …)
airtable_sync/
  service.py                  NEW delete_snapshot_from_airtable(record_id)

hub/
  views.py                    NEW thin views (all @fog_admin_required):
                                voting_overview, voting_history, voting_history_detail,
                                voting_snapshots, voting_settings, voting_snapshot_take (POST),
                                voting_snapshot_delete (POST). Rename old admin_voting_dashboard → voting_overview.
  urls.py                     NEW routes (name → path), all prefixed hub_admin_voting_:
                                _overview /manage/voting/ (rename, keep path)
                                _history /manage/voting/history/
                                _history_detail /manage/voting/history/<pk>/
                                _snapshots /manage/voting/snapshots/
                                _settings /manage/voting/settings/
                                _snapshot_take /manage/voting/snapshots/take/ (POST)
                                _snapshot_delete /manage/voting/history/<pk>/delete/ (POST)

templates/hub/admin/
  _voting_tabs.html           NEW — shared tab-nav partial (.pl-vote-tabs > .vote-tab anchors keyed off active_tab)
  voting_overview.html        NEW — re-skin of voting_dashboard.html (was inline-styled), + Spec 2 banner placeholder
  voting_history.html         NEW — snapshot list (cycle/date/pool/source badge) + per-row View + per-row Delete
  voting_history_detail.html  NEW — stored-mode analyzer (immutable audit), native, + header Delete
  voting_snapshots.html       NEW — draft-mode analyzer (live audit) + dry-run + Take (NO delete here)
  voting_settings.html        NEW — empty shell ("Voting settings live here") — Spec 2 fills it
  (voting_dashboard.html      DELETE after voting_overview.html replaces it)

templates/hub/base.html       Sidebar: label "Voting Dashboard" → "Voting"; both the {% url %} href
                                AND the {% active_nav %} arg string → hub_admin_voting_overview (line ~161/166)

static/css/
  voting-admin.css            NEW — all voting-admin styles, theme tokens only, pl- prefix
  unfold-custom.css           ADD a small `.pl-admin-snapshot-link` class (replaces the inline
                                style="color:#EEB44B;" the admin index uses on its snapshot link)

templates/admin/
  snapshot_analyzer.html      DELETE (the Django-admin-chrome page is retired)
  index.html                  REPOINT (BLOCKER 2) — {% url 'admin_snapshot_draft' %} (~:222) →
                                'hub_admin_voting_snapshots' (the tab that holds the Take form);
                                {% url 'admin_snapshot_detail' stats.last_snapshot.pk %} (~:236) →
                                'hub_admin_voting_history_detail'; drop the inline color style → class.

plfog/
  admin_views.py              REMOVE snapshot_draft/detail/take/delete + the 5 helpers (moved)
  urls.py                     REMOVE admin_snapshot_draft/take/detail/delete routes (no redirect; 404 is fine)
  version.py                  VERSION bump + member-friendly CHANGELOG (BUILD time, not now)
```

Member-facing `hub/views.py:snapshot_history` / `snapshot_detail` and their templates/routes are **unchanged**.

---

## 4. Data model

**No new models. No migrations.** The audit reuses `FundingSnapshot.raw_votes`. Only three light touches:

1. **`raw_votes` shape** gains two boolean keys per row — `is_guild_lead`, `is_guild_staff` — written in both `FundingSnapshot.take()` and the live serializer. It's a JSONField, so this is a serialization change, not a schema change. Consumers read `row.get("is_guild_lead", False)` / `row.get("is_guild_staff", False)` so legacy rows degrade.

2. **`FundingSnapshot.delete()`** — new override (currently inherited):
   ```python
   def delete(self, *args, **kwargs):
       record_id = self.airtable_record_id
       result = super().delete(*args, **kwargs)
       if record_id and not getattr(self, "_skip_airtable_sync", False):
           from airtable_sync.service import delete_snapshot_from_airtable
           delete_snapshot_from_airtable(record_id)
       return result
   ```
   Direct mirror of `VotePreference.delete()` (`membership/models.py:1328`). The autouse test fixture already sets `AIRTABLE_SYNC_ENABLED=False`, so specs don't hit the network.

3. **`source_label` (read-only property, recommended)** — the graceful-degrade contract for the auto/manual badge:
   ```python
   @property
   def source_label(self) -> str:
       """How this snapshot was created. Spec 2 makes this branch on a real `is_auto` field."""
       return "Manual"
   ```
   The Funding History and Snapshots templates render the pill from `{{ snapshot.source_label }}`. Spec 2 adds the persisted `is_auto` field and rewrites this property to return `"Automatic"`/`"Manual"`, plus the `.pl-vote-badge--auto` pill variant. **Recommendation:** ship the stub property (one line, zero migration) over the zero-touch `{{ snapshot.is_auto|yesno:"Automatic,Manual" }}` template trick — the property is an explicit contract instead of relying on Django's silent missing-attribute fallback.

No `Member`/`Guild` changes — `Member.is_guild_lead` / `is_guild_staff` already exist.

---

## 5. Business logic (fat models / pure helpers — views stay thin)

**`membership/vote_analyzer.py` (new, pure functions — easy to unit-test, in coverage scope):**

- `serialize_live_votes() -> list[dict]` — moved verbatim from `_serialize_live_votes`, **plus** `is_guild_lead` / `is_guild_staff` per row (from the member properties). Still scoped to signed-up voters via `VotePreference.objects.from_signed_up_members()`.
- `parse_is_paying(value) -> bool | None` — moved.
- `parse_minimum_pool(raw, default=DEFAULT_MINIMUM_POOL) -> Decimal` — moved.
- `apply_filters(rows, *, member_types, fog_roles, is_paying, is_guild_lead, is_guild_staff) -> list[dict]` — extends `_apply_filters` with the two new booleans (each `None` = "don't filter"; `True` = "only those who are"). Pure in-memory, no DB.
- `build_analyzer_context(raw_votes, *, snapshot, get_params) -> dict` — moved from `_render_analyzer`, minus `admin.site.each_context()` (we're in the hub now). Returns the filtered rows, the dry-run `calculate_results(...)` dict, paying/non-paying counts, `filter_state`, the choices lists, and the legacy flag. Shared by `voting_history_detail` (stored) and `voting_snapshots` (draft).

**`FundingSnapshot` (fat model — `membership/models.py`):**
- `take()` keeps owning the commit (freeze → calc → create → `publish_results()`); only its `raw_votes` dict literal grows the two keys.
- `delete()` (new) owns Airtable cleanup (§4).
- The hub views never compute — they fetch, call the analyzer/model, and render.

**Domain note:** `take()` already returns `None` when there are no votes; the Snapshots view turns that into the "No votes yet — nothing to snapshot" empty/guard path rather than a crash.

**Admin glue — repoint the orphaned consumers (BLOCKERS, no logic change):** the retired `admin_snapshot_*` routes are referenced in four spots that must move to the new hub names *in the same change* that removes the routes, or each 500s:
- `membership/admin.py` `_member_snapshot_rows()` (~:349) and `FundingSnapshotAdmin.analyzer_link()` (~:379): `reverse("admin_snapshot_detail", args=[pk])` → `reverse("hub_admin_voting_history_detail", args=[pk])`. Both keep working — the Member-admin "Historical Votes" rows and the changelist analyzer link now open the native hub detail (still admin-gated). **Call: repoint, not drop** — these are useful jumps and removing them loses functionality for no gain.
- `templates/admin/index.html` (~:222) `{% url 'admin_snapshot_draft' %}` → `{% url 'hub_admin_voting_snapshots' %}` (the Snapshots tab holds the Take form); (~:236) `{% url 'admin_snapshot_detail' stats.last_snapshot.pk %}` → `{% url 'hub_admin_voting_history_detail' stats.last_snapshot.pk %}`; replace that link's inline `style="color:#EEB44B;"` with a `.pl-admin-snapshot-link` class in `unfold-custom.css` (admin's CSS file). The admin index's *own* `<style>` block (other `#EEB44B` uses) is pre-existing admin chrome, **out of scope** for this re-skin — only the one inline `style=` attribute the route-repoint touches is cleaned up.

---

## 6. UI / UX  ← completeness checklist applied per screen

**Global shell (all four tabs):**
- **Container:** each tab is its own page extending `hub/base.html`, content in `<div class="hub-card">` blocks. Page `<title>` = "Voting — Past Lives"; `<h1 class="hub-page-title">Voting</h1>`.
- **Tab nav:** `templates/hub/admin/_voting_tabs.html`, included at the top of each tab page. A container `<nav class="pl-vote-tabs">` (the flex row — `display:flex; flex-wrap:wrap; border-bottom:1px solid var(--hub-border)`, defined in `voting-admin.css`, **not inline** — `.vote-tab` itself only styles the item) holding `<a class="vote-tab">` anchors — **Overview / Funding History / Snapshots / Settings** — the active one also gets `vote-tab--active`, decided by the server `active_tab` context value (e.g. `class="vote-tab {% if active_tab == 'history' %}vote-tab--active{% endif %}"`). The `.vote-tab` anchor needs `text-decoration:none` added in `voting-admin.css` (the shared `.vote-tab` rule in hub.css has none, and guild_edit only escapes this by inlining it — we don't inline). Anchors are natively keyboard-focusable/activatable (Tab + Enter) and screen-reader-legible; `aria-current="page"` on the active one. **Mobile:** `flex-wrap:wrap` lets tabs wrap to a second line — **no horizontal scroll**, no fixed widths. (We use links, not Alpine `x-show`, specifically because the filter forms submit via GET and Funding-History drills into a sub-URL; Alpine-only state would reset on each reload — same reason guild_edit makes its cross-page tabs anchors.)
- **Sidebar:** `templates/hub/base.html` (line ~166) label "Voting Dashboard" → **"Voting"**; the link (line ~161) updates in **two** spots — the `{% url 'hub_admin_voting_dashboard' %}` href *and* the `{% active_nav 'hub_admin_voting_dashboard' %}` argument string — both → `hub_admin_voting_overview` (the renamed `/manage/voting/`). `active_nav` then highlights the item for every `/manage/voting/…` sub-route.
- **Styling:** new `static/css/voting-admin.css`, linked via `{% block extra_head %}<link rel="stylesheet" href="{% static 'css/voting-admin.css' %}">{% endblock %}` on each tab page. **No inline `<style>` in extra_head** (the `check_no_inline_style_in_extra_head.py` guard blocks new offenders) and **no inline `style=` colors**. Every color comes from hub theme tokens — this is the central cleanup: the current `voting_dashboard.html` hardcodes `#EEB44B`, `#96ACBB`, `rgba(255,255,255,…)` and the analyzer hardcodes `#092E4C`/`#0a1929`. Replacements: accents → `--color-tuscan-yellow` / `--hub-link`; muted text → `--hub-text-muted`; body text → `--hub-text`; tiles/stripes → `--hub-surface`; cards → `--hub-card-bg`; borders → `--hub-border`. New classes use the `pl-` prefix (e.g. `.pl-vote-tabs`, `.pl-vote-stats`, `.pl-vote-stat`, `.pl-vote-leaders`, `.pl-vote-bar`, `.pl-vote-table`, `.pl-vote-table-scroll`, `.pl-vote-filters`, `.pl-vote-filters__actions`, `.pl-vote-take`, `.pl-vote-detail-actions`, `.pl-vote-badge`, `.pl-vote-badge--paying`, `.pl-vote-badge--manual`). **Verify both Obsidian (dark) and Slate (light).**
- **Action-button margins (the canonical failure class):** these aren't formset rows, so they don't inherit any auto top-margin — each gets an explicit 8px-grid top margin in `voting-admin.css` so it clears the field/content above it: `.pl-vote-filters__actions { margin-top:1rem; }` (the Apply/Clear row under the filter controls), `.pl-vote-detail-actions { margin-top:1rem; }` (the header Delete/Back row), and `.pl-vote-take button { margin-top:1rem; }` (the Take-snapshot submit under the Title/Minimum inputs). No button sits flush against the input above it.

### 6.1 Overview tab — `templates/hub/admin/voting_overview.html` (landing, `/manage/voting/`)

- **Content:** today's `voting_dashboard.html`, re-skinned to tokens — the two 3-up stat grids (members-with-votes, active members, participation %, paying voters, active guilds, projected pool incl. the floor-applied note), the **Current vote leaders** bar chart (guild logo + medal + points + 1st/2nd/3rd + proportional bar), and a current-cycle line (`get_cycle_context()` → "Cycle: June 2026 · closes June 30, 2026").
- **Components/data:** `dashboard_callback()` + `get_cycle_context()`; bars are `.pl-vote-bar` with `width:{{ guild.bar_pct }}%` (a width % is the one allowed inline nudge — color/gradient live in the class).
- **Controls:** the old page's "Take Snapshot" / "View Funding History" buttons are **removed** — those live in the tabs now. Overview is read-only.
- **Spec 2 hook:** a clearly-marked placeholder comment immediately under the `<h1>`, above the stat grids:
  ```django
  {# SPEC 2 HOOK: "Results are in — review & send" banner mounts here.
     Spec 1 renders nothing. Do not build the banner in this spec. #}
  ```
- **States:** **Empty** — when `stats.total_voters == 0`, leaders block is hidden and a muted "No votes cast this cycle yet." line shows; stat tiles still render (zeros). **Loading/Error** — plain server-rendered page, no async; nothing to spin. **Success** — n/a (read-only).
- **Mobile:** stat grids collapse `repeat(3,1fr)` → 1 column under ~640px (media query in the CSS, not inline); bars are full-width and reflow naturally.

### 6.2 Funding History tab — list — `templates/hub/admin/voting_history.html` (`/manage/voting/history/`)

- **Content:** one `.hub-card` with a table of past snapshots, newest first (`FundingSnapshot.objects.order_by("-snapshot_at")`): **Cycle · Date · Pool · Source · (actions)**. **Source** = a `.pl-vote-badge` pill rendering `{{ snapshot.source_label }}` ("Manual" in Spec 1; Spec 2 adds the "Automatic" variant).
- **Row actions (the actions column):**
  - **View audit** — each row's Cycle (and a "View" link) opens the detail (`/manage/voting/history/<pk>/`).
  - **Delete (per-row, the user's requested control)** — a `.hub-btn--sm .hub-btn--danger` button: `@click="$dispatch('open-confirm','del-snapshot-{{ snapshot.pk }}')"` (the verified `confirm_modal.html` trigger — `confirm_modal.html` listens for `open-confirm` with its `confirm_id`). One `{% include "components/confirm_modal.html" %}` per row, with `confirm_id="del-snapshot-{{ snapshot.pk }}"`, `confirm_title="Delete this snapshot?"`, `confirm_message` naming the cycle and stating it also removes the Airtable mirror and cannot be undone, `confirm_action_url={% url 'hub_admin_voting_snapshot_delete' snapshot.pk %}`, `confirm_button_text="Delete Snapshot"`. (Pattern precedent: the per-row `del-staff-{{ sm.pk }}` modals in `guild_edit.html`.)
- **After delete:** the POST hard-deletes the row + its Airtable record, then **redirects back to this Funding History list** (`hub_admin_voting_history`) with `messages.success("Deleted snapshot '<cycle>'.")` — the admin lands where they were, minus the row.
- **States:** **Empty** — "No funding snapshots yet. Take your first one from the **Snapshots** tab." with an inline link to the Snapshots tab (no dead end). **Loading/Error** — server-rendered.
- **Mobile:** wrap the table in `.pl-vote-table-scroll` (a contained `overflow-x:auto` region) so it scrolls within the card instead of blowing out the viewport. The danger button is a real tap target, not an icon.

### 6.3 Funding History detail — `templates/hub/admin/voting_history_detail.html` (`/manage/voting/history/<pk>/`)

The **stored-mode** analyzer, re-skinned native — the immutable audit for one snapshot.

- **Header (actions row `.pl-vote-detail-actions`, `margin-top:1rem` so it clears the title block above):** cycle label, "Taken {{ snapshot.snapshot_at }}", `${{ snapshot.funding_pool }}` pool, `{{ snapshot.contributor_count }}` paying contributors, the Source pill, plus two controls — a **Delete** button (`.hub-btn--sm .hub-btn--danger`, `@click="$dispatch('open-confirm','del-snapshot-{{ snapshot.pk }}')"`, with one `{% include "components/confirm_modal.html" %}` → `confirm_id="del-snapshot-{{ snapshot.pk }}"`, message naming the cycle and that it removes the Airtable mirror + cannot be undone, `confirm_action_url={% url 'hub_admin_voting_snapshot_delete' snapshot.pk %}`, `confirm_button_text="Delete Snapshot"`) and a **"Back to Funding History"** link (`.hub-btn--ghost`). Same delete wiring and the same outcome as the per-row delete in 6.2 — **after delete the POST redirects to the Funding History list** (`hub_admin_voting_history`) with the success message (you came from a row that no longer exists, so landing on the list is correct, not a dead detail URL).
- **Summary card:** voters included (paying / non-paying), contributed pool, minimum floor, total pool — from the recomputed `build_analyzer_context` over the snapshot's `raw_votes` (filters re-run client-of-server-side for *analysis*; the stored totals are immutable).
- **Filters card (the filter form):** a single GET `<form>` (Apply submits, reloads this same URL with query params; that's how filter state persists across the tab). Real controls grouped:
  - **Member type** — checkbox group (multi-select), `Member.MemberType.choices`.
  - **FOG role** — checkbox group, `Member.FogRole.choices`.
  - **Guild role** — two checkboxes: "Guild leads only", "Guild staff only" (each is a *narrowing* boolean; unchecked = don't constrain). Honestly: these match against the frozen `is_guild_lead`/`is_guild_staff`; on pre-Spec-1 snapshots they match nothing (a muted hint says "Lead/staff tags weren't recorded on older snapshots").
  - **Paying** — described honestly as a **tri-state**, not a toggle: a 3-radio group **Both (default) / Paying only / Non-paying only** (`is_paying` → `""`/`"yes"`/`"no"`). A toggle can't express "either", so it stays radios.
  - **Apply / Clear row** = a `.pl-vote-filters__actions` wrapper (`margin-top:1rem`, clearing the filter controls above): **Apply** = `.hub-btn--primary` (submit); **Clear filters** = a `.hub-btn--ghost` link back to the bare detail URL (the affordance to reset).
  - Controls live in `.hub-form-group` wrappers (or `.pl-vote-filters select/input` styled to `--hub-input-bg`/`--hub-input-border`/`--text`, with `select option { background; color }`) so nothing renders as a white box on dark. No native date/time inputs here.
- **Per-guild allocation table:** Guild · 1st · 2nd · 3rd · Points · Share % · Funding (from `calc.results`).
- **Per-member audit table:** Member · Member type · FOG role · Paying? · 1st · 2nd · 3rd (paying rendered as `.pl-vote-badge--paying` / muted pill). This is the immutable transparency record.
- **States:** **Empty (filtered)** — when filters exclude everyone, both tables show "No votes match these filters." with the Clear-filters link beside it (not a blank region). **Legacy snapshot** (empty `raw_votes`) — a `.pl-vote-banner` explains per-vote history predates this snapshot; filters + member table are hidden, only the stored totals show (mirrors today's `is_legacy` path). **Loading/Error** — server-rendered; a bad pk → 404 via `get_object_or_404`.
- **Mobile:** both tables wrapped in `.pl-vote-table-scroll`. **Decision:** the 7-column audit table uses a **horizontal-scroll container, not stacked cards** — the data is dense and column alignment is the point of an audit; a contained scroll keeps it readable without breaking the viewport. The filter grid collapses `1fr 1fr 1fr` → 1 column under ~768px.

### 6.4 Snapshots tab — `templates/hub/admin/voting_snapshots.html` (`/manage/voting/snapshots/`)

The **draft-mode** analyzer, re-skinned native — "who's voting what, right now" + dry-run + commit. (No delete here — see below.)

- **Same analyzer body as 6.3** (summary, the identical filters card with persistence + Clear, per-guild allocation, per-member audit) but run over **live** `serialize_live_votes()` — so it's the dry-run preview (live recalc + paying/non-paying counts) before committing. Filters here are analysis-only; the commit always captures the full unfiltered live state (existing behavior, kept).
- **Take Snapshot (the primary action):** an inline `<form method="post" action="{% url 'hub_admin_voting_snapshot_take' %}">` (class `.pl-vote-take`) in its own `.hub-card` at the top:
  - **Title** — `<input type="text" name="title">` defaulting to the current "Month Year" (`timezone.now().strftime("%B %Y")`), wrapped in `.hub-form-group`.
  - **Minimum pool $** — `<input type="number" name="minimum_pool" min="0" step="1">` defaulting to `DEFAULT_MINIMUM_POOL` ($1,000), wrapped in `.hub-form-group`.
  - **Take Snapshot** = `.hub-btn--primary` submit, given `margin-top:1rem` (via `.pl-vote-take button`) so it clears the Minimum-pool input above it. On success: `messages.success("Snapshot '<cycle>' created — $<pool> pool.")` then **redirect to the new snapshot's detail** (`hub_admin_voting_history_detail`) so the admin lands on the immutable record they just made. Full-page POST → Django messages (not a toast) per the interaction table.
  - **Empty/guard:** if `take()` returns `None` (no votes), `messages.warning("No votes yet — nothing to snapshot.")` and redirect back to Snapshots. The Take card also shows a muted **"No votes cast this cycle yet — nothing to snapshot."** line when the live set is empty, and the audit tables show their empty state.
- **No delete on this tab.** The per-member rows here are members' *live standing votes*, owned by the member — they are not admin-deletable. Snapshot deletion lives on the Funding History list (6.2) and the snapshot detail (6.3), because that's where the rows *are* snapshots. (There is deliberately no per-live-vote delete anywhere in this surface.)
- **States:** **Unfiltered-empty (no votes at all)** — Take card shows the guarded **"No votes cast this cycle yet — nothing to snapshot."** line and the audit tables show the same; **Filtered-empty (votes exist but none match)** — a distinct **"No votes match these filters."** + the Clear link. (Two different strings so the admin knows whether the cycle is empty vs. their filter is too narrow.) **Error** — bad `minimum_pool` falls back to the default via `parse_minimum_pool` (no 500); a message shows only on the no-votes guard. **Success** — see redirect above.
- **Dark/light + mobile:** identical token + `.pl-vote-table-scroll` + filter-grid-collapse rules as 6.3. The Take inputs are number/text (no date/time picker), styled via `.hub-form-group`.

### 6.5 Settings tab — `templates/hub/admin/voting_settings.html` (`/manage/voting/settings/`)

- **Spec 1 = empty shell only.** One `.hub-card` with the tab nav and a single muted line: **"Voting settings live here. Configuration is coming soon."** No form, no controls.
- **Spec 2 hook:** a comment — `{# SPEC 2 owns this tab's content (cycle cadence, floor defaults, auto-snapshot toggle, email settings). #}`
- **States:** static; nothing to empty/load/error. Renders for admins only (same gate).

---

## 7. Notifications / emails / activity

**None in Spec 1.** `FundingSnapshot.take()` already calls `publish_results()` (emits `voting.results_published` + logs `FUNDING_SNAPSHOT_TAKEN`); Spec 1 doesn't change that path — taking a snapshot from the new Snapshots tab fires the same existing notification. No new triggers, audiences, or templates. (All email/automation work is Spec 2.)

---

## 8. Build order (phased; each phase ships green)

1. **Extract + harden the model/helpers (no UI).** Create `membership/vote_analyzer.py` (move the 5 helpers, add the two role booleans + the two filter args). Add `FundingSnapshot.delete()` override + `source_label` property + the two `take()` freeze keys. Add `delete_snapshot_from_airtable()` to `airtable_sync/service.py`. Update/move the existing `tests/plfog/snapshot_analyzer_spec.py` expectations to the new module. *Green: full suite + lint + mypy.*
2. **Hub views + routes + repoint consumers (atomic).** Add the seven `@fog_admin_required` views to `hub/views.py` (rename `admin_voting_dashboard` → `voting_overview`); add the `hub_admin_voting_*` routes to `hub/urls.py`. **In the same commit** that removes `snapshot_*` from `plfog/admin_views.py` and `admin_snapshot_*` from `plfog/urls.py`, repoint the four consumers — `membership/admin.py` (×2) and `templates/admin/index.html` (×2) — to the new hub names, or the suite 500s. *Green (the admin-links regression spec from §9 proves it).*
3. **Templates + CSS.** Build the four tab templates + `_voting_tabs.html`, the re-skinned overview, and `static/css/voting-admin.css` (tokens only, `pl-` prefix). Delete `templates/hub/admin/voting_dashboard.html` and `templates/admin/snapshot_analyzer.html`. Rename the sidebar label. *Green + manual dark/light check on `pastlives.test:8000`.*
4. **Housekeeping.** Confirm `check_no_inline_style_in_extra_head.py` passes; run `ruff format .` + `ruff check .` + scoped `mypy`. Bump `plfog/version.py` VERSION + a member-friendly CHANGELOG entry (at BUILD time).

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under `tests/`, `describe_*`/`it_*` (never `context_*` — not collected), factory-boy (`GuildFactory`, `GuildStaffMembershipFactory`, `VotePreferenceFactory`, `FundingSnapshotFactory`, `MemberFactory` all exist in `tests/membership/factories.py`), ≥98% coverage gate, run in the `plfog-web` Docker image (SQLite, mirrors CI), `--no-cov` for subsets.

- **`tests/membership/vote_analyzer_spec.py` (new):**
  - `describe_serialize_live_votes` → `it_only_includes_signed_up_members`; `it_tags_guild_leads_and_staff` (a lead row has `is_guild_lead=True`; a staffed member `is_guild_staff=True`).
  - `describe_apply_filters` → `it_filters_by_member_type`; `it_filters_by_fog_role`; `it_filters_by_paying_tristate` (yes/no/both); `it_filters_guild_leads_only`; `it_filters_guild_staff_only`; `it_returns_empty_when_nothing_matches`.
  - `describe_parse_minimum_pool` → `it_falls_back_on_blank_or_invalid_or_negative`.
- **`tests/membership/take_funding_snapshot_spec.py` (extend) + a snapshot-delete spec:**
  - `it_freezes_guild_lead_and_staff_flags_in_raw_votes`.
  - `describe_delete` → `it_hard_deletes_the_row`; `it_deletes_the_airtable_record_when_record_id_present` (assert `delete_snapshot_from_airtable` called via mock/respx); `it_skips_airtable_when_no_record_id`; `it_skips_airtable_when_sync_disabled`.
  - `it_source_label_is_manual` (the Spec 1 contract).
- **`tests/hub/voting_admin_spec.py` (new — views/templates/gating):**
  - `describe_gating` → `it_requires_fog_admin` for **every** route (overview/history/detail/snapshots/settings/take/delete) — a plain member gets redirected/403, never a 200.
  - `describe_overview` → `it_renders_stats_and_leaders`; `it_shows_empty_state_with_no_votes`; `it_includes_the_spec2_banner_placeholder_comment`.
  - `describe_history` → `it_lists_snapshots_newest_first`; `it_shows_empty_state_with_link_to_snapshots`; `it_renders_the_source_badge`.
  - `describe_history` (cont.) → `it_renders_a_per_row_delete_button_per_snapshot`.
  - `describe_history_detail` → `it_renders_immutable_audit_from_raw_votes`; `it_applies_filters_via_get`; `it_shows_no_match_empty_state`; `it_shows_legacy_banner_for_empty_raw_votes`; `it_404s_unknown_pk` (raw Django 404 — fine for an admin tool); `it_renders_a_header_delete_button`.
  - `describe_snapshots` → `it_renders_live_audit_and_dry_run_totals`; `it_take_creates_snapshot_and_redirects_to_detail`; `it_take_with_no_votes_warns_and_stays`; `it_take_uses_title_and_minimum_pool`; `it_shows_no_votes_cast_message_when_empty` (the unfiltered-empty string); `it_shows_no_match_message_when_filtered_empty` (distinct from the no-votes string); `it_has_no_delete_controls`.
  - `describe_delete` → `it_deletes_and_redirects_to_history_with_message` (from both the list and the detail entry points); `it_removes_the_airtable_record`; `it_is_post_only`.
- **`tests/membership/admin_voting_links_spec.py` (new — blocker regression guard):** `it_member_admin_change_page_loads_with_vote_history` (the Member admin renders the "Historical Votes" rows with the repointed `hub_admin_voting_history_detail` URL — no `NoReverseMatch`); `it_fundingsnapshot_changelist_loads` (the `analyzer_link` column resolves); plus a render of `templates/admin/index.html` asserting both repointed `{% url %}` tags resolve (no `NoReverseMatch` on the admin dashboard home). Also assert the **old** `admin_snapshot_*` names no longer reverse (the routes are gone, 404 expected).
- **Migrate** `tests/plfog/snapshot_analyzer_spec.py` cases into the two new specs and delete the file (the analyzer module it covered is gone).
- **Gotchas:** seed a `MembershipPlan` before any member-gated login (signal skips Member creation otherwise — known fresh-Postgres/CI failure mode); the autouse `_disable_airtable_sync` fixture means delete-Airtable assertions must mock the service function (or flip `_skip_airtable_sync` off deliberately). `cycle.py` is month-boundary sensitive — freeze time if asserting the cycle label.
- **Manual** on `pastlives.test:8000` (never localhost): all four tabs render in dark **and** light; filters persist across Apply and Clear resets them; Take redirects to the new detail; Delete confirms then returns to history; the audit table scrolls within its card on a phone width; tabs wrap (no horizontal scroll).

## 10. Open / deferred

- **Out of scope — Spec 2:** the Settings tab's actual content (cycle cadence, floor defaults, auto-snapshot toggle), the `is_auto` persisted field + "Automatic" badge variant, all automation, and all emails (beyond the existing `publish_results()` call). Spec 1 only leaves the Settings shell + the Overview banner placeholder.
- **Out of scope — former-role filtering.** Filtering by *past* officers/leads is intentionally excluded: no role history is stored and none is being built. Only current member type / FOG role / guild-lead / guild-staff / paying are filterable.
- **Out of scope — voting-window / lockout state machine.** Soft close stands; no open/closed/locked states.
- **Out of scope — member-facing redesign.** `/guilds/voting/history/` and its detail stay as-is (kept because `publish_results()` deep-links members there).
- **No per-live-vote deletion** in the Snapshots tab — a member's standing vote is theirs; admins delete *snapshots*, not live rows.
- **No compatibility redirect for the old `/admin/snapshots/*` URLs** — they were admin-chrome-only with no external links, so they simply 404 after removal (acceptable). All in-app references are repointed (§3/§5); a bookmark to an old URL is the only thing that breaks, and an admin can re-navigate via the sidebar.
- **A deleted-snapshot detail URL returns a raw Django 404** (via `get_object_or_404`) — acceptable for an admin tool; no custom not-found screen is built.
- **Versioning** — the `plfog/version.py` VERSION bump + member-friendly CHANGELOG entry happen at **build** time, not in this spec.
