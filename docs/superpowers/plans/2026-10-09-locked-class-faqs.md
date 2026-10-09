# Locked class FAQs: cancellation and accessibility

Felix, 2026-10-09: "Can we lock these two questions? So instructors cannot change the copy?" (screenshot: "What's your cancellation policy?" and "Is the space accessible?" on a class page FAQ).

## Today
- `DEFAULT_CLASS_FAQS` (classes/models.py) holds three starting questions: cancellation, accessibility, prior experience. The class FAQ formset (`build_class_faq_formset`, classes/forms.py) seeds them as editable rows; the first save materializes them as `ClassFaq` rows the instructor can reword or delete.
- `ARRIVAL_CLASS_FAQ` is already site policy: always appended by `ClassOffering.display_faqs`, never an editable row. That is the precedent to follow.
- Prod (read 2026-10-09): 39 classes hold materialized copies of both questions; 37 carry the current default copy, classes 553 and 667 an older cancellation answer (710 chars, not 718).
- Eventbrite (`core/integrations/eventbrite.py` ~371) sends only `offering.faqs.all()`.

## Acceptance criteria
1. Cancellation and accessibility become `LOCKED_CLASS_FAQS` (site policy, copy lives in code like the arrival FAQ). `DEFAULT_CLASS_FAQS` keeps only the prior experience question.
2. Every class page's FAQ shows the two locked questions first, with the code's copy, then the class's own rows (or the remaining default when it has none), then the arrival question, as today. A class row whose question matches a locked question (case and surrounding space ignored) is never shown, so a locked question appears exactly once.
3. The FAQ formset (composer step 4, published class edit, admin composer) shows the two locked questions read-only above the editable rows, labelled so an instructor understands they are set by Past Lives and the same on every class. No input, no delete button for them.
4. A crafted POST cannot change them: a submitted row whose question matches a locked question fails validation with a plain message (for example "This question is set by Past Lives for every class. Ask a different question.").
5. A data migration deletes existing `ClassFaq` rows whose question matches a locked question (both the current and the older copy go; the locked copy replaces them). Reverse is a no-op.
6. Eventbrite's FAQ widget sends the locked questions first, then the class's own rows (arrival stays excluded, as today).
7. Cloning still copies the class's own rows; nothing duplicates the locked ones.
8. Admins are locked too: the copy changes only in code.

## Out of scope
- An admin screen to edit the locked copy.
- The arrival question (already locked).
- The "Why teach" page FAQ (`ClassSettings.teach_page_faq`).
