# Choose who each announcement emails ("send to some, not all") — Spec & Implementation Plan

**Status:** Spec only — approved to build (v23).
**Date:** 2026-07-17
**Surface:** FOG hub — the announcement **compose wizard** (`hub_compose`, `templates/hub/announcement_compose.html`), Step 2 (Email).
**Related:** `2026-07-17-guild-settings-mailing-list.md` (**hard dependency** — reuses `GuildMailingListEmail` + `emit(extra_emails=…)`; build this AFTER it lands).

---

## 1. Summary

When a guild lead composes an announcement, they can now **pick which recipients get the email** instead of always emailing everyone. Step 2 of the wizard shows the guild's full mailing list — members and custom addresses — as a checklist, everyone checked by default, with select-all / none. Deselecting someone removes them from **this announcement's email only**; the in-app notification still reaches all members and the single Discord channel post is unchanged (a channel broadcast can't target individuals). The selection posts with the compose form and is honored on send. (The wizard's draft save/resume UI is currently hidden — see `2026-07-13-hide-announcement-drafts.md` — so the selection lives for the one compose→send request; the draft field is the carrier, not a resumable store.)

### Locked decisions (from user + scout)

| Decision | Choice |
|---|---|
| What the selection controls | **Email only.** In-app bell still goes to all members; Discord posts once to the channel. The UI says so. |
| Default | **All selected** — same reach as today unless the lead opts out specific people. |
| Selectable rows | Guild **members** (keyed by `User.pk`, from `announcement_recipients()`) + **custom addresses** (from `GuildMailingListEmail`). |
| Send mechanism | A new **preference-preserving subset filter** on emit's EMAIL channel (NOT `email_to`, which bypasses member opt-outs). Selected members ride the normal per-recipient path restricted to chosen pks; selected custom addresses ride `extra_emails` (from the mailing-list feature), filtered to the chosen set. |
| Opt-outs | **Honored.** A member who turned off announcement emails still doesn't get one even if selected — the subset filter keeps the existing `preferences.enabled_channels` check (the reason to avoid `email_to`). |
| Count semantics | The wizard's confirm/toast reports the **email** recipient count (the selected subset) and notes the in-app notice still reaches all members. |
| Persistence | A new field on `AnnouncementDraft`, default "all", roundtripped through `save_from_form` / `_draft_initial` / the compose form. |

## 2. What already exists (reuse, don't reinvent)

Confirmed on `origin/main` (+ the mailing-list feature landing just ahead of this).

| Need | Existing thing | Location |
|---|---|---|
| Compose view (single form, Alpine 3-step) | `hub_compose` | `hub/views.py:2003` |
| Compose form (add the recipient field here) | `AnnouncementComposeForm` | `hub/forms.py:1849` |
| Compose template — Step 2 is Email (gated on `alsoEmail`) | `announcement_compose.html:53` | — |
| Draft model + persistence (add the field here) | `AnnouncementDraft`, `save_from_form`, `_draft_initial` | `membership/models.py:2513,2653`; `hub/views.py:1942` |
| Draft send (branch guild → notify_members) | `AnnouncementDraft.send` | `membership/models.py:2682` |
| The guild send that emits | `GuildAnnouncement.notify_members` | `membership/models.py:2163` |
| Recipient rows to render (User + reason, email-guaranteed) | `Guild.announcement_recipients()` → `guild_members` resolver | `membership/models.py:1660`; `core/events/resolvers.py:161` |
| The recipients list markup to turn into a checklist | v22 "Show recipients" collapsible | `templates/hub/guild_edit.html:571-590`; context `hub/views.py:698-700` |
| Per-recipient email fan-out + the preference check to preserve | `_per_recipient_fan_out` (`preferences.enabled_channels`) | `core/events/emit.py:151-172,307` |
| Custom-address additive path (from the dependency) | `emit(extra_emails=…)` | `core/events/emit.py` (added by the mailing-list feature) |
| Email identity key | `user.email` (delivery) / `User.pk` (dedup) | `core/events/channels.py:148-160`; `resolvers.py:60` |

