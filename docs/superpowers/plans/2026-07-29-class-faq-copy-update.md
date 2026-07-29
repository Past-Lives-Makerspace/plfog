# Class Detail Page FAQ Copy Update

**Date:** 2026-07-29
**Version:** 0.23.47
**Size:** XS — pure copy change, one file (`classes/models.py` `DEFAULT_CLASS_FAQS`)

## What

Update the three default FAQ entries shown on every public class page to match
the approved copy from the Past Lives team. These defaults appear on any class
that has not yet saved custom FAQ rows through the edit form.

**Changed symbol:** `DEFAULT_CLASS_FAQS` in `classes/models.py`

---

## Changes

### Q1 — What's your cancellation policy?

Full policy replacing the old 7-day refund blurb:
- 48-hour cutoff with no fee
- Under 48 hours or no-show: $50 fee ($35 to instructor, $15 to PLM admin)
- Emergency exceptions case-by-case
- Contact: studios@pastlives.space

### Q2 — Is the space accessible?

Old answer claimed step-free entry and ADA restrooms. New answer is accurate:
ramp to first floor, not currently ADA accessible, no ADA restrooms. Directs
questions to studios@pastlives.space.

### Q3 — Do I need any prior experience or skill level?

Old title: "What if I've never done this before?"
New title: "Do I need any prior experience or skill level?"

Answer updated: classes for all skill levels; each listing notes if prior
experience helps.

---

## Scope

- No model changes, no migrations.
- Template unchanged (`urlize|linebreaks` already formats multi-paragraph plain text correctly).
- Tests reference `DEFAULT_CLASS_FAQS` dynamically — no test edits needed.
