# Emails tab in Site Settings — one place to see every email the app sends

**Status:** spec + implementation plan
**Author:** Josh (via assistant)
**Date:** 2026-07-30

---

## 1. What & why

Admins have no single place that answers "what emails does this app send, to whom, and
when?" The wording is editable at `/manage/notifications/` (the copy catalogue), the
scheduled-job on/off switches live on the **Automations** tab, and the voting cadence
lives on the **Voting** settings page. Three surfaces, no overview.

Add an **Emails** tab to Site Settings that lists **every email the app can send**,
grouped by category, each row showing:

- **What it is** — label + one-line description.
- **Who gets it** — the resolved audience ("Members who voted", "Guild leadership", …).
- **How it goes out** — channel badges (Email, plus any In-app / Discord the same event
  also fans out to, so the full reach is visible).
- **When it sends** — an **Automatic** vs **Triggered** badge and a plain-language note
  ("Sent 3 days before the monthly guild vote closes" / "Sent when a member registers
  for a class").
- **Adjust** — an "Edit wording" link (to the existing copy editor with live preview),
  and, when the email has an adjustable schedule/toggle, a link to the exact control
  (Voting settings for the vote reminders; Automations for the scheduled jobs).

This is **read-and-route**, not a new editor. It reuses the copy catalogue for wording
and links out to the existing controls for parameters — it does not duplicate their
forms.

**Scope note (deliberate):** we are NOT building a generic per-email trigger/schedule
editor. Triggers are code (emit points + scheduler sources); the only adjustable
schedule parameters that exist today are the voting lead-days/toggles and the job
on/off switches, and both already have homes. The tab surfaces and links to them. A
fully generic scheduler editor is YAGNI.

**Audience:** FOG admins only (`@fog_admin_required`). Not member-facing → **no member
changelog entry.**

---

## 2. Constraints & decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where | New `emails` tab on the existing `admin_site_settings` page | The user asked for a *tab in Site Settings*, next to Automations/Announcements. |
| Wording edit | Link out to the existing `/manage/notifications/<key>/email/edit/` | That editor already has preview + version history + revert. Don't rebuild it. |
| Which events show | Only events that declare an **EMAIL** or **SCHEDULED_EMAIL** channel | "All emails this app can send." An in-app/push/Discord-only event is not an email. |
| Automatic vs triggered | Curated `_AUTOMATIC_EMAILS` set + per-key note; everything else "Triggered" | Honest and low-maintenance. The scheduled member emails are a small known set; the rest are transactional. |
| Schedule note source | Live for voting (reads `VotingSettings.reminder_lead_days`); static sentence otherwise | The lead-days is the one live parameter worth reflecting. |
| Adjust link | Voting settings for vote reminders; Automations for other scheduled jobs; none for triggered | Routes to the real control without duplicating its form. |
| Logic location | New `core/events/email_catalogue.py` service (fat model, skinny view) | Per PLFOG standards — the view just calls `build_email_catalogue()`. |
| POST | None — the tab is a list + links | Nothing to save here; the Save button is hidden on this tab (like Announcements). |

---

## 3. The service — `core/events/email_catalogue.py`

```python
@dataclass(frozen=True)
class EmailRow:
    key: str
    label: str
    description: str
    audience: str            # copy_module.audience_description(event)
    channels: list[str]      # human labels of every channel the event fans out to
    is_automatic: bool       # scheduler-driven (True) vs action-triggered (False)
    schedule_note: str       # plain language: cadence or trigger
    adjust_label: str        # "" when none
    adjust_tab: str          # "" or a Site-Settings tab, e.g. "automations"
    adjust_url: str          # "" or an absolute path to the controlling page

def build_email_catalogue() -> list[tuple[str, list[EmailRow]]]:
    """Every emailing event, grouped by category in registry order."""
```

- **Filter:** `[e for e in all_events() if e.has_channel(EMAIL) or e.has_channel(SCHEDULED_EMAIL)]`.
- **`_AUTOMATIC_EMAILS`:** the scheduler-driven member emails —
  `voting.closing_soon`, `voting.vote_soon`, `voting.officers_closing_soon`,
  `class_reminder`, `event.reminder`, `event.happening_now` (each present only if it
  actually declares an email channel; membership is harmless if not).
- **`schedule_note`:** voting keys →
  `f"Sent {VotingSettings.load().reminder_lead_days} days before the monthly guild vote closes."`;
  other automatic keys → their curated sentence; triggered keys →
  `f"Sent when {description.lower_first().rstrip('.')}."`
- **`adjust_*`:** voting keys → Voting settings page; other automatic keys → Automations
  tab; triggered → none.

Reuses `copy_module.audience_description` and the `notification_views` channel labels
(lift `_CHANNEL_LABELS` into a shared spot or import it) — no new audience/label logic.

---

## 4. View + template

- **View:** add `"emails"` to `allowed_tabs` in `admin_site_settings`; when building the
  render context, add `"email_catalogue": build_email_catalogue()`. No POST branch.
- **Template:** new `templates/hub/admin/_emails_tab.html`, included in
  `site_settings.html` as an `x-show="tab === 'emails'"` block **after** the settings
  `</form>` (like the Announcements/Slideshow-b blocks) so the settings Save button
  isn't implicated. Add the tab button to the tab bar and extend the Save-button
  `x-show` guard to also hide on `emails`.
- **Row UI:** a stacked list of cards. Each: title + description; a row of channel
  badges; an Automatic/Triggered pill (amber for automatic, slate for triggered) + the
  schedule note; a footer with "Edit wording →" and (when present) "Adjust schedule →".

---

## 5. Tests (`tests/core/events/email_catalogue_spec.py` + a hub view spec)

- `build_email_catalogue` includes only emailing events; excludes an in-app-only event.
- Voting rows are `is_automatic=True` and their note reflects a **changed**
  `reminder_lead_days` (set it to 5 → note says "5 days").
- A transactional event (e.g. `registration_confirmed`) is `is_automatic=False` with a
  "Sent when …" note and no adjust link.
- Voting rows link to the Voting settings page; a scheduled non-voting email links to
  the Automations tab.
- Grouping preserves registry category order.
- Hub: `GET /manage/site-settings/?tab=emails` as an admin returns 200 and lists a known
  email label; a non-admin is redirected/403 (existing `@fog_admin_required` coverage).

---

## 6. Out of scope

- Editing email **wording** inline (the linked catalogue owns that).
- A generic trigger/schedule editor or new schedule fields.
- Legacy code-template emails' live preview (they're in the email gallery; a future pass
  migrates them onto the spine).
