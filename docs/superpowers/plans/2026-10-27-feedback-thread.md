# Feedback requests become a conversation (feedback desk, part 1 of 2)

Asked by Felix on 2026-10-27: "I want to be able to send notes to the people who submitted them as
comments that they get notified ... An actual commenting system where they can comment back and add
more images. We're basically zendesk now."

## Problem

A feedback request (`core.FeedbackRequest`, #693) carries one `staff_note` field. Each save
overwrites it, so there is no history, and whether a save notifies the sender depends on a rule the
admin cannot see (note text changed and not blank, or a move to a notifying status). The sender
cannot answer at all, and cannot add photos after sending.

## Decided (locked, from Felix)

- Every message on a request is a **comment** in one thread, newest last, shown on both sides: the
  sender's Feedback page (`hub_beta_feedback`, the request's open row) and the admin request page
  (`hub_admin_feedback_request`).
- **Posting a comment always notifies the other side**, every time, and only then. The admin's
  button says who hears it ("Send to Amber") and the toast confirms ("Sent. Amber was notified.").
  Status saves stay separate from comments.
- Both sides may attach photos to a comment, with the same caps and field as the Feedback form
  (`MultiplePhotoField`, `MAX_FEEDBACK_PHOTOS`, `MAX_FEEDBACK_PHOTO_TOTAL_BYTES`).
- **A sender's comment notifies the Webmasters** (`Recipients.WEBMASTERS`, the `WEBMASTER`
  `AdminCapability`; Felix alone in prod), never a settings email list.
- **`BETA_FEEDBACK_EMAILS` is retired.** The new request email that `BetaFeedbackForm.send` sends to
  it goes to the Webmasters instead (same content, photos attached). Remove the setting, its env
  example line and any spec that reads it. If no one holds Webmaster, the send logs and drops (the
  request is saved regardless, as today).
- No staff only (internal) comments. Out of scope.

## Behavior

1. Model: `FeedbackComment` (request FK, author User FK, body text, created_at) and photos for it
   (either a new `FeedbackCommentPhoto` or a nullable `comment` FK on `FeedbackRequestPhoto`, builder's
   choice, whichever is simpler). `is_from_staff` is derived: author is not the request's sender.
2. Admin page: the thread renders under the original message and photos (the original message is the
   first item). Below it, a comment box with photos and a "Send to <sender>" button. The old
   "Note to the member" field is gone. The status / GitHub link form stays, with status hints that say
   exactly who is notified.
3. Not planned still needs a reason: moving to Not planned requires a comment in the same save (the
   status form gets an optional comment box that is posted as a comment when filled; Not planned
   refuses without it, in the model's words). A status move to a notifying status with a comment sends
   ONE notice carrying both.
4. Sender's Feedback page: each request's row shows the thread and a reply box with photos. Only the
   sender (and admins via their page) can see or post on a request; a crafted POST to another user's
   request is a 404.
5. Notices: an admin comment emits `feedback.request_updated` (or a new `feedback.comment_added` event
   to the sender, builder's call, keep copy plain), carrying the comment text, linking the request.
   A sender comment emits a new event to `Recipients.WEBMASTERS` linking the admin request page,
   carrying the text. Register the event in `core/events/registry.py` with copy in its usual place and
   whatever the settings matrix / parity specs require. No dashes in member facing copy.
6. Inbox: a request whose newest comment is from the sender and that no admin has opened since shows
   a "Member replied" pill, and the inbox has a filter for it. Opening the admin request page marks it
   seen (store `staff_seen_at` on the request, or similar).
7. Migration: each existing non blank `staff_note` becomes the first staff comment on its request
   (author: the request's most recent admin actor if knowable, else leave author null and render it as
   "Past Lives"), then `staff_note` is dropped. Two prod rows carry a note today.
8. `mark_live` and `announce_live` keep working; they no longer touch a note.
9. Update the Help Center article(s) that describe the Feedback page / inbox in
   `membership/help_content.py` so they describe comments and replies.

## Out of scope

Closing, Not a bug, and closed ticket behavior (part 2). Staff only notes. Saved replies. Editing or
deleting comments. Email reply by mail (replying to the email does not post a comment).
