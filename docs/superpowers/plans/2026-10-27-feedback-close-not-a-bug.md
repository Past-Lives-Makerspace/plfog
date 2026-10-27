# Close a feedback request, and Not a bug (feedback desk, part 2 of 2)

Asked by Felix on 2026-10-27: "Also not an easy way for me to mark something as not a bug with easy
instructions for them on the right solution. I need a way to close feedback tickets."

Builds on part 1 (`2026-10-27-feedback-thread.md`): comments, Member replied, Webmaster notices.

## Decided (locked, from Felix)

- **Not a bug** is a one click resolution on a bug report that closes it and **requires a written
  comment** telling the sender the right way to do the thing. The comment box starts with a short
  opener the admin edits, e.g. "Thanks for reporting this. It is working as intended. Here is how to
  do it:". Written each time; no saved replies.
- **Close** is available on every request, general feedback included, with an optional comment.
- **A sender's reply on a closed request leaves it closed** but flags it "Member replied" (part 1's
  pill and filter), so it still lands in front of the admin. The sender can always reply.

## Behavior

1. `FeedbackRequest.Status` gains `NOT_A_BUG` ("Not a bug") and `CLOSED` ("Closed"). The closed
   statuses are Live (Fixed), Not planned, Not a bug and Closed; give the model one place that says so
   (e.g. `CLOSED_STATUSES` and an `is_closed` property). Both new statuses notify the sender, in the
   same single notice as any comment posted with them. Status pills: neutral tone for both.
2. General feedback may now move to Closed (and only Closed or Received). Update `update_refusal` and
   the admin form's choices; Not a bug is offered only on bug reports. Not a bug refuses without a
   comment, like Not planned.
3. Admin request page: two buttons beside Mark Fixed/Live: **Not a bug** (bug reports only) and
   **Close**. Each opens or uses a comment box (Not a bug prefilled with the opener and required;
   Close optional) and posts status plus comment in one save, one notice. Plain POST views like
   `hub_admin_feedback_mark_live`, `@fog_admin_required`, `@require_POST`.
4. Inbox: a status filter of Open (default), Closed, All, alongside the existing filters. Open means
   not in the closed statuses, OR flagged Member replied (so a reply on a closed request is never
   hidden by the default view). The existing Received to-do pile keeps working.
5. Sender's Feedback page: a closed request reads as closed (its status pill), keeps the reply box,
   and a short line under it: "This request is closed. Reply here if you still need help."
6. Reopening is the existing status select (move back to Received, Planned or Building). Moving off
   Not a bug or Closed does not clear the thread.
7. `announce_live` / `live_announcement_skip`: a request that is Not a bug or Closed is skipped by the
   release job (it would otherwise flip a closed ticket to Live).
8. Help Center: describe Close and Not a bug for admins in the same article part 1 touched.

## Out of scope

Saved replies. Auto closing stale requests. Reopening on reply. Staff only notes. Satisfaction
ratings.

## Carried from part 1 (#966)

9. Drop `FeedbackRequest.staff_note`, kept through part 1 for the deploy window. First copy any
   non blank `staff_note` left on a request into a comment (no author, as part 1's 0129 did), then
   remove the field. Reverse restores the column (blank).
