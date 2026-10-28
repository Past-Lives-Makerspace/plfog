# One conversation thread everywhere (part 3 of 3): classes

Builds on part 1 (the shared thread). Same locked decisions: same look and composer with photos,
posting always notifies the other side and only then, admins are always staff side, one time
reasons stay as fields and are also posted into the thread.

## Scope
- `ClassOffering` gets a message thread between the **class's instructors** (all equal
  instructors, #686) and its **reviewers** (the existing audience: the guild lead and Class
  Reviewers, else the CMS Administrators for a lead-less category, i.e.
  `guild_class_reviewers_or_cms_admins`; admins always). An instructor message notifies the
  reviewers minus the author; a reviewer message notifies the instructors minus the author.
- Shown on the instructor's class pages (composer / teach class overview, near "Where Your Class
  Is") and on the review page (`templates/classes/admin/class_review.html`), replacing the separate
  note history display there with the thread.
- Review decisions keep `ClassApproval.notes` (it is the decision's record) and ALSO post the note
  into the thread as the reviewer's message in the same action, one notice (the existing decision
  email carries it; no second notification).
- Instructor change requests on a live class (`ClassOffering.request_change`) post into the thread
  as the instructor's message (still reaching the same audience, one notice).
- The instructor can reply in words at any time, including while changes are requested, without
  resubmitting.
- Migration: existing non blank `ClassApproval.notes` are copied into the thread as reviewer
  messages (author = the approval's decider if recorded), oldest first, dated at the decision.
- Help Center copy for class review mentions messages.

## Out of scope
Discount code requests, instructor inquiries, registrations, roster announcements (one way). Kiln.