### Genuine gaps to close

1. A **draft field** for the selection.
2. A **compose-form field** (checklist) + validation (⊆ the resolvable set).
3. An **emit EMAIL-subset filter** (`email_only_user_ids`) that preserves preferences and leaves bell/Discord whole.
4. **Send wiring:** map the selection → chosen member pks (subset filter) + chosen custom addresses (`extra_emails`), through `notify_members`.

## 3. Where the code lives

```
core/events/emit.py            # + email_only_user_ids param: restrict the EMAIL channel of _per_recipient_fan_out to chosen pks
membership/models.py           # + AnnouncementDraft.email_recipient_selection field; save_from_form persist; send() maps selection ->
                               #   notify_members(selected_user_ids=…, selected_custom_emails=…); recipient_count() email semantics
hub/forms.py                   # + recipient checklist field on AnnouncementComposeForm (choices from announcement_recipients + custom)
hub/views.py                   # _draft_initial seed (default all), _render_compose context for the checklist
templates/hub/announcement_compose.html   # Step 2 checklist UI (all-checked, select all/none, "email only" note)
tests/... (membership, hub, core/events)  # emit subset filter, draft roundtrip, send mapping, form validation, template
```

## 4. Data model

**`AnnouncementDraft.email_recipient_selection`** (`membership/models.py`):

| Field | Type | Notes |
|---|---|---|
| `email_recipient_selection` | `JSONField(default=dict, blank=True)` | `help_text="Which recipients get this announcement's email. Empty/absent = everyone (default). Shape: {\"users\": [pk,…], \"custom\": [\"addr\",…]}."` |

An **empty/absent** value means "all" (backward-compatible with existing drafts; no data migration of meaning). Only a **non-empty** selection restricts. Migration: `AddField` with `default=dict` (auto-reversible). *(Alternative — two M2Ms — is heavier and needs cleanup on prune; a JSON snapshot is simplest.)* **Persistence is dormant:** the wizard's draft save/resume UI is hidden (`2026-07-13-hide-announcement-drafts.md`), so in practice this field only carries the selection within the single compose→send POST (`save_from_form` → `send`), exactly like the rest of the draft fields today. Store it anyway (one field, no cost) so it's ready if drafts return; don't build resume-seeding UI around it.

## 5. Business logic

**Emit — the one spine change (preference-preserving, additive, default-off):**
- Add `email_only_user_ids: set[int] | None = None` to `emit()`. When `None` (every existing caller) → no change. When a set, the **EMAIL** channel of `_per_recipient_fan_out` is sent **only** to resolved users whose `pk` is in the set; **all other channels (in-app bell, push) still fan out to every resolved user**, and the per-user `preferences.enabled_channels` check still runs (so opt-outs win). This is deliberately NOT `email_to` (which suppresses all resolver emails and skips preferences).
- Concretely: in the per-recipient loop, when `email_only_user_ids is not None and user.pk not in email_only_user_ids`, drop `EMAIL` from that user's channel set (keep the rest). Leave `extra_emails` / `email_to` untouched.

**`GuildAnnouncement.notify_members`** gains two optional kwargs: `selected_user_ids: set[int] | None` and `selected_custom_emails: list[str] | None`, both default `None` = "everyone" (today's behavior). It **still computes the full deduped custom list** (the mailing-list feature's `extra_emails` choke point stays exactly as shipped — do NOT remove it):
```python
member_emails = {u.email.strip().lower() for u, _ in recipients}
all_custom = self.guild.mailing_list_emails_deduped(member_emails)
effective_custom = all_custom if selected_custom_emails is None else [e for e in all_custom if e in set(selected_custom_emails)]
emit(..., email_only_user_ids=selected_user_ids, extra_emails=(effective_custom if self.send_email else None), ...)
```
So `selected_custom_emails=None` (every existing caller — the guild-edit create view, `GuildAnnouncement.approve`) → the FULL custom list still sends (no regression to the mailing-list feature); a selection only ever *narrows*.

