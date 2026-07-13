# Automations Dashboard (Site Settings) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — Site Settings (`/manage/site-settings/`, `hub_admin_site_settings`), new **Automations** tab; small cross-link callout on the existing **Legacy CMS** tab. Fog-admin only.
**Related:** `hub/calendar_service.py::sync_all_sources`, `core/management/commands/run_scheduled_tasks.py`, the Legacy CMS "Sync Now" pattern already on this page.

---

## 1. Summary

Right now the nightly/every-15-minutes background jobs are invisible: they live as two hard-coded tuples inside `run_scheduled_tasks.py`, they self-gate silently in code, only the legacy-CMS pull tracks a last-run time, and the only way to know whether "Sync offerings from legacy CMS" is on is to remember what an unchecked checkbox three tabs over means. A fog admin has no single place to see *what runs, when it last ran, whether it worked, and how to turn it off or kick it now.*

This feature adds an **Automations** tab to Site Settings: a plain-English list of every scheduled job with its schedule, its last-run time + status (with a clear "never run" state), an **ON/OFF toggle**, and a **Run now** button. It also folds the recurring confusion about the legacy CMS pull directly into that list — the "Sync offerings from legacy CMS" flag appears as a labelled automation that says in words "OFF right now — the nightly pull from classes.pastlives.space is not running."

Everything is driven by **one job registry** that both the dispatcher and the settings page read, so the list can never drift out of sync with what actually runs.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Where the list of jobs comes from | A declared **job registry** (`core/scheduled_jobs.py`, frozen dataclasses). The dispatcher iterates it instead of hard-coded tuples; the settings page renders it. One source of truth. |
| Where last-run/status lives | A new **`ScheduledTaskRun`** model (append-only history). The dispatcher, the standalone crons, and "Run now" all record through **one shared helper** so recording is uniform. |
| Where per-job ON/OFF lives | A new tiny **`ScheduledJobState`** model keyed by job. **Absence of a row = enabled**, so a fresh DB preserves today's "everything runs" behavior with zero seeding. |
| How the dispatcher honors ON/OFF | Each due job is skipped when `is_enabled(key)` is false. Scheduled invocation is otherwise byte-for-byte what runs today. |
| How "Run now" works | Generalize the existing `action=sync_now` submit into `run_job=<key>` on the same settings form (full-page submit → Django message → redirect, reusing the existing spinner). Never passes `--force`, so a manual run is identical to a scheduled one. |
| Money-moving jobs (`bill_tabs`) | Still runnable, but the Run-now button routes through **`confirm_modal.html`** with an explicit consequence, and — the real safety — the manual run passes **no `--force`**, so `bill_tabs` still self-gates to actual billing time and no-ops otherwise. |
| Item 3 (legacy CMS clarity) | The legacy pull is surfaced in the Automations list as a labelled sub-toggle of the nightly sync, with a plain-English on/off line and its last-run; the Legacy CMS tab gets a one-line cross-link callout. |
| `airtable_pull` (separate Render cron) | Included in the registry as `cadence=EXTERNAL`. It runs from its own cron, so its enable-gate + run-recording are added inside that command via the shared helper (not the dispatcher). |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| The 15-min dispatcher (always-run + daily tuples, per-task try/except) | `run_scheduled_tasks` command | `core/management/commands/run_scheduled_tasks.py:36-65` |
| The separate nightly Airtable cron | `airtable_pull` command + `render.yaml` cron `0 10 * * *` | `render.yaml:54-56` |
| The dispatcher cron itself (`*/15`) — **new jobs go inside the dispatcher, not render.yaml** | `render.yaml` note + cron | `render.yaml:64-71` |
| Daily fan-out that the legacy pull hides inside | `sync_all_sources()` (legacy branch gated by `config.legacy_cms_sync_enabled`) | `hub/calendar_service.py:277-306` |
| The legacy-CMS enable flag + last-run + duration | `legacy_cms_sync_enabled`, `legacy_cms_last_synced_at`, `legacy_cms_last_sync_duration` on `SiteConfiguration` | `core/models.py:156-174` |
| The manual-trigger pattern to generalize (submit button, inline run, message, redirect, spinner + progress estimate) | Legacy CMS "Sync Now" | `templates/hub/admin/site_settings.html:325-341`, JS `:869-913`; view `hub/views.py:4488-4497` |
| The legacy sync callable Run-now invokes | `sync_legacy_cms()` | `classes/import_service.py` (called from view) |
| Site-settings page shell, tab UX (`x-data`, `.vote-tab`/`.vote-tab--active`), allowed-tab set | `admin_site_settings` + template | `hub/views.py:4464-4560`, `templates/hub/admin/site_settings.html:90-120` |
| Precedent for a tab that owns its own formset(s) saved by the page's Save | Calendar feed formset; Slideshow zones/slides formsets | `hub/views.py:4506-4536` |
| Toggle switch component | `components/toggle.html` | `templates/components/toggle.html` |
| Confirm-before-destructive component | `components/confirm_modal.html` | (component library) |
| Admin gate | `@fog_admin_required` | `hub/view_as.py:205` |
| Append-only audit log (optional attribution of a manual run) | `SiteActivity.log()` | `core/models.py:777` |
| Button styles | `.hub-btn--sm`, `--primary`, `--danger` | `static/css/hub.css:973-995` |
| Test layout + dispatcher spec to update | `describe_*/it_*`, patches `call_command`, mocks `timezone` | `core/spec/management/run_scheduled_tasks_spec.py` |

