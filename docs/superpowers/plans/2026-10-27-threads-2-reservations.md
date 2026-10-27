# One conversation thread everywhere (part 2 of 3): equipment reservations

Builds on part 1 (the shared thread). Same locked decisions: same look and composer with photos,
posting always notifies the other side and only then, admins are always staff side, one time
reasons stay as fields and are also posted into the thread.

## Scope
- `EquipmentReservation` gets a message thread between the **member who booked** and the
  **equipment managers of that item** (the audience `equipment.reservation_needs_approval` reaches,
  plus admins). Member message notifies the managers minus the author; manager message notifies
  the member.
- Shown on the member's reservation (Reservations page / their booking rows and any reservation
  detail) and on the manager side (approval queue, Bookings tab, `equipment_manage.html`), with a
  clear "Messages" affordance and unread cue where rows are listed.
- `purpose` stays a field (it is the booking's one line label).
- Decline and manager cancel keep their required `cancelled_reason` field; the reason is also
  posted as a manager message in the same action, in one notice (no second notification).
- A reservation awaiting approval: the member can ask, the manager can answer before deciding.
- Help Center copy for reservations mentions messages.

## Out of scope
Credits (`EquipmentCredit.note` stays a one shot note). Manager blocks (no member). Kiln.