**`AnnouncementDraft.send`** (guild branch) translates the saved `email_recipient_selection` into those kwargs. **The empty-vs-absent distinction is pinned:** an **absent/`{}`** selection means "all" → pass `None`/`None`; a **present** selection means "exactly these" (never re-expands to all). A present selection is intersected with the *current* resolvable roster so a member who left or a deleted custom row can't resurrect:
- `selected_user_ids = {int(pk) for pk in sel["users"]} & {u.pk for u, _ in recipients}`
- `selected_custom_emails = [e.lower() for e in sel["custom"] if e.lower() in set(all_custom)]`
- The **site** branch is untouched (no guild mailing list; site keeps all-subscribers).

Guard against the footgun: an empty selection (`{"users": [], "custom": []}`) with `send_email=True` is rejected at the form (§5 validation) and never stored as "all" — so "Select none" can't silently email everyone.

**Counts — reconcile ALL of them (they diverge today).** Three surfaces show a count and all currently reflect the full guild: the Step-3 reach line (`announcement_compose.html:98`), the irreversible send **confirm** (`:110`, "Send to ${recipientCount}"), and the post-send **success message** (`hub/views.py:2146`, a Django `messages.success`, not a toast). The server-seeded `recipientCount` (Alpine, `:16,20`, updated only by the `hub_compose_count` HTMX) cannot see checkbox state. So:
- Drive a client-side **`selectedEmailCount`** off the checkboxes and use it in the Step-3 reach line and the confirm string: *"Email {selectedEmailCount} · everyone in the guild still sees it in the app."*
- Post-send: `AnnouncementDraft.send()` returns **both** the email count (subset) and the full member total, and the view's success message reads *"Emailed {n} of {m} · everyone sees it in the app."* (compute `m` before send if simpler than threading a tuple).
- When email is off (`send_email=False`) the selection is moot and the checklist is hidden; the counts revert to bell/Discord reach.

**Validation** (`AnnouncementComposeForm.clean`): the submitted selection must be a subset of the current resolvable rows (members + custom for the chosen guild); unknown ids are dropped, not errored (a roster can change between load and submit). An empty submission (deselected everyone) with `send_email=True` → a form error "Pick at least one email recipient, or turn off Also send email."

## 6. UI / UX