**Genuine gaps to close (kept minimal):**
1. No registry — the job list is hard-coded in two places (dispatcher tuples). → Add `core/scheduled_jobs.py`.
2. No per-job last-run/status store except legacy CMS. → Add `ScheduledTaskRun`.
3. No per-job ON/OFF except the three `SiteConfiguration` feature flags. → Add `ScheduledJobState`.
4. No UI. → Add the Automations tab + a `run_job` handler (generalize `sync_now`).

## 3. Where the code lives

```
core/
  scheduled_jobs.py                         NEW  registry (ScheduledJob dataclass, SCHEDULED_JOBS,
                                                 JOBS_BY_KEY, Cadence/Trigger enums,
                                                 is_enabled(), record_run() context manager)
  models.py                                 EDIT + ScheduledTaskRun, ScheduledJobState (+ managers)
  migrations/0049_scheduled_task_infra.py   NEW  additive CreateModel x2 (auto-reverse; no data migration)
  management/commands/run_scheduled_tasks.py EDIT iterate SCHEDULED_JOBS; honor is_enabled; wrap in record_run
  management/commands/airtable_pull.py      EDIT gate on is_enabled + wrap body in record_run (cadence=EXTERNAL)
  spec/
    scheduled_jobs_spec.py                  NEW  registry parity, is_enabled default, record_run
    models/scheduled_task_run_spec.py       NEW  model + manager
    models/scheduled_job_state_spec.py      NEW  model + manager
    management/run_scheduled_tasks_spec.py  EDIT registry-driven assertions + gating + recording
hub/
  views.py                                  EDIT admin_site_settings: add "automations" tab, run_job handler,
                                                 ScheduledJobState formset, automation-rows context
  forms.py                                  EDIT ScheduledJobStateFormSet factory (enabled only)
  spec/views/site_settings_automations_spec.py NEW view gating, run-now, money guard, toggle persistence, states
templates/hub/admin/
  site_settings.html                        EDIT + Automations tab button + panel; Legacy CMS cross-link callout;
                                                 generalize the sync spinner JS to any [data-run-now] button
static/css/
  hub.css                                   EDIT + .pl-automation-* card/list + status-pill classes
plfog/version.py                            EDIT bump VERSION (+ changelog — see §8)
```

Home app for models/registry/dispatcher: **`core`** (already in the coverage source list). UI + view glue: **`hub`**.

## 4. Data model

### 4a. Registry (`core/scheduled_jobs.py`) — not a DB model, a declared list

