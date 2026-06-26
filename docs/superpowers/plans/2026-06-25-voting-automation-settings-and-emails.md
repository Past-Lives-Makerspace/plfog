# Voting automation, Settings tab & member email suite — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-25
**Surface:** FOG hub (`pastlives.test`) — the admin **Voting** page (Settings + Overview tabs) and the member voting email suite (recipients' inboxes / the bell).
**Related:**
- **Spec 1 (ships first):** `docs/superpowers/plans/2026-06-25-voting-admin-tabs-and-audit.md` — builds the tabbed Voting admin IA (Overview / Funding History / Snapshots / Settings) as native hub tabs, plus the per-member audit and snapshot take/delete. **This spec assumes that tab shell exists** and fills the Settings tab, adds automation, wires the "Results are in — review & send" banner onto Overview, and adds/extends the emails.
- `docs/superpowers/plans/2026-06-25-branded-notification-emails.md` — the branded `_base.html` shell that the spine wraps copy-mode emails in. The new voting emails inherit that branding for free.
- The notification-spine phases (registry / emit / copy / channels / scheduler).

---

## 1. Summary

Today the monthly guild-funding vote has one hardcoded automation (a 48-hour "voting closes soon" broadcast) and one **fully automatic** member email: the instant an admin takes a funding snapshot, `FundingSnapshot.take()` immediately emails *every voter* the results. There is no admin review step, no per-member personalization, and no place to configure any of it.

This feature gives admins a real **Voting → Settings** control panel and turns the results email into an **admin-confirmed** action. After this work:

- An admin opens **Voting → Settings** and sets the reminder lead time, the funding-pool floor, and master switches for the automated cycle-end snapshot and the member nudges — all as proper themed toggles/fields with a Save button, and with one-click links out to edit each email's wording.
- At month end the system **auto-takes a snapshot** (so the monthly record always exists) and pings admins **"Results are in — review & send"** — it does **not** email members. The member results email goes out only when the admin clicks **Send results** on the Voting Overview, after eyeballing the numbers.
- Members get two **personalized** nudges before close — *"Polls closing soon!"* (to people who voted, showing **their own** 1st/2nd/3rd) and *"Vote soon!"* (to people who've signed in but never voted) — and a **personalized results email** ("here's the allocation, and here's what we recorded *you* voting for").

Voting itself never locks — this is a **soft close**. The member voting page stays open continuously.

### Locked decisions (from the brainstorm)

| Decision | Choice |
|---|---|
| Results email timing | **Admin-confirmed.** `take()` no longer auto-sends. Taking a snapshot marks it "results pending send" and notifies admins; the member email fires only on the admin's **Send results** click. *The single most important behavior change in this spec.* |
| Close model | **Soft close — no lockout.** Automation only auto-takes a snapshot + enters the confirm flow. No voting-window state machine; the member voting page is never made read-only. |
| Auto snapshot flag | Auto-taken snapshots carry **`is_auto=True`** (introduced here) so Spec 1's badge + the "delete/recalc the automated ones" story work. Delete + recalc are Spec 1's. |
| Reminder lead time | **Configurable** (`reminder_lead_days`, default 3), replacing the hardcoded 48h. |
| Pool floor | **Configurable** (`minimum_pool_floor`, default $1,000), replacing the hardcoded constant in three files. |
| `voting.closing_48h` event | **Repurpose → `voting.closing_soon`** (the name's "48h" is wrong once the lead time is configurable). Resolver becomes **per-member** and copy gains the recipient's recorded vote. Migration renames the DB copy/preference keys. |
| Per-member personalization | The spine renders **one** message per `emit()` (shared context across recipients), so personalization is done by **emitting once per member** in a loop — not by a single broadcast `emit()` to `ALL_VOTERS`. (See §7.2 — this is the load-bearing mechanism.) |
| Discord on the reminder | The personalized `closing_soon` reminder is **email + in-app only** (no Discord — a per-member email isn't a broadcast). A generic Discord "go vote" ping is **deferred** (§10). |
| Results audience | The results email goes to **members who voted** (driven by the snapshot's frozen `raw_votes`), each personalized. Non-voters see results on the history page. (§7.3) |
| Email-text editing | **Reuse the existing hub copy-editor** at `/manage/notifications/<event_key>/<channel>/edit/`. We register the events + curate the copy; the Settings tab links out to it. **No new email-text editor.** |

---

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| Single emission point (logs activity, resolves recipients, fans out, dedupes) | `emit(event_key, *, actor, target, context, title, body, url, period, messages, email_to)` | `core/events/emit.py:43` |
| Idempotency ledger (unique `event_key/target_ref/channel/period`) | `EventDelivery` | `core/models.py:820` |
| Per-user, per-channel opt-out (email opt-out, in-app forced) | `NotificationPreference` + `enabled_channels()` | `core/models.py:784`, `core/events/preferences.py:67` |
| Generalized scheduler — source yields `ScheduledOccurrence`s, `run_sources` due-checks the 15-min window + fires `emit`, deduped by `period` | `ScheduledOccurrence`, `run_due`, `run_sources` | `core/events/scheduler.py:52,111,134` |
| The current 48h voting reminder source + cycle timing helpers (`cycle_label`, `cycle_period`, `month_end_close`, `closes_on_display`) | `closing_48h_occurrences` & friends | `membership/voting.py:38,43,48,63,74` |
| Per-member recipient resolver (reads `context["member"]`, drops no-account/no-email) | `registrant` resolver | `core/events/resolvers.py:210` |
| Admin audience resolver | `fog_admins` | `core/events/resolvers.py:95` |
| Eligible-voter audience (paying + active + usable account) | `all_voters` | `core/events/resolvers.py:338` |
| Per-member email-safety (drop members with no usable `User`/email) | `_member_user` / `_members_to_recipients` | `core/events/resolvers.py:44,75` |
| Curated copy + documented placeholders + sample context for the voting events | `_CURATED["voting.closing_48h"]`, `_CURATED["voting.results_published"]` | `core/events/copy.py:431,462` |
| Branded email shell wrap at copy-render time | `rendered_message` / `wrap_email_html` | `core/events/templates.py:89,71` |
| **The email-text editor to reuse** (edit copy + live preview + version revert) | catalogue / edit_copy / preview / revert | `hub/urls.py:215`, `hub/notification_views.py:169,180`, `templates/hub/admin/notifications/{catalogue,edit_copy,_preview}.html` |
| Catalogue already groups events by `EventType.category` → a **"Voting"** section already renders | `_grouped_events()` | `hub/notification_views.py:127` |
| Seed/refresh DB copy rows from code defaults (override-safe; auto-covers new events) | `seed_notification_templates` + `seedable_rows()` | `core/management/commands/seed_notification_templates.py`, `core/events/copy.py:583` |
| Settings-singleton pattern (`pk=1`, `load()`, `save()` forces pk=1) | `SiteConfiguration`, `BillingSettings`, `ClassSettings` | `core/models.py` `load()`, `billing/models.py:173`, `classes/models.py:1796` |
| Admin-edits-a-settings-singleton page (GET/POST view, ModelForm, `form_field.html`, `messages.success`, Save button) | `ClassSettings` admin settings page | `classes/views.py:2528`, `templates/classes/admin/settings.html` |
| The snapshot model — take/publish/summary, frozen per-member votes, Airtable sync | `FundingSnapshot.take()`, `publish_results()`, `allocation_summary()`, `raw_votes` | `membership/models.py:1394,1482,1471,1371` |
| The funding math (already accepts a `minimum_pool` arg) | `calculate_results(votes, *, paying_voter_count, minimum_pool)` | `membership/vote_calculator.py:25` |
| Member querysets (active / paying / email status) and recorded vote | `Member.objects.active()/paying()`, `with_email_status()/missing_email()`, `member.vote_preference` | `membership/models.py:121,124,143,179` + `VotePreference.member` related_name `vote_preference` (`:1290`) |
| Cron dispatcher (always-run + daily lists, each task self-deduping) | `run_scheduled_tasks` | `core/management/commands/run_scheduled_tasks.py:36` |
| A clean EventDelivery-deduped scheduled job to copy | `send_lease_expiry_reminders` (`period=f"lease:{pk}:expiring"`) | `core/management/commands/send_lease_expiry_reminders.py` |
| Toast / confirm modal / toggle / form-field components | `trigger_toast`, `components/{confirm_modal,toggle,form_field}.html` | `hub/toast.py`, `templates/components/` |

**Gaps to close (kept small):**
1. A `VotingSettings` singleton + its Settings-tab form/view/template.
2. Three `FundingSnapshot` fields (`is_auto`, `results_sent_at`, `results_send_count`) + the `take()`/`send_results()` rework so taking ≠ sending.
3. Two new events (`voting.vote_soon`, `voting.results_ready`) + the repurpose/rename of `voting.closing_48h → voting.closing_soon`; per-member resolvers + curated copy with vote placeholders.
4. Two more scheduler sources (closing-soon + vote-soon) and one new auto-snapshot management command, wired into the cron.
5. The Overview "Results are in — review & send" banner + Send/Resend controls.
6. Switch the hardcoded pool floor (three files) to the setting.

---

## 3. Where the code lives

> **Cross-spec reconciliation (with Spec 1) — read this first.** Spec 1 ships the Voting tab shell as **flat** templates under `templates/hub/admin/`: `voting_overview.html`, `voting_settings.html`, `voting_history_detail.html`, `voting_snapshots.html`, `_voting_tabs.html`. Spec 1 **already creates** the `voting_settings` view + the `/manage/voting/settings/` route (the GET shell) and the admin audit-detail page `voting_history_detail` at `/manage/voting/history/<pk>/`. This spec therefore **adds content into those existing flat files and extends Spec 1's existing `voting_settings` view (adds its POST/save branch)** — it does **not** create a `voting/` subdirectory and does **not** add a parallel settings view or route. The only genuinely new route here is the `send_results` mutation endpoint (a new action, not a shell duplicate). Spec 1 also explicitly hands this spec two badge items: rewrite `FundingSnapshot.source_label` and add the `.pl-vote-badge--auto` pill CSS (both listed below).

```
membership/
  models.py
    + VotingSettings                      # NEW singleton (pk=1, load())
    ~ FundingSnapshot                      # + is_auto, results_sent_at, results_send_count
                                           #   take() stops auto-sending; new send_results()
    ~ FundingSnapshot.source_label         # rewrite → "Automatic"/"Manual" from is_auto  (Spec 1 handoff)
  voting.py
    ~ closing_soon_occurrences(now)        # renamed from closing_48h_occurrences; per-member; lead from settings
    + vote_soon_occurrences(now)           # NEW per-member source (logged-in, no vote)
    + previous_cycle_label / close_period / cycle_start   # NEW helpers for the auto-snapshot job
    - REMINDER_OFFSET (= timedelta(hours=-48)) + the "48h" module docstring  # removed/rewritten — lead is now configurable
  management/commands/
    take_funding_snapshot.py   ~ --minimum-pool default → None (falls through to VotingSettings floor)
  migrations/
    + 00xx_votingsettings.py               # CreateModel VotingSettings
    + 00xx_fundingsnapshot_results_flags.py# add fields + backfill results_sent_at on existing rows
core/
  events/registry.py    ~ rename + repurpose closing event; add vote_soon + results_ready; change resolvers/channels/activity_kind
  events/copy.py        ~ rename curated key; add vote_soon + results_ready copy; new vote placeholders + sample contexts
  management/commands/
    send_voting_reminders.py   ~ run_sources([closing_soon_occurrences, vote_soon_occurrences])
    take_cycle_snapshot.py     + NEW auto-snapshot job (EventDelivery-deduped)
    run_scheduled_tasks.py     ~ add "take_cycle_snapshot" to the always-run list
  migrations/
    + 00xx_rename_voting_closing_event_keys.py  # data migration: closing_48h → closing_soon on NotificationTemplate/Preference (reverse renames back)
hub/
  forms.py   + VotingSettingsForm
  views.py   ~ voting_settings (Spec 1's view) — ADD the POST/save branch (do NOT add a parallel view)
             + send_results (NEW HTMX mutation view — a new action, not a shell duplicate)
  urls.py    ~ reuse Spec 1's /manage/voting/settings/ route; + ONE new route for send_results
plfog/
  dashboard.py     ~ pool floor from VotingSettings (was MINIMUM_FUNDING_POOL_FLOOR)
  admin_views.py   ~ pool floor from VotingSettings (MOOT if Spec 1 deletes this file — coordinate at build time)
static/css/
  hub.css   + .pl-vote-badge--auto pill  (Spec 1 handoff — pairs with the source_label rewrite)
templates/hub/admin/   (Spec 1 ships these FLAT files — this spec adds content INTO them)
  voting_settings.html        + the VotingSettings form + copy-editor links     (into Spec 1's file)
  voting_overview.html        + the "Results are in — review & send" banner      (into Spec 1's file)
  _results_send_control.html  + NEW shared partial: Send / "sent at … · Resend" + confirm modals + loading state
tests/  (BDD specs — see §9)
```

Home apps: `membership` owns the settings model + snapshot logic + cycle timing; `core` owns the events/copy/scheduler/cron; `hub` owns the admin pages (extending Spec 1's). This mirrors the existing split and keeps everything inside the current coverage/mypy scope.

---

## 4. Data model

### 4.1 `VotingSettings` (new singleton — `membership/models.py`)

Follows the confirmed `SiteConfiguration`/`BillingSettings`/`ClassSettings` pattern exactly: `pk=1`, `load()` classmethod, `save()` forces `self.pk = 1`.

| Field | Type | Notes |
|---|---|---|
| `reminder_lead_days` | `PositiveIntegerField(default=3, validators=[MinValueValidator(1)])` | help_text: "How many days before close to send the 'Polls closing soon!' reminder (minimum 1)." Replaces the hardcoded 48h. **Minimum is 1, not 0:** the occurrence anchors at `month_end_close` (= midnight on the 1st of the *next* month); with `offset=timedelta(days=0)` the reminder would fire only *after* the cycle has already rolled over and would compute the new month's label. Enforcing ≥1 matches the "N days before" intent and avoids that misfire. |
| `minimum_pool_floor` | `DecimalField(max_digits=10, decimal_places=2, default=Decimal("1000.00"))` | help_text: "Dollar floor for the funding pool. The pool is the larger of (paying voters × $10) and this." Replaces the hardcoded constant; default matches today's value so behavior is unchanged. |
| `reminders_enabled` | `BooleanField(default=True)` | Master switch for the "Polls closing soon!" reminder. |
| `send_vote_soon_enabled` | `BooleanField(default=True)` | Master switch for the "Vote soon!" nudge to members who've signed in but never voted. |
| `auto_snapshot_enabled` | `BooleanField(default=True)` | Master switch for the automated cycle-end snapshot (which then enters the admin-confirmed results flow — it never auto-emails members). |

- `load() -> VotingSettings`: `obj, _ = cls.objects.get_or_create(pk=1); return obj`.
- `save()`: `self.pk = 1; super().save(...)`.
- `__str__`: `"Voting settings"`.
- Defaults are chosen so a never-edited install behaves like today (floor $1,000) but with the automation on; nothing emails members without an admin click, so defaults-on is safe.
- **Migration:** `CreateModel` only; no data migration (the row is created lazily by `load()`). Reverse = drop table (default).

### 4.2 `FundingSnapshot` additions (`membership/models.py`)

| Field | Type | Notes |
|---|---|---|
| `is_auto` | `BooleanField(default=False)` | help_text: "True when this snapshot was taken automatically at cycle end (vs. by an admin)." Drives Spec 1's badge + "delete/recalc the automated ones" story. |
| `results_sent_at` | `DateTimeField(null=True, blank=True)` | help_text: "When the member results email was sent for this snapshot. Null = results are pending the admin's review & send." |
| `results_send_count` | `PositiveIntegerField(default=0)` | help_text: "How many times the results email has been sent (≥1 after the first send; supports resend without the dedupe ledger blocking it)." |

Properties:
- `results_pending -> bool`: `self.results_sent_at is None and bool(self.allocation_summary())` — there are real per-guild results and they haven't been sent. (A legacy/vote-less snapshot with no allocation is never "pending.")

**Migrations (two logical changes):**
1. **Schema:** add the three fields. Reversible by default (field removal).
2. **Data backfill (critical):** every snapshot that already exists was created under the **old auto-send** behavior, so its results were already emailed. Without a backfill they'd all have `results_sent_at IS NULL` and wrongly show up in the new "review & send" banner. The data migration sets, for all existing rows, `results_sent_at = snapshot_at` and `results_send_count = 1`. **Reverse:** set `results_sent_at = NULL`, `results_send_count = 0` (a real reverse, not a no-op). New auto/manual snapshots created after deploy start at `results_sent_at = NULL` (genuinely pending).

### 4.3 Event-key rename (data migration — `core/migrations`)

Repurposing `voting.closing_48h → voting.closing_soon` changes the registry key, which is also the `event_key` stored on `NotificationTemplate` (admin-edited copy) and `NotificationPreference` (per-user opt-outs). A data migration renames those rows so any admin override and any member's email opt-out carry over:

- `NotificationTemplate.objects.filter(event_key="voting.closing_48h").update(event_key="voting.closing_soon")`
- `NotificationPreference.objects.filter(event_key="voting.closing_48h").update(event_key="voting.closing_soon")`
- **Reverse:** rename `voting.closing_soon → voting.closing_48h` on both tables.
- `EventDelivery` rows for the old key are an inert ledger (old `voting:YYYY-MM` periods) — left as-is; new sends use the new key. Runs **before** the post-deploy `seed_notification_templates` so the renamed (preserved-if-overridden) copy row is the one seed refreshes.

---

## 5. Business logic (fat models / managers / commands)

### 5.1 `FundingSnapshot.take()` — stop auto-sending; notify admins instead

New signature: `take(cls, *, title="", minimum_pool=None, is_auto=False, actor=None)`.

Changes from today (`membership/models.py:1394`):
- `minimum_pool=None` → resolve from `VotingSettings.load().minimum_pool_floor` (was the literal `1000`). An explicit value still overrides (the manual snapshot form passes one).
- Set `is_auto` on the created row.
- **Remove the `snapshot.publish_results()` call.** Taking a snapshot no longer emails members.
- Log the snapshot-taken activity **once** here: `SiteActivity.log("funding_snapshot_taken", actor=actor, target=snapshot)` (previously this rode on the results `emit`; now it's explicit so it stays a single row regardless of how many member emails go out later).
- Emit the admin notification: `emit("voting.results_ready", actor=actor, target=snapshot, context={...}, period=f"snapshot_ready:{snapshot.pk}")` — one-shot per snapshot, to `FOG_ADMINS`. This is the "Results are in — review & send" ping.
- Return the snapshot unchanged otherwise.

Guard/side-effect summary: still returns `None` when there are no votes; still Airtable-syncs on `save()` (unchanged); `is_auto` rows are deletable by Spec 1's hard-delete + Airtable-cleanup flow.

### 5.2 `FundingSnapshot.send_results(*, actor=None, resend=False)` — the admin-confirmed member send

Replaces the old `publish_results()` (rename for clarity; the registry comment that referenced `publish_results` is updated). The per-member personalization is the whole reason this is a loop, not one `emit`:

```python
def send_results(self, *, actor=None, resend=False) -> int:
    if self.results_sent_at is not None and not resend:
        raise ResultsAlreadySentError(f"Results for '{self.cycle_label}' were already sent.")
    self.results_send_count += 1
    n = self.results_send_count
    sent = 0
    # One query for every still-active voter on this snapshot (select_related the user
    # the registrant resolver will read), then loop the frozen votes — not a query per vote.
    member_ids = [v["member_id"] for v in self.raw_votes]
    active = {
        m.pk: m
        for m in Member.objects.filter(pk__in=member_ids, status=Member.Status.ACTIVE).select_related("user")
    }
    for vote in self.raw_votes:                      # frozen per-member votes (already on the snapshot)
        member = active.get(vote["member_id"])
        if member is None:
            continue                                  # voter no longer active → skip (audience safety)
        result = emit(
            "voting.results_published",
            target=self,
            context={
                "member": member,                     # → registrant resolver (drops no-account/no-email; respects email opt-out)
                "member_name": member.display_name,
                "cycle_label": self.cycle_label,
                "allocation_summary": self.allocation_summary(),
                "vote_1st": vote["guild_1st_name"],
                "vote_2nd": vote["guild_2nd_name"],
                "vote_3rd": vote["guild_3rd_name"],
                "voting_url": "/guilds/voting/history/",
            },
            url="/guilds/voting/history/",
            period=f"snapshot:{self.pk}:send:{n}",     # distinct per send → resend re-delivers; same-send re-runs dedupe
        )
        sent += result.delivery_count and 1 or 0
    self.results_sent_at = timezone.now()
    self.save(update_fields=["results_sent_at", "results_send_count"])
    return sent
```

- **Idempotency:** the first send stamps `results_sent_at`; a second call without `resend=True` raises `ResultsAlreadySentError` (a domain exception, per CLAUDE.md). The UI hides the plain "Send results" button once `results_sent_at` is set, so the only way to re-send is the explicit, warned **Resend**.
- **Resend safety:** each send uses a fresh `period` (`…:send:<n>`), so the EventDelivery dedupe doesn't silently swallow an intentional resend, while a double-clicked *same* send (same `n`, racing requests) is deduped per member.
- **Audience safety** is layered: the loop skips non-`ACTIVE` voters; the `registrant` resolver drops members with no linked `User` / no usable email; the spine's `enabled_channels` honors each member's `voting.results_published` email opt-out (in-app stays forced-on). See §7.4 for the exact filters.
- `save(update_fields=...)` deliberately re-triggers the Airtable sync hook (acceptable; the snapshot row is what syncs).
- **Synchronous-send latency:** `send_results()` runs in the admin's request and sends N emails inline through `core.email.send` (the app has no queue — see the instructor-welcome spec). For a ~100-voter cycle that's a noticeable wait, so the Send/Resend control shows a loading state while in flight (`hx-disabled-elt="this"` + a spinner/"Sending…" label — §6.2) and the button can't be double-submitted. The single batched `Member` query above keeps the DB cost flat; the cost is the inline SMTP calls. If this ever becomes too slow, moving the loop to the scheduler/a background task is the future fix (noted §10) — out of scope here.

### 5.3 Cycle-timing helpers (`membership/voting.py`)

- The module's existing `REMINDER_OFFSET = timedelta(hours=-48)` constant (`membership/voting.py:35`) and the "48h"-specific module docstring are **removed/rewritten** — the lead time is now read from `VotingSettings` per tick, so a fixed module constant is wrong.
- `closing_soon_occurrences(now)` (renamed from `closing_48h_occurrences`): if `VotingSettings.load().reminders_enabled` is False → yield nothing. Else compute `close = month_end_close(now)`, `offset = timedelta(days=-settings.reminder_lead_days)` (the setting is validated ≥1, so the fire time is always *before* the close — §4.1), and **yield one `ScheduledOccurrence` per member** in the "has voted" audience (§7.4), each carrying that member's vote context + `period=cycle_period(now)` (`voting:YYYY-MM`). Event key `voting.closing_soon`. Because every occurrence shares the same `anchor`/`offset`, `run_due` fires them all in the same 15-min window; each member's `EventDelivery` (`user:<pk>` × `voting:YYYY-MM`) dedupes to once per cycle.
- `vote_soon_occurrences(now)` (new): if `not (reminders_enabled and send_vote_soon_enabled)` → yield nothing. Else, same anchor/offset, **one occurrence per member** in the "logged in, never voted" audience (§7.4), event key `voting.vote_soon`, `period=cycle_period(now)`.
- `previous_cycle_label(now) -> str`, `close_period(now) -> str`, and `cycle_start(now) -> datetime` (new): the label (`"May 2026"`), the EventDelivery dedupe bucket (`"voting_close:2026-05"`), and the first instant of the month that **just closed** relative to `now` (an aware datetime — midnight on the 1st of the *previous* month). Used by the auto-snapshot job (the last for the duplicate-snapshot guard, §5.5).

### 5.4 `send_voting_reminders` command (generalize)

`handle()` becomes `run_sources([closing_soon_occurrences, vote_soon_occurrences], now=now)`. Same cron placement (already in the always-run list). Each source self-gates on the settings switches and windows its own member query. Nothing else changes — `run_sources` + `EventDelivery` already give the dedupe.

### 5.5 `take_cycle_snapshot` command (new auto-snapshot job)

Modeled on `send_lease_expiry_reminders` (EventDelivery-deduped, idempotent, no-op outside its window). Added to the `run_scheduled_tasks` always-run list.

```python
def handle(self, *args, **options):
    settings = VotingSettings.load()
    if not settings.auto_snapshot_enabled:
        self.stdout.write("Auto-snapshot disabled.");  return
    now = timezone.now()
    period = close_period(now)                 # "voting_close:2026-05" once we're into June
    label = previous_cycle_label(now)          # "May 2026"
    # Claim the once-per-cycle slot on the same EventDelivery ledger (the locked dedupe).
    _row, created = EventDelivery.objects.get_or_create(
        event_key="voting.auto_snapshot", target_ref="cycle", channel="system", period=period)
    if not created:
        self.stdout.write("Auto-snapshot already handled this cycle.");  return
    # Match on the cycle WINDOW, not the free-text label: any snapshot taken since the
    # closed cycle began means an admin already captured it (even with a custom title).
    if FundingSnapshot.objects.filter(snapshot_at__gte=cycle_start(now)).exists():
        self.stdout.write("A snapshot already exists for this cycle — skipping auto-take.");  return
    snapshot = FundingSnapshot.take(title=label, is_auto=True)   # take() notifies admins, does NOT email members
    self.stdout.write(self.style.SUCCESS(f"Auto-took snapshot '{label}'." if snapshot else "No votes — nothing to snapshot."))
```

- **Idempotency (the "avoid double-firing" requirement):** the `get_or_create` on `EventDelivery(event_key="voting.auto_snapshot", target_ref="cycle", channel="system", period="voting_close:YYYY-MM")` is the authority — the unique constraint makes the first tick of the new month claim the slot and every later tick (or a racing concurrent tick) a no-op.
- **Duplicate-record guard (not by label):** the second check matches on the cycle **window** — `snapshot_at >= cycle_start(now)` (the first instant of the just-closed month) — so an admin who already took the month's snapshot **with any title** suppresses the auto-take. (The earlier `filter(cycle_label=label)` only caught the exact default label and a custom-titled manual snapshot would have slipped through → a duplicate `is_auto=True` record + a spurious "review & send" banner for an already-handled cycle. The EventDelivery slot already prevents *re-firing*, so that was a duplicate-record bug, not a runaway — fixed here.)
- **On the EventDelivery sentinel:** `EventDelivery`'s docstring (`core/models.py:820`) says it folds in the legacy `ScheduledNotificationMarker` dedupe and that `period` is a free-form bucket, which is why it's the natural home for this once-per-cycle marker. Note `channel` and `event_key` are stored as free `CharField`s (not enum-constrained), so the `channel="system"` / `event_key="voting.auto_snapshot"` sentinel is a **convention choice** this spec is making (the model doesn't formally bless a non-enum channel), kept distinct from any real event key so it never collides with a delivery row.

### 5.6 Switch the hardcoded pool floor to the setting

Every site that currently hardcodes the $1,000 floor reads `VotingSettings.load().minimum_pool_floor` instead:

| Call site | Today | Change |
|---|---|---|
| `membership/models.py:1399` | `minimum_pool: Decimal | int = 1000` default in `take()` | default `None` → resolve from `VotingSettings` (§5.1). |
| `membership/management/commands/take_funding_snapshot.py:22` | `--minimum-pool` arg `default=Decimal("1000")`, then `take(minimum_pool=...)` — **bypasses `VotingSettings` entirely** | change the arg `default` to `None` so an un-passed flag falls through to the `VotingSettings` floor inside `take()` (an explicit `--minimum-pool` still overrides). |
| `plfog/dashboard.py:13,35,82,83` | `MINIMUM_FUNDING_POOL_FLOOR = 1000` (used in `max()` + two context keys) | load the floor once at the top of `dashboard_callback` from `VotingSettings`; `dashboard_callback` feeds **both** the Unfold admin dashboard and the hub `admin_voting_dashboard` (`hub/views.py:2025`), so this one change updates both. |
| `plfog/admin_views.py:110,170,201` | `DEFAULT_MINIMUM_POOL = Decimal("1000")` (default for `_parse_minimum_pool`) | default from `VotingSettings`. **Note:** Spec 1 may relocate/replace this Unfold snapshot analyzer with the hub Snapshots tab; **if Spec 1 deletes this file, this row is moot** — coordinate at build time. |

`calculate_results(..., minimum_pool=...)` (`membership/vote_calculator.py:25`) is unchanged — it already takes the floor as an argument; only its *callers* switch their source.

---

## 6. UI / UX  (completeness checklist applied, concretely)

> All admin screens live inside **Spec 1's** Voting tab shell at `/manage/voting/` (Overview / Funding History / Snapshots / Settings). This spec fills the **Settings** tab and adds the **Overview** banner + the per-snapshot **Send/Resend** control. Every section below names real artifacts. **Email content is the exception to the web styling rules** — see the branded-emails spec; the email body is the spine's branded `_base.html` shell, inline-styled by design.

### 6.1 Settings tab — the VotingSettings form

- **Screen / partial:** `templates/hub/admin/voting_settings.html` — **Spec 1's existing flat Settings-tab template**; this spec supplies its form contents (Spec 1 ships the tab nav + page chrome via `_voting_tabs.html`).
- **Layout & container:** inline form on the tab — it's a 5-field settings form (4+ fields → inline page form per the FRONTEND.md interaction table), wrapped in a `<div class="hub-card">`. Not a modal.
- **Components used:** `components/form_field.html` for every field — it auto-renders the three booleans (`reminders_enabled`, `send_vote_soon_enabled`, `auto_snapshot_enabled`) as **toggle switches** (`components/toggle.html`) and the two numeric/decimal fields (`reminder_lead_days`, `minimum_pool_floor`) as themed inputs. No raw `{{ field }}`, no raw checkboxes.
- **Controls, named:**
  - **Save:** a single `<button type="submit" class="hub-btn hub-btn--primary">Save voting settings</button>` at the bottom of the card. POSTs to **Spec 1's existing `voting_settings` view at `/manage/voting/settings/`** — this spec **adds the POST/save branch** to that view (Spec 1 created the GET shell), it does not introduce a second view or route. On success the view sets `messages.success(request, "Voting settings saved.")` and redirects back to the Settings tab (full-page form → Django messages, matching `ClassSettings`). Not an HTMX/toast flow.
  - **Email-copy links:** below the form, a small "Email wording" sub-card listing each voting email with an **"Edit wording"** link out to the existing copy-editor:
    - *Polls closing soon!* → `/manage/notifications/voting.closing_soon/email/edit/`
    - *Vote soon!* → `/manage/notifications/voting.vote_soon/email/edit/`
    - *Results are in!* (members) → `/manage/notifications/voting.results_published/email/edit/`
    - *Results ready* (admins) → `/manage/notifications/voting.results_ready/email/edit/`
    Each link opens the existing edit-copy page with its live preview and version history — no new editor is built. (The catalogue's "Voting" category section already lists these for free.)
- **States:**
  - **Empty / first load:** `VotingSettings.load()` lazily creates the row with the defaults (floor $1,000, lead 3 days, all switches on); the form renders those defaults — never a blank/500 screen.
  - **Validation / error:** `VotingSettingsForm` validates in the form layer (not the view): `reminder_lead_days` must be **≥ 1** (the `MinValueValidator(1)` + a friendly message "Send the reminder at least 1 day before close." — 0 and negatives both rejected; see §4.1 for why 0 misfires), and `clean_minimum_pool_floor` rejects negatives ("The pool floor can't be negative."). Errors render inline under each field via `form_field.html`; the page returns 200 with the bound form (no redirect, no lost input).
  - **Success:** green Django message banner "Voting settings saved." on the reloaded tab.
  - **Loading:** none needed (synchronous full-page POST).
- **Dark + light + mobile:** all controls inherit `.hub-form-group` / `form_field.html` theme input tokens (`--hub-input-bg` / `--hub-input-border` / `--text`) — no inline `background`/`color`, no `var(--surface)`. No `<select>` (so no option-popup styling needed) and **no date/time inputs** (lead time is a plain number), so the calendar-picker dark-mode caveat doesn't apply. The card and form reflow to a single column on narrow screens; toggles are real tap targets. Verify both themes. The Save button gets the standard margin so it clears the last field (8px grid).

### 6.2 Overview tab — "Results are in — review & send" banner

- **Screen / partial:** `templates/hub/admin/voting_overview.html` (**Spec 1's flat Overview template**) renders the banner at the top, plus a NEW shared partial `templates/hub/admin/_results_send_control.html` for the Send/Resend control (reused on Spec 1's `voting_snapshots.html` + `voting_history_detail.html`).
- **When it appears:** the Overview view queries `FundingSnapshot.objects.filter(results_sent_at__isnull=True).order_by("-snapshot_at")` and keeps those with `results_pending` (has a real allocation). If any exist, the banner shows the **most recent** one: a `hub-card` with an attention treatment (use the existing hub "info/attention" card modifier — no new color introduced) reading **"Results are in for {cycle_label} — review & send."** with the pool total and an `is_auto` "Auto" pill when applicable, plus:
  - a **"Review numbers"** link → the **admin** snapshot audit detail (**Spec 1's `voting_history_detail` at `/manage/voting/history/<pk>/`** — *not* the member-facing `hub_snapshot_detail`, which Spec 1 leaves unchanged),
  - a **"Send results"** primary button (`hub-btn hub-btn--primary`) that opens a **confirm modal** (`components/confirm_modal.html`, `confirm_button_style="primary"`): title "Email this cycle's results?", message "This emails all members who voted in {cycle_label} the allocation and their own recorded vote. In-app notifications go out too." → POSTs to `send_results`.
  - **Stacked pending:** the banner shows only the most recent pending snapshot. If more than one is pending (rare — e.g. a month was never sent), sending the top one re-renders the banner and the **next** pending snapshot surfaces on the reload/OOB swap, so they're cleared one at a time rather than hidden.
- **Send action (HTMX mutation → toast + OOB):** the **Send results** confirm POSTs to the `send_results` hub view, which calls `snapshot.send_results(actor=request.user)`. The view returns the re-rendered `_results_send_control.html` (the new "sent" state) with `trigger_toast(response, "Results sent to {n} members.", "success")`, and `hx-swap-oob` updates the Overview banner (collapses it / flips it to the sent state, surfacing the next pending snapshot if any). Per FRONTEND.md, an HTMX mutation returns a toast, not a Django-messages redirect.
- **Loading state:** because the send is synchronous and may email ~100 members (§5.2), the Send/Resend button carries `hx-disabled-elt="this"` and an `hx-indicator` spinner / "Sending…" label so it disables and shows progress in flight and can't be double-submitted.
- **Sent state:** once `results_sent_at` is set, the control reads **"Results sent {results_sent_at:%b %-d, %-I:%M %p}"** with a secondary **"Resend"** button (`hub-btn hub-btn--sm`) behind its **own** confirm modal carrying a stronger warning: "Results for {cycle_label} were already sent on {date}. Resend to every voter? They'll get a fresh email and notification." → POSTs `send_results` with `resend=1` (same loading state). The plain "Send results" button is absent in this state (idempotency made visible).
- **Empty state (no pending results):** no banner — the Overview shows its normal stats (Spec 1). Explicitly: when every snapshot has been sent, the "review & send" region renders nothing rather than an empty card.
- **Error state:** if `send_results` is called for an already-sent snapshot without `resend` (e.g. a stale tab), the view catches `ResultsAlreadySentError` and returns the control re-rendered in its sent state with an `error` toast: "Those results were already sent." — never a 500. A snapshot with no votes/allocation can't reach this control (it isn't `results_pending`).
- **Dark + light + mobile:** banner + control use `hub-card` + `pl-`/`hub-btn` classes and theme tokens only; no inline styles. On mobile the banner stacks (text, then buttons full-width); buttons are real tap targets. Verify both themes.

### 6.3 Per-member email content (what the member actually sees)

All three member emails render via the spine's branded `_base.html` shell (navy card, logo, footer) with the per-member merge fields substituted (autoescaped) by `rendered_message`. The admin previews/edits each via the existing copy-editor's live preview (the documented `sample_context` drives it).

- **Polls closing soon! (`voting.closing_soon`)** — to members who voted. Body: greeting by name, "the {cycle_label} guild funding vote closes on {closes_on}", then **their recorded vote**: "You're currently voting — 1st: {vote_1st}, 2nd: {vote_2nd}, 3rd: {vote_3rd}. Change it any time before close:" + the vote link.
- **Vote soon! (`voting.vote_soon`)** — to members who signed in but never voted. Body: greeting, "you haven't cast a guild funding vote yet for {cycle_label} — it closes {closes_on}. It takes a minute and decides where the pool goes:" + the vote link. No vote merge (they have none).
- **Results are in! (`voting.results_published`)** — to members who voted. Body: greeting, "votes for {cycle_label} are counted", the `{allocation_summary}` block (per-guild $ + %), then **"You were recorded as voting — 1st: {vote_1st}, 2nd: {vote_2nd}, 3rd: {vote_3rd}."** + the history link.
- **Admin preview path:** each event's edit page (`/manage/notifications/<key>/email/edit/`) shows the live wrapped preview using the event's `sample_context` (which must include every placeholder — see §7.1), so admins edit the wording without sending.

### 6.4 The admin notification email (`voting.results_ready`)

Plain admin-facing copy (in-app + email to FOG_ADMINS): "A funding snapshot for {cycle_label} was taken ({funding_pool} pool, {votes_cast} votes). Review the numbers and send results to members:" + `{review_url}`. This is the email/in-app twin of the Overview banner — it's what tells an admin to go click Send.

---

## 7. Notifications / emails / events

### 7.1 Event registry + copy changes (`core/events/registry.py`, `core/events/copy.py`)

| Event key | Change | recipient | channels | activity_kind |
|---|---|---|---|---|
| `voting.closing_soon` | **Repurposed/renamed** from `voting.closing_48h` | `REGISTRANT` (was `ALL_VOTERS`) | in-app ON + email ON (**Discord removed**) | `None` |
| `voting.vote_soon` | **New** | `REGISTRANT` | in-app ON + email ON | `None` |
| `voting.results_published` | **Resolver + activity changed** | `REGISTRANT` (was `ALL_VOTERS`) | in-app ON + email ON | `None` (was `funding_snapshot_taken` — now logged once in `take()` so per-member sends don't write N activity rows) |
| `voting.results_ready` | **New (admin-facing)** | `FOG_ADMINS` | in-app ON + email ON | `None` (the snapshot-taken activity is logged in `take()`) |

Registry constants: `VOTING_CLOSING_48H → VOTING_CLOSING_SOON = "voting.closing_soon"`; add `VOTING_VOTE_SOON = "voting.vote_soon"`, `VOTING_RESULTS_READY = "voting.results_ready"`. `membership/voting.py`'s import updates accordingly.

Curated copy (`_CURATED`) — keep placeholders and `sample_context` in **lock-step** (the seed command + a test assert it):
- `voting.closing_soon`: placeholders `(member_name, cycle_label, closes_on, vote_1st, vote_2nd, vote_3rd, voting_url)`.
- `voting.vote_soon`: placeholders `(member_name, cycle_label, closes_on, voting_url)`.
- `voting.results_published`: placeholders `(member_name, cycle_label, allocation_summary, vote_1st, vote_2nd, vote_3rd, voting_url)` — adds the three vote fields to today's set.
- `voting.results_ready`: placeholders `(cycle_label, funding_pool, votes_cast, review_url)`.

**Deploy step:** after deploy, run `python manage.py seed_notification_templates`. `seedable_rows()` iterates `all_events()`, so the two new events get DB copy rows automatically; the renamed `closing_soon` row (renamed in §4.3) is refreshed to the new copy if not overridden, preserved if overridden (admins who hand-edited the old 48h copy keep theirs, minus the new vote line, until they re-edit — accepted, mirrors the branded-emails spec).

### 7.2 Per-recipient context — the load-bearing mechanism

`emit()` builds **one** `Message` per channel and reuses it across all resolved recipients (`core/events/emit.py:122-127`); the `context` is global, not per-recipient. So a single broadcast `emit()` to `ALL_VOTERS` **cannot** show each member their own vote. Personalization is therefore done by **emitting once per member**, each call carrying that member's `context` and a **per-member resolver** (`REGISTRANT`, which reads `context["member"]`):

- Reminders: the scheduler sources yield **one `ScheduledOccurrence` per member** (§5.3). All share the same `anchor`/`offset`, so they're all due in the same tick; each fires its own `emit` with that member's vote context; `EventDelivery` (`user:<pk>` × `voting:YYYY-MM`) dedupes per member per cycle.
- Results: `FundingSnapshot.send_results()` loops `raw_votes` and emits per member (§5.2), pulling each member's frozen vote straight from the snapshot.

The `member` object rides in `context` for the resolver; the rendering layer only substitutes the documented **string** placeholders and ignores the object.

### 7.3 Audience: results email sourced from `raw_votes`

`FundingSnapshot.raw_votes` already freezes each voter's `member_id` + the three guild names at snapshot time — exactly "what we recorded *you* voting for." Sourcing the results loop from `raw_votes` (rather than live `VotePreference`) means the email reflects the snapshot, not later vote edits, and naturally scopes the audience to **members who voted**. Non-voters aren't emailed (they have no recorded vote); they can view results on the history page.

### 7.4 Audience-safety filters (mandatory — exact querysets)

Each audience excludes no-email members, non-`ACTIVE` members, and respects opt-outs:

- **"Polls closing soon!" (voted):**
  `Member.objects.active().filter(vote_preference__isnull=False).select_related("user", "vote_preference__guild_1st", "vote_preference__guild_2nd", "vote_preference__guild_3rd")`
  → per-member occurrence with vote context from `member.vote_preference`.
- **"Vote soon!" (eligible, logged in, never voted):**
  `Member.objects.paying().active().filter(user__last_login__isnull=False, vote_preference__isnull=True).select_related("user")`.
  `.paying()` is included because voting eligibility tracks paying membership (matching the `all_voters` resolver / its docstring) — nudging a non-paying member who can't vote would be noise.
- **"Results are in!" (voted, still active):** loop `snapshot.raw_votes`; per entry `Member.objects.filter(pk=vote["member_id"], status=Member.Status.ACTIVE).first()`; skip `None`.

In all three the per-member `registrant` resolver (`core/events/resolvers.py:210` → `_member_user`) then drops any member with no linked `User` or no usable email, and the spine's `enabled_channels` (`core/events/preferences.py:67`) honors each member's per-channel `NotificationPreference` — so a member who opted out of that event's **email** still gets the (forced) in-app bell but no email. Suspended/former members are excluded by the `status=ACTIVE` filter. **Confirmed:** the spine already does per-user, per-channel opt-out resolution; this spec adds no new opt-out logic, only the correct querysets feeding it.

### 7.5 Activity log

`take()` logs exactly one `SiteActivity("funding_snapshot_taken", target=snapshot)` (so the per-member results emails — `activity_kind=None` — never write duplicate rows). The admin `voting.results_ready` and the member reminder/results events log no activity.

---

## 8. Build order (phased; each phase ships green)

1. **`VotingSettings` model + form + Settings tab.** Model (`load()`), `CreateModel` migration, `VotingSettingsForm` with validation (lead ≥ 1, floor ≥ 0). **Extend Spec 1's existing `voting_settings` view** with the POST/save branch (no new view/route) and fill Spec 1's `voting_settings.html` with the form + copy-editor links. *(Settings tab usable; no behavior change yet.)* **Depends on Spec 1's Settings tab shell + route.**
2. **Pool floor → setting.** Switch the **four** call sites (§5.6 — `take()` default, `take_funding_snapshot.py`, `dashboard.py`, `admin_views.py`) to `VotingSettings.load().minimum_pool_floor` / `minimum_pool=None`. *(Behavior identical at the default $1,000.)*
3. **Snapshot fields + `take()`/`send_results()` rework + admin event + badge.** Add `is_auto`/`results_sent_at`/`results_send_count` (+ backfill data migration), rewrite `FundingSnapshot.source_label` to return "Automatic"/"Manual" from `is_auto` and add the `.pl-vote-badge--auto` pill CSS in `hub.css` (Spec 1 handoff), register `voting.results_ready`, make `take()` stop auto-sending + notify admins + log activity once, add `send_results()` + `ResultsAlreadySentError`. Update the tests that assert auto-send (§9). *(The core behavior change lands here.)*
4. **Overview banner + Send/Resend controls.** `send_results` HTMX view + its one new route, the banner in Spec 1's `voting_overview.html`, the shared `_results_send_control.html` (surfaced on Spec 1's `voting_snapshots.html` + `voting_history_detail.html`), confirm modals + toast + OOB + loading state. **Depends on Spec 1's Overview/Snapshots/history-detail templates.**
5. **Events repurpose/rename + per-member copy.** Rename `voting.closing_48h → voting.closing_soon` (registry + copy + the §4.3 data migration), add `voting.vote_soon`, switch `voting.results_published` resolver to `REGISTRANT` + add vote placeholders + sample contexts.
6. **Reminder automation.** `closing_soon_occurrences` + `vote_soon_occurrences` (per-member, settings-gated, configurable lead), **remove `REMINDER_OFFSET` + rewrite the "48h" `voting.py` docstring**, `send_voting_reminders` → `run_sources([...])`.
7. **Auto-snapshot job.** `previous_cycle_label`/`close_period`/`cycle_start` helpers, `take_cycle_snapshot` command (EventDelivery-deduped), wire into `run_scheduled_tasks` always-run list.
8. **Housekeeping.** Re-seed deploy note; `ruff format . && ruff check .`; **at build time** bump `plfog/version.py` VERSION + a member-friendly CHANGELOG entry.

> Spec only — do not build until approved. Each phase must leave the full suite + `ruff` + `mypy` green.

---

## 9. Testing

BDD `*_spec.py`, `describe_*` / `it_*` only (**`context_*` is not collected** — use `describe_*` for nested blocks), factory-boy, `respx` only if HTTP (none expected — email is captured via `mail.outbox`), run in the `plfog-web` Docker image, ≥98% coverage, full type hints. Use the existing `tests/membership/`, `tests/core/events/`, `tests/core/` (commands), and `tests/hub/` spec trees and factories (`MemberFactory`, `VotePreferenceFactory`, `FundingSnapshotFactory`, `MembershipPlanFactory`).

**`VotingSettings` (`tests/membership/...`):**
- `load()` returns the pk=1 singleton, creating it with the documented defaults; a second `load()` returns the same row; `save()` can't create a second row.

**`VotingSettingsForm` / Settings view (`tests/hub/...`):**
- Saves all five fields **through Spec 1's `voting_settings` view's POST branch**; success message set; redirects to the Settings tab.
- `reminder_lead_days` of `0` **and** `-1` each raise the form validation error (min is 1), and `minimum_pool_floor = -5` raises (no save, bound form re-rendered); `reminder_lead_days = 1` saves.
- The tab renders the three booleans as toggles and links to each voting event's copy-editor URL.

**`FundingSnapshot.take()` / `send_results()` (`tests/membership/...`):**
- `take()` **does not** send `voting.results_published` to members (assert no member results in `mail.outbox` / no member `EventDelivery`), but **does** emit `voting.results_ready` to admins and logs exactly one `funding_snapshot_taken` `SiteActivity`.
- `take(minimum_pool=None)` uses `VotingSettings.load().minimum_pool_floor`; an explicit `minimum_pool` overrides.
- `take(is_auto=True)` sets the flag; default is `False`. `source_label` returns "Automatic" when `is_auto` else "Manual".
- `send_results()` emits `voting.results_published` once per **active** voter, each with their own `vote_1st/2nd/3rd` from `raw_votes`, and stamps `results_sent_at` + `results_send_count = 1`.
- **Idempotency:** a second `send_results()` without `resend` raises `ResultsAlreadySentError` and sends nothing; with `resend=True` it re-delivers (fresh `period`) and bumps `results_send_count`.
- **Audience safety:** a voter who is now `FORMER`/`SUSPENDED`, has no linked user/email, or opted out of the `voting.results_published` email is excluded from the email (the opted-out one still gets the in-app row).
- `results_pending` is True for a fresh snapshot with allocation, False after send, False for a vote-less/legacy snapshot.

**Migrations (`tests/membership/...`):** the backfill sets `results_sent_at = snapshot_at` / `results_send_count = 1` on pre-existing snapshots (so they don't appear pending); reverse nulls them.

**Reminder sources (`tests/membership/voting_spec.py` / `tests/core/events/...`):**
- `closing_soon_occurrences` yields one per voted member with vote context, due exactly `reminder_lead_days` before close; honors a changed lead time; yields nothing when `reminders_enabled` is False; re-running the same cycle is a deduped no-op (`voting:YYYY-MM`).
- `vote_soon_occurrences` targets **only** paying, active, logged-in-no-vote members — a non-paying logged-in-no-vote member is excluded; yields nothing when `send_vote_soon_enabled` (or `reminders_enabled`) is False; deduped per cycle.
- A voter and a logged-in-no-voter each get the correct, distinct email; a never-logged-in member with no vote gets neither.

**Auto-snapshot (`tests/core/...` command spec):**
- Fires once per cycle (claims the `voting_close:YYYY-MM` `EventDelivery` slot); a second tick the same cycle is a no-op; flags the snapshot `is_auto=True`; pings admins, does not email members.
- No-op when `auto_snapshot_enabled` is False, and when there are no votes.
- **Window-based manual guard:** a manual snapshot taken during the closed cycle **with a custom title** (not the default label) still suppresses the auto-take (the guard matches `snapshot_at >= cycle_start`, not the free-text label) — assert no `is_auto=True` row is created in that case.

**Overview view + `send_results` HTMX (`tests/hub/...`):**
- The banner appears only when a `results_pending` snapshot exists; absent otherwise.
- `send_results` POST sends + returns a success toast + the sent-state control; calling it again returns the error toast (no second send) unless `resend` is set.

**Date-window gotcha:** all timing tests freeze `timezone.now()` (the project tz) so `month_end_close`, the `reminder_lead_days` window, and `previous_cycle_label`/`close_period` (December → January rollover) are deterministic.

---

## 10. Open / deferred / out of scope

**Out of scope (deliberately):**
- **Any voting-window lockout / read-only member page** — soft close was chosen; the voting page stays open.
- **"Former-role" filtering** — Spec 1's concern, and dropped there.
- **Any new email-text editor** — we reuse the existing hub copy-editor; this spec only registers events + curates copy + links to it.
- **A generic Discord "voting closes soon" broadcast** — removed from the personalized `closing_soon` event (a per-member email isn't a broadcast). If a community-channel ping is wanted later it's a separate, generic broadcast event; deferred.
- **Spec 1's tab shell, the Snapshots tab, snapshot delete/recalc, and the per-member audit** — this spec consumes them (banner on Overview, `is_auto` badge, Send/Resend control surfaced on Snapshots) but does not build them.

**Deferred / open questions:**
- **Reminder audience: all voters vs. paying voters.** "Polls closing soon!" is specced for *any* active member who has voted (they've clearly engaged). If we'd rather match the strict `all_voters` (paying-only) audience, add `.paying()` to the §7.4 queryset — flagged, defaulting to "anyone who voted."
- **Results email to non-voters.** Today's behavior emailed all voters (voted or not); this spec narrows the results email to **members who voted** (so the "your recorded vote" line is always meaningful). If admins want the allocation broadcast to all members, that's a future toggle.
- **Resend re-notifies in-app too.** A resend writes a fresh in-app bell row per member (new `period`). Acceptable (the admin chose to resend); noted.
- **Synchronous results send.** `send_results()` emails N voters inline in the admin's request (the app has no queue). The loading state (§6.2) covers the UX; if a large cycle makes the wait unacceptable, moving the loop onto the scheduler / a background task is the future fix — out of scope here.
- **New token/color/component:** none introduced. The banner reuses an existing hub "attention/info" card treatment and `is_auto` reuses an existing pill modifier — **if no suitable existing attention style exists, flag before adding one** (do not introduce a new color unilaterally).

**Note:** the version bump (`plfog/version.py` VERSION) + the member-friendly CHANGELOG entry happen **at build time**, one entry for the release, not now.

> Spec only — do not build until approved.