- **Screen:** `templates/hub/announcement_compose.html`, **Step 2 (Email design)**.
- **Layout & container:** a collapsible **"Email recipients"** card at the top of Step 2, gated on **`x-show="alsoEmail && audience.startsWith('guild')"`** — it appears only for a **guild** audience with email on. For a **site** audience the card is absent and the form builds **empty** recipient choices (the deselect-all error in §5 is scoped to `audience==guild and send_email`, so a valid site send never trips it). Header: "**Email recipients**" + a `.pl-help` tooltip: *"Everyone is included by default. Uncheck anyone you don't want to email this time — they'll still see it in the app, and it still posts to your Discord channel."* Summary: "**Emailing {selectedEmailCount} of {total}**" (live via Alpine off the checkbox state).
- **Components / controls, named:**
  - A **checklist** — one row per member (label = display name + `user.email`) keyed `value="user:{pk}"` and per custom address keyed `value="custom:{addr}"`, each a **real styled checkbox**, all **checked by default**. This is a re-implementation, not a reuse (the `guild_edit.html:571-590` list is email-only `<li>` with the pk discarded): define a `pl-recipient-checklist` class in `hub.css`, and **explicitly set the checkbox `accent-color` to a theme token and verify contrast in both light and dark** (the checkbox is the one control the "verify both themes" rule must name here). No `toggle.html` (that's a single boolean switch, wrong component for a multi-select list).
  - **Select all / Select none** buttons — **`hub-btn hub-btn--sm`** (the compose surface uses `hub-btn`, not `pl-btn`) — toggle every checkbox and refresh `selectedEmailCount` (Alpine).
  - The card is **collapsible** (`x-show`/`x-collapse`, collapsed by default with the summary line always visible) so a 100-member guild doesn't dominate the step.
  - The checkboxes live inside the single compose `<form>` (`announcement_compose.html:28`) — no nested form. Send stays the Step-3 submit.
- **States:**
  - *Empty guild roster* (no emailable members, no custom): the card shows "No email recipients yet — add members or addresses on your Guild Settings mailing list"; email send is a no-op (existing behavior).
  - *Deselected everyone + email on (guild):* inline form error (§5), re-rendered on the compose step with the checkboxes preserved.
  - *Guild switched mid-compose:* the recipient checklist is **re-rendered for the newly-chosen guild** by extending the `hub_compose_count` HTMX response to OOB-swap the checklist partial (same mechanism that already OOB-swaps the Discord channel picker, `hub/views.py:2050-2062`) — otherwise a multi-guild lead keeps the previous guild's roster and sends the wrong subset.
  - *Success:* the existing post-send **`messages.success`** (not a toast — this is a full-page POST), reworded to "Emailed N of M · everyone sees it in the app" (both numbers from `send()`).
- **Dark + light:** checkboxes via the shared component / `.pl-form-group`; `.pl-help` is global. No inline colors. Verify both themes.
- **Mobile:** the checklist is a single stacked column of full-width tap targets; the collapsible keeps it short; no table.

This is **not** the famous list-editor (no add/delete of rows here — the roster is managed on the Guild Settings mailing list); it's a selection control, so the +Add/Delete rubric doesn't apply, but the checkboxes must be real styled controls (not raw) and the select-all/none + count must work.

## 7. Notifications / emails / activity

No new triggers. The only change is that the guild-announcement **email** channel can now reach a chosen subset while the in-app notification and Discord post stay whole-guild. Member opt-outs are still honored (the subset filter keeps the preference check). Custom addresses (non-members) have no preferences — they're emailed iff selected.

## 8. Build order (each phase green) — **after the mailing-list feature lands**

1. **Emit subset filter** — `email_only_user_ids` param + the EMAIL-only restriction, with tests proving: bell still reaches all, email reaches only the subset, opt-outs still suppress, `None` = unchanged for all existing callers. Green. *(Highest-risk — shared spine.)*
2. **Draft field + send mapping** — `email_recipient_selection` field/migration; `save_from_form` persist; `send`/`notify_members` map selection → `email_only_user_ids` + filtered `extra_emails`; `recipient_count` email semantics. Green.
3. **Form + checklist UI** — compose-form field + validation + Step 2 checklist template + `_draft_initial` default-all + select-all/none. Green.
4. **Version + changelog** — folded into the v23 mailing-list entry or its own: *"When you post a guild announcement you can now choose exactly who gets the email — everyone's included by default, uncheck anyone you want to skip."* (Handled centrally.)

## 9. Testing

- **Emit (critical):** `email_only_user_ids={a}` → user a gets email, user b does NOT, but BOTH get the in-app bell; a selected user who opted out of the email still gets none; `None` → all email as before (regression guard across the many emit callers); Discord broadcast fires once regardless.
- **Draft roundtrip:** selection persists on save, reloads on resume, empty = all; a stale pk / removed custom row is dropped at send (intersect with current roster).
- **Send mapping:** guild announcement with a subset emails only the chosen members + chosen custom addresses (deduped, case-insensitive), bell to all members, one Discord post; email-off ignores selection.
- **Form:** default all-selected; subset validated ⊆ roster; deselect-all + email-on → friendly error.
- **Template:** checklist renders all-checked with the count + select-all/none + the "still in the app / Discord" note; collapsed by default; hidden when email is off.

## 10. Open / deferred

- **Saved recipient groups / segments** (reusable "just the officers") — deferred; per-send selection is enough.
- **Site announcements subset** — out of scope; this is guild-only (site keeps all-subscribers).
- **Showing who was emailed on the sent record** — nice-to-have; not required for v1.

> Spec only — build under v23, **after** the mailing-list feature lands (shares `emit.py`, `membership/models.py`, the guild recipient plumbing).