```python
class Cadence(models.TextChoices):
    ALWAYS = "always", "Every 15 minutes"    # dispatcher, every tick
    DAILY  = "daily",  "Once daily (~6 AM PT)" # dispatcher, only when UTC hour == 13
    EXTERNAL = "external", "Own schedule"       # separate Render cron (airtable_pull)

class Trigger(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    MANUAL    = "manual", "Run now"

@dataclass(frozen=True)
class ScheduledJob:
    key: str            # stable id, == command name; the ScheduledTaskRun/State key
    name: str           # human title, e.g. "Class reminder emails"
    description: str     # one plain sentence of what it does
    command: str         # management command call_command() runs
    schedule_label: str  # human, e.g. "Every 15 min", "Nightly ~6 AM", "Nightly ~3 AM"
    cadence: str         # Cadence value — drives dispatcher gating
    toggleable: bool = True   # does the UI show an ON/OFF toggle
    money_job: bool = False   # routes Run-now through a confirm modal; still never --force

SCHEDULED_JOBS: list[ScheduledJob] = [ ... ]   # the 10 always-run + 2 daily + airtable_pull
JOBS_BY_KEY = {j.key: j for j in SCHEDULED_JOBS}
```

Registry rows (member-readable `name`/`description` matter — this is the UI copy):

| key / command | name | cadence | toggleable | money | notes |
|---|---|---|---|---|---|
| `send_voting_reminders` | Guild voting reminders | ALWAYS | yes | | self-gates by voting window |
| `take_cycle_snapshot` | Funding cycle snapshots | ALWAYS | yes | | |
| `send_lease_expiry_reminders` | Lease-expiry reminders | ALWAYS | yes | | |
| `auto_complete_orientations` | Auto-complete orientations | ALWAYS | yes | | |
| `send_class_reminders` | Class reminder emails | ALWAYS | yes | | self-gates by session window |
| `publish_due_events` | Publish scheduled events | ALWAYS | yes | | |
| `send_event_reminders` | Event reminders | ALWAYS | yes | | |
| `bill_tabs` | **Charge member tabs** | ALWAYS | yes | **yes** | self-gates via advisory lock + billing-time; never `--force` |
| `retry_calendar_pushes` | Retry Google Calendar pushes | ALWAYS | yes | | housekeeping — describe the consequence of OFF |
| `sync_discord_guild_roles` | Sync Discord guild roles | ALWAYS | yes | | |
| `sync_all_sources` | Nightly calendar & class sync | DAILY | yes | | umbrella; owns the legacy-CMS sub-toggle (§6) |
| `generate_orientation_slots` | Generate orientation slots | DAILY | yes | | |
| `airtable_pull` | Airtable member pull | EXTERNAL | yes | | own Render cron `0 10 * * *`; gate+record inside the command |

Two module helpers used by dispatcher, standalone commands, and the view:

- `is_enabled(key: str) -> bool` — reads `ScheduledJobState`; **returns `True` when no row exists** (default-on). Manual "Run now" bypasses this (a manual override should run even a paused job).
- `record_run(key, *, trigger, actor=None)` — a `@contextmanager` that opens a `ScheduledTaskRun` (status RUNNING), yields, marks OK on clean exit, marks FAILED + captures `error` on exception, then **re-raises** (so the dispatcher's per-task try/except still logs and continues). This is the single place a run is written — dispatcher, `airtable_pull`, and the run-now view all use it.

### 4b. `ScheduledTaskRun` (core) — append-only run history

| Field | Type | Note |
|---|---|---|
| `task_key` | `CharField(max_length=64, db_index=True)` | registry key; free string (a retired job's history survives) |
| `status` | `CharField(choices=Status)` | `RUNNING` / `OK` / `FAILED`, default `RUNNING` |
| `trigger` | `CharField(choices=Trigger)` | `SCHEDULED` / `MANUAL` |
| `actor` | `FK(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL)` | who clicked Run now; null for scheduled |
| `started_at` | `DateTimeField(default=timezone.now, db_index=True)` | |
| `finished_at` | `DateTimeField(null=True, blank=True)` | null while running / if the process died mid-run |
| `error` | `TextField(blank=True, default="")` | exception text on failure |

- `class Status(TextChoices): RUNNING, OK, FAILED`. `help_text` on every field. Meaningful `__str__` (`f"{task_key} {status} @ {started_at:…}"`).
- **Meta:** `ordering = ["-started_at"]`; `indexes = [Index(fields=["task_key", "-started_at"], name="idx_taskrun_key_recent")]` — the dashboard's "latest per key" lookup.
- **Manager / queryset** (`ScheduledTaskRunQuerySet`):
  - `latest_per_task() -> dict[str, ScheduledTaskRun]` — one row per key (order by `-started_at`, first-wins), built in a single query, so the dashboard is O(1) queries not O(jobs).
- **Properties:** `succeeded` / `failed` (from `status`); `duration` (`finished_at - started_at`, `None` while running); `is_stale_running` (`status == RUNNING and started_at < now - 1h`) — a crashed run left RUNNING is rendered as "Unknown", not a perpetual "Running…".

### 4c. `ScheduledJobState` (core) — current ON/OFF per job

| Field | Type | Note |
|---|---|---|
| `task_key` | `CharField(max_length=64, unique=True)` | registry key |
| `enabled` | `BooleanField(default=True)` | absence of a row also means enabled |
| `updated_at` | `DateTimeField(auto_now=True)` | |
| `updated_by` | `FK(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL)` | who last flipped it |

- `help_text` on every field, `__str__`.
- **Manager:** `is_enabled(key) -> bool` (mirrors the module helper; `filter(...).first()` → `row.enabled if row else True`); `set_enabled(key, enabled, *, user)`; `sync_registry()` → `get_or_create` a row per `SCHEDULED_JOBS` entry (idempotent, so the toggle formset always has a row to bind) — **keeps** rows for unknown keys (never deletes history/state).

### 4d. Migration

`0049_scheduled_task_infra.py` — a plain additive migration with two `CreateModel`s and the index. **No data migration** (state rows are seeded lazily by `sync_registry()` at request/dispatch time, not in the migration), so Django's automatic reverse (`DeleteModel` ×2) is the complete and correct `reverse` — there is no `RunPython` needing a hand-written reverse function. Depends on `core/0048_…`.

## 5. Business logic (fat models)

- **Dispatcher rewrite** (`run_scheduled_tasks.handle`): iterate `SCHEDULED_JOBS`. Skip `EXTERNAL` (its own cron). Skip `DAILY` unless `now.hour == 13`. Skip when `not is_enabled(job.key)` (log `– {key} disabled`). Otherwise `with record_run(job.key, trigger=SCHEDULED): call_command(job.command, stdout=…, stderr=…)`, still wrapped in the existing per-task try/except so one failure can't stop the rest. Behavior is identical to today when every job is enabled and no state rows exist.
- **`airtable_pull.handle`**: at the top, `if not is_enabled("airtable_pull"): return` (log a skip). Wrap the actual pull body in `with record_run("airtable_pull", trigger=SCHEDULED):`. No recursion — it records around its own work, it does not re-dispatch itself.
- **`record_run`** owns the RUNNING→OK/FAILED transition and error capture; it is the only writer of `ScheduledTaskRun`. Domain: no new exception type needed — it re-raises whatever the command raised after marking FAILED.
- **`ScheduledJobState.set_enabled`** is the single mutation for a toggle (records `updated_by`). Optionally `SiteActivity.log(kind=…, actor=…)` for a manual Run-now and for a toggle flip — reuse the existing log; add no new Kind unless review wants one (leave as an open item, §10).
- Views stay thin: the run-now view resolves the job from `JOBS_BY_KEY` (fail loudly on unknown key), calls `record_run(key, trigger=MANUAL, actor=request.user)` around `call_command(job.command)` (**never** `--force`), and returns a message. Validation of "is this a real job / is this a money job" lives on the registry, not scattered in the view.

## 6. UI / UX  ← completeness checklist applied concretely

### Screen A — Site Settings → **Automations** tab (`templates/hub/admin/site_settings.html`)

- **Layout & container:** a new `.vote-tab` button "Automations" alongside General/Calendar/Legacy CMS/Features/Discord/Slideshow, and an `x-show="tab === 'automations'"` panel. Add `"automations"` to the allowed-tab set in the view (`hub/views.py:4477`). The panel is a **responsive card list**, *not* a `<table>` (tables don't reflow cleanly — FRONTEND §6). Each job is a `.pl-automation-card`.
- **This panel sits INSIDE `#site-settings-form`** (like the Features/Legacy tabs) so the toggles save with the page's existing **Save** button and the Run-now buttons are submit-buttons of that form — mirroring the `sync_now` precedent. **Do not** wrap individual rows in their own `<form>` elements: a `<form>` nested inside `#site-settings-form` is invalid HTML and orphans the outer Save button (the nested-form bug we've hit before). One outer form, multiple named submitters.
- **Components used:** `components/toggle.html` (the ON/OFF per row — never a raw checkbox), `components/confirm_modal.html` (money jobs only), the page's existing Save button, the reused sync spinner.

**Each `.pl-automation-card` shows five regions** (a CSS-grid row on desktop, stacked on mobile):

1. **Name + description** — `job.name` (bold) over `job.description` (muted). For the money job, a small `.pl-automation-badge` "charges cards".
2. **Schedule** — `job.schedule_label` (e.g. "Every 15 min", "Nightly ~6 AM").
3. **Last run** — from `latest_per_task()[key]`:
   - OK → green `.pl-run-status--ok` pill + `run.finished_at|date:"N j, g:i A"` (Portland tz, matching the rest of the page) + relative "(2h ago)".
   - Failed → red `.pl-run-status--failed` pill "Failed" + time, and the truncated `error` under it in muted red with the full text in `title=`.
   - Running (fresh) → blue `.pl-run-status--running` "Running…".
   - Stale running / no `finished_at` past 1h → grey "Unknown".
   - **Never run** → grey `.pl-run-status--never` "Never run" + em-dash time (the explicit empty state — never a blank cell).
4. **Enabled toggle** — `{% include "components/toggle.html" with field=jobform.enabled toggle_label="On" %}` bound to that job's `ScheduledJobState` formset row (below). When off, add a muted `.pl-automation-paused` line "Paused — won't run on schedule" and dim the card. Non-`toggleable` jobs render a static "Always on" chip instead of a toggle.
5. **Run now** — for a normal job: `<button type="submit" name="run_job" value="{{ job.key }}" class="hub-btn hub-btn--sm hub-btn--primary" data-run-now>` with the reused spinner span. For a **money job** (`bill_tabs`): a `hub-btn--sm hub-btn--danger` button that `@click="$dispatch('open-confirm', 'run-bill_tabs')"` opens a `confirm_modal.html` whose form submits `run_job=bill_tabs` — message names the consequence ("This attempts real card charges. It still only runs if it's a scheduled billing day."). Even so the server passes **no `--force`**.

**The toggles are a formset, saved by the page Save** (precedent: the slideshow zone/slide + calendar-feed formsets on this same page):
- On GET, the view calls `ScheduledJobState.objects.sync_registry()` then builds `ScheduledJobStateFormSet` (a `modelformset_factory(ScheduledJobState, fields=["enabled"], extra=0)`) over the rows in registry order. **`extra=0`** — no perpetual blank row (FRONTEND §1). The context pairs each `ScheduledJob` with its bound form + latest run so the template loops once (`automation_rows = [{"job":…, "form":…, "last_run":…}, …]`).
- On Save, `_save_site_settings` also does `formset.save()`. No Add/Delete controls — the row set is fixed by the registry (this is not a user-managed list), so §1's Add/Delete requirement is N/A; the fixed set is stated in copy ("Jobs are defined by the app; you can pause or run them, not add or remove them").

**Legacy CMS clarity (item 3), inside the `sync_all_sources` card:** directly under that row, a nested labelled block:
- The `form.legacy_cms_sync_enabled` toggle via `toggle.html`, labelled **"Include legacy CMS offerings (nightly pull)"**.
- A plain-English status line driven by the flag: ON → "The nightly pull from classes.pastlives.space is running."; OFF → **"OFF right now — the nightly pull from classes.pastlives.space is NOT running. Turn this on to resume."**
- Its own last-run (`config.legacy_cms_last_synced_at` or "Never synced") and a **"Sync now"** that keeps the existing `action=sync_now` path (which carries the duration-estimate progress bar) — so the richer legacy progress UI is preserved, while the generic jobs use the plain spinner.

- **Controls, named:**
  - **Save:** the page's existing Save button (bottom of `#site-settings-form`) — persists all toggles (job states + legacy flag); full-page post → Django `messages.success("Site settings saved.")` → redirect `?tab=automations`.
  - **Run now (generic):** submit button `name="run_now"… value="<key>"`; POST handled before form save (like `sync_now`); Django message "Ran <name> — <ok/failed>."; redirect `?tab=automations`.
  - **Run now (money):** same, gated by confirm modal; danger styling.
  - Toggles: `toggle.html` for every boolean. No raw checkboxes.

- **States:** empty/never-run (per row "Never run"); loading (spinner on the clicked Run-now button; legacy keeps its progress bar); error (a failing command → FAILED pill + message "Ran <name> — failed: <reason>", not a 500; unknown `run_job` key → `messages.error` + redirect, no dispatch); success (message + the row's last-run updates on reload).
- **Dark + light:** all colors from tokens — `--hub-card-bg`, `--hub-border`, `--hub-text`, `--hub-text-muted`; status pills from the existing success/danger/info accents (define `.pl-run-status--ok/--failed/--running/--never` with token backgrounds, no hardcoded hex except the shared accent vars). Toggle + buttons already theme-correct. **No inline `background`/`color` on any control**; toggles/buttons carry their own themed classes. The reused sync-spinner CSS already exists. Verify both themes.
- **Mobile:** `.pl-automation-card` is `display:flex/grid` **in a CSS class** (never inline on the `x-show` panel — FRONTEND §12/Rule 12). Desktop: `grid-template-columns` laying the five regions across; `@media (max-width:640px)`: single column, each region stacked with its own small label ("Schedule", "Last run"), full-width toggle + Run-now button (real tap targets). No horizontal scroll. 8px-grid spacing; Run-now/Delete-style buttons carry `margin-top:0.75rem` clearance in the stacked layout.

### Screen B — Site Settings → **Legacy CMS** tab (existing), one-line cross-link

Add a short `.hub-callout`-style line above the existing legacy sync section: *"'Sync offerings from legacy CMS' is the nightly automatic pull. Unchecked = it's OFF. Manage it alongside every scheduled job on the [Automations tab](?tab=automations)."* No behavior change here — pure signposting so the two places agree.

## 7. Notifications / emails / activity

No emails. **Optional** `SiteActivity.log()` on a manual Run-now and on a toggle flip (attribution: who ran/paused what) — reuse the existing log; whether to add a dedicated `Kind` (e.g. `AUTOMATION_RUN`, `AUTOMATION_TOGGLED`) or reuse a generic one is left to §10. No member-facing notification (admin-only surface).

## 8. Build order (phased; each phase ships green)

1. **Registry + models + migration.** `core/scheduled_jobs.py` (dataclass, `SCHEDULED_JOBS`, `JOBS_BY_KEY`, `is_enabled`, `record_run`), `ScheduledTaskRun` + `ScheduledJobState` + managers, migration `0049`. Specs: registry parity, `is_enabled` default-on, `record_run` OK/FAILED, manager `latest_per_task`/`sync_registry`. No behavior change yet. Green.
2. **Dispatcher + airtable_pull instrumentation.** Rewrite `run_scheduled_tasks` to iterate the registry, honor `is_enabled`, wrap in `record_run`; gate + record `airtable_pull`. Update `run_scheduled_tasks_spec.py` to assert against the registry. Behavior identical when all enabled / no state rows. Green.
3. **Automations tab UI + run_job handler.** Add tab button + panel, the `ScheduledJobStateFormSet`, `automation_rows` context, the generic `run_job` handler (generalized `sync_now`), the legacy-CMS clarity block + Legacy-tab cross-link, `hub.css` `.pl-automation-*` + status pills, generalize the spinner JS to `[data-run-now]`. View/template specs. Green.
4. **Money-job confirm guard + state polish + release.** `confirm_modal.html` on `bill_tabs`; failed/never/stale-running rendering; verify dark+light. Bump `plfog/version.py`. **Changelog:** this is an **admin/ops-only** surface (fog admins), not visible to general members — per the versioning rules that leans toward *no* CHANGELOG entry (internal). Recommend bumping VERSION with **no** changelog entry, or a single short admin-facing line if the team wants it announced; flag the call at PR time rather than inventing member copy for an admin tool.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under each app's `spec/`, `describe_*`/`it_*` (**never `context_*` — not collected**), factory-boy, run in the `plfog-web` Docker image, ≥98% coverage.

- **Registry ↔ dispatcher parity** (`scheduled_jobs_spec.py`): every `job.command` is a real registered management command (`django.core.management.get_commands()`); keys unique; every `cadence` a valid `Cadence`; the set of `ALWAYS`+`DAILY` keys equals what the dispatcher iterates (guards against a job added to the registry but not wired, or vice versa).
- **`is_enabled`**: `True` with no row (default-on); reflects `enabled=False`; `set_enabled` writes `updated_by`.
- **`record_run`**: clean body → one `ScheduledTaskRun` OK with `finished_at`; raising body → FAILED + `error` captured + exception re-raised; `trigger`/`actor` recorded.
- **`ScheduledTaskRun` manager**: `latest_per_task()` returns exactly one (most recent) per key in a bounded query count; `duration`/`is_stale_running` properties.
- **Dispatcher (updated spec)**: patch `call_command`, mock `timezone`; ALWAYS jobs called at hour≠13; DAILY jobs only at hour==13; **disabled job (state row `enabled=False`) is NOT called while others are**; `EXTERNAL` job never dispatched here; a raising command doesn't stop later jobs; a `ScheduledTaskRun` row is written per attempted job (OK vs FAILED).
- **Run-now view** (`site_settings_automations_spec.py`): non-admin → 403 (`fog_admin_required`); `run_job=<key>` as admin → `call_command` invoked **without `--force`**, `ScheduledTaskRun(trigger=MANUAL, actor=user)` created, success message, redirect `?tab=automations`; unknown key → error message, **no** dispatch; a command that raises → failed message, FAILED run recorded, no 500.
- **Money-job guard**: `bill_tabs` Run-now dispatches with the exact safe args (no `--force`); template renders the confirm modal + danger button for `bill_tabs` and a plain primary button for others.
- **Toggle persistence**: saving the settings form with a job toggled off writes `ScheduledJobState.enabled=False`; the legacy sub-toggle writes `SiteConfiguration.legacy_cms_sync_enabled`.
- **Template states**: never-run row renders "Never run"; failed row renders the FAILED pill + error `title`; disabled row renders the "Paused" note; the legacy block renders the plain-English OFF sentence when the flag is off.
- **tz note**: dispatcher gate is UTC `hour == 13`; timestamps stored UTC and displayed via `|date` in project (Portland) tz — assert the display filter, not a raw UTC string, and keep any hour-based test mocking `timezone.now`.

## 10. Open / deferred

- **`SiteActivity` Kind for automations** — reuse a generic kind vs. add `AUTOMATION_RUN`/`AUTOMATION_TOGGLED`. Deferred to review (adding a Kind is a model change with its own migration).
- **Run-history drill-down** — the model stores full history, but the tab shows only the latest run per job. A "view last N runs" modal is out of scope for v1 (YAGNI); the data is there when we want it.
- **Async Run-now** — jobs run synchronously in the request (same as today's `sync_now`); a slow one holds the request. Acceptable for admin-only, low-frequency clicks; background execution is out of scope.
- **Pruning old `ScheduledTaskRun` rows** — append-only; a retention/cleanup job is deferred until volume warrants it (12–13 jobs × 96 ticks/day is modest, and only the daily/manual ones write often).
- **Changelog** — see §8; whether this admin tool gets an announced entry is a ship-time call.
- **Out of scope:** adding/removing jobs from the UI (registry is code), editing schedules from the UI (schedules live in `render.yaml` + the dispatcher gate), and any member-facing surface.

## 11. Review addendum — fold in before building

An adversarial UX review confirmed the good structural choices (Run-now kept as outer-form submitters, `toggle.html` not checkboxes, card list not table, real tokens, the `bill_tabs` no-`--force` self-gate + advisory lock) but found substantive holes.

**Won't function as drawn:**
1. **The `bill_tabs` Run-now confirm is a dead control.** `confirm_modal.html` (plain-POST) renders a *teleported* `<form>` with only `{% csrf_token %}` + a name-less submit — it can't carry `run_job=bill_tabs` and posts *outside* `#site-settings-form`. Extend the component with a hidden-field slot, or encode the job key in `confirm_action_url` and read it in the handler.
2. **Button-name inconsistency:** §4a/§5 use `name="run_job"`; §6's "Controls, named" says `name="run_now"`. Pick one canonical key — the handler branches on exactly one.
3. **A failing Run-now would 500.** `record_run` re-raises after marking FAILED and the run-now view is specced with no surrounding try/except → unhandled exception instead of the promised FAILED pill. Wrap it like the legacy `sync_now` handler.
4. **Toggle↔job row alignment is undefined.** `ScheduledJobState` has no `Meta.ordering` and the formset queryset isn't pinned to registry order → form[i] can bind a different job than job[i], saving the wrong job's on/off. Pin the order (or match each form to its job by `instance.task_key`), and render the formset `management_form` + each `{{ form.id }}` (the feed/slide formsets on this page do).

**Reuse / foot-guns / states:**
5. **Reinvented status pills.** Use the existing `.hub-pill--ok/--warn/--danger/--neutral` (hub.css:390-404) — they already carry the `[data-theme="light"]` overrides added for legibility; the proposed `.pl-run-status--*` omit that and re-break light mode.
6. **`bill_tabs` on/off = a second, hidden billing kill-switch** decoupled from `BillingSettings.charge_frequency`. Make `bill_tabs` non-toggleable (Run-now only), or surface the billing-settings state on the card.
7. **Run-now silently discards unsaved toggle edits** (13 toggles + 13 run-now buttons in one form; run-now intercepts before save). Warn, or persist toggles as part of the run-now POST.
8. **Slow-job Run-now → worker timeout.** `sync_all_sources` is a generic card but is the slow umbrella; run synchronously it can time out a Render worker and leave a row stuck RUNNING (stale only after 1h). Specify which jobs are safe in-request, or give the umbrella the richer progress treatment (not just its legacy child).
9. **Dual "last run" provenance for the legacy pull** — the untouched `sync_now` writes `config.legacy_cms_last_synced_at` while the card reads `ScheduledTaskRun.latest_per_task()`; two timestamps, different buttons, overlapping work. Pick one authoritative source.
10. **Umbrella-toggle OFF consequence understated** — turning off the nightly sync stops the whole `sync_all_sources` fan-out (calendar feeds too), not just classes; say so in the card copy.
11. **Minor:** every registry row is `toggleable=yes` so the "Always on" branch is dead (drop the field or use it); and state that the jobstate formset binds whenever posted (feeds pattern — correct, the panel is always in the DOM) so a jobstate error can't block saving another tab.
