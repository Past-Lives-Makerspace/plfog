# 371 item 2a: a Guild Lead's approval survives the instructor's next submission

**Issue:** #371 item 2a. **Status:** build ready.

## The defect

`ClassOffering.submit_for_review` (`classes/models.py:1230`) opens every submission with
`self.approvals.all().delete()`, then creates the first stage gate from scratch. Because
`_create_first_stage_approval` always picks Guild Lead when the category's guild has one,
the lead is asked again on every round trip:

1. Instructor submits. Guild Lead gate opens, lead emailed.
2. Lead approves. `_escalate_to_admin` opens the Admin gate. The lead is done.
3. Admin asks for changes. The class bounces to DRAFT.
4. Instructor fixes the copy and resubmits.
5. The delete destroys the lead's APPROVED row, the Guild Lead gate reopens, and the lead
   is emailed again, about a class whose dates they already signed off, over an edit they
   were not part of.

`send_guild_lead_review_reminder` then re-sends daily while that reopened gate is open.

The other three `approvals.all().delete()` sites are **correct and must not change**:
`withdraw` (`:1453`), `restore` from archive (`:1549`) and `unpublish` (`:1578`) are full
resets to DRAFT where starting review over is the intended behaviour.

## The ruling on the issue's open question

The issue left one decision open: should a date change re-ask the lead? **Yes, and only a
date change.** A guild lead's stated job is date and space availability, so the schedule is
the one edit that genuinely needs them again; a retitled description is not their business.
This is the issue's own reasoning, taken as the decision.

Consequence: skipping the lead requires knowing **what they approved**, and nothing records
it today. `ClassSession` has no timestamps, so the lead's `decided_at` cannot be compared
against the schedule either. One new field is therefore load bearing.

## Design

### 1. `ClassApproval.approved_schedule_fingerprint`

```python
approved_schedule_fingerprint = models.CharField(
    max_length=64,
    blank=True,
    default="",
    help_text="Hash of the session schedule this row approved; lets a later submission "
              "tell whether the dates changed since.",
)
```

Blank by default, so every existing row migrates untouched and reads as "no recorded
schedule". One `AddField` on a small table: cheap DDL, no data migration.

`ClassOffering.schedule_fingerprint` is the producer: a stable digest of the class's
sessions, `sha256` over each session's `starts_at` and `ends_at` in sorted order, hex
digested. Sorted, so reordering `sort_order` alone is not a schedule change. A class with
no sessions has the digest of the empty set, which is a real value and not the empty
string; the empty string means "never stamped" and is the thing that must never be
confused with a match.

`ClassApproval.decide` stamps it when and only when a GUILD_LEAD row is APPROVED.

### 2. `submit_for_review` keeps what was decided in this member's favour

Replace the blanket delete with:

- delete undecided rows (a stale open gate must not survive a resubmission), and
- delete bounce rows (`decision in _BOUNCE_DECISIONS`), and
- **keep APPROVED rows.**

Deleting the bounce rows is not incidental tidying. `_is_bounced` (`:2093`) and the
`bounced` annotation (`:336`) are `exists()` over `_BOUNCE_DECISIONS`, and `lifecycle`
reads DRAFT plus a bounce row as CHANGES_REQUESTED. Keeping a spent bounce row would leave
a freshly resubmitted class reading as bounced on every surface that asks. `latest_bounce_row`
(`:2115`) and `:2311` have the same dependency.

### 3. `_create_first_stage_approval` skips a gate already satisfied

Open the ADMIN gate instead of the GUILD_LEAD gate when **all** of:

- an APPROVED GUILD_LEAD row exists for this class, and
- its `approved_schedule_fingerprint` is non empty, and
- it equals the class's current `schedule_fingerprint`.

Otherwise behave exactly as today. A lead who requested changes or denied has no APPROVED
row, so they are asked again, which is right. A row stamped before this field existed has
an empty fingerprint and so re-asks: the safe direction.

## Traps the build must clear

1. **`on_review_decision_recorded` (`:1727`)** gates escalation on
   `not self.approvals.filter(role=ADMIN).exists()`. Surviving APPROVED rows change what
   that sees. Verify a second cycle still opens the admin gate.
2. **`open_guild_lead_approval` and `guild_lead_approved_at`** (`:2058`, `:2071`) scan
   `approvals.all()`. With a surviving APPROVED lead row plus a fresh ADMIN row, confirm
   each still resolves to the row it means.
3. **`required_review_roles`** decides whether ADMIN is even in play; do not assume it.
4. **The reviewer queues** (teaching dashboard and guild page) filter on the open gate.
   A skipped lead must disappear from them, not linger.
5. `context_*` blocks are not collected by this repo's pytest config. Every nested block is
   `describe_*`.

## Acceptance criteria

1. Lead approves, admin requests changes, instructor resubmits **without touching dates**:
   no new GUILD_LEAD row, the ADMIN gate opens directly, and the lead receives no email.
2. Same sequence but the instructor **changes a session time**: the GUILD_LEAD gate reopens
   and the lead is notified.
3. Same sequence where the lead **requested changes** rather than approving: the GUILD_LEAD
   gate reopens. Their bounce row does not survive to make the class read as bounced.
4. A resubmitted class never reads as bounced: `_is_bounced`, the `bounced` annotation,
   `lifecycle` and `latest_bounce_row` all agree it is in review.
5. A class whose lead approved under the old code (empty fingerprint) re-asks the lead
   rather than silently skipping them.
6. `withdraw`, `restore` and `unpublish` still clear every approval row.
7. Adding, removing or retiming a session changes `schedule_fingerprint`; reordering
   `sort_order` alone does not.

## Out of scope

- The daily reminder cadence (issue #371 decision 2).
- A `cycle` number on `ClassApproval` and the multi round Review History panel. The history
  loss is real but is its own change; this PR keeps approvals rather than restoring rounds.
- Item 2b (`class_published` Discord routing), dropped by the owner.
