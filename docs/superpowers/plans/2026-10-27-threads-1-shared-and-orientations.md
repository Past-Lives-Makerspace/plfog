# One conversation thread everywhere (part 1 of 3): the shared thread, and orientation bookings

Asked by Felix on 2026-10-27, after the feedback thread shipped (#966): "can the
orientations/reservations adopt this cleaner feedback back and forth comment notes thing you
created? same with classes? it looks great so we should standardize it." Then: "no need to show
me, just build it."

## Decided (locked)

- One shared thread, extracted from the feedback thread (`core.FeedbackComment`,
  `templates/hub/partials/_feedback_thread.html`, `_feedback_photos_field.html`,
  `_feedback_photos_script.html`, `FeedbackCommentForm`, `notify_other_side`). Every domain uses the
  same look, the same composer (text plus photos through the shared drop zone, busy submit guard),
  and the same rule: **posting a message always notifies the other side, and only then**. The
  toast names who was told.
- Each domain keeps its own concrete comment and photo tables (the feedback precedent), built on
  shared abstract bases, so there is no generic foreign key.
- One time reasons stay as fields (decline and cancel reasons, credit notes, reservation purpose,
  deny notes); where a reason is written, it is ALSO posted into the thread as a message so the
  conversation reads whole.
- Admins see and can post on every thread (an admin is always on the staff side).
- Kiln keeps its own thread for now (out of scope).

## Part 1 scope

### A. Extract the shared thread (no behavior change for feedback)
- Abstract model bases in `core` (e.g. `ThreadMessage` with author User FK nullable SET_NULL,
  body, created_at, and an abstract photo base), the "who hears it" contract as a small method each
  concrete model implements (staff side vs. member side, recipients, events), and one helper that
  posts a message with photos and calls it.
- One shared template partial (`components/thread.html` or similar) and the shared composer partial
  with the photo drop zone; the feedback pages switch to them. `FeedbackComment` and its photo
  model move onto the bases with no schema change beyond what Django needs (no data loss, no
  renamed tables). Feedback specs and e2e must still pass unchanged in behavior.

### B. Orientation bookings get a thread
- `OrientationBooking` gets a message thread between the **member** and the **orientors** of that
  orientation (the audience `orientation_requested` reaches today:
  `GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS`, plus admins). A member message notifies that audience
  minus the author; a staff side message notifies the member.
- Shown on the member's booking card (wherever the member sees a booking: their orientations list
  and booking detail) and on the leader side (`orientation_respond.html` and the guild/leader
  booking views and modals).
- `member_note` and `lead_note` become messages: a migration copies each non blank value into the
  thread (member_note authored by the member's user, lead_note authored by nobody, signed as the
  guild, dated sensibly), then the fields leave the model state only (column kept with a database
  default for the deploy window, as core 0130 did; a later release drops them).
- Writing paths keep working: the booking request's note becomes the first member message; the
  decline note (required or optional as today) is posted as a staff message with the decline;
  "propose a new time" (#853) gains an optional message posted with the proposal, and the member
  can reply with words, not only Accept/Keep.
- Emails that showed `member_note` / `lead_note` read the newest relevant message instead.
- Help Center copy for orientations mentions messages.

## Out of scope
Reservations and classes (parts 2 and 3). Kiln. Editing or deleting messages. Staff only notes.
Reply by email.
