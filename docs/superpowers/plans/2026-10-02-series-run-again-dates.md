# Running a series again lands on its dates

Round: 2026-10-02 instructor feedback round, PR 5 of 7.

**User story.** As an instructor with a three session series (Blacksmithing 101), when I want to teach it again to a second group, I would like one obvious move that gives me a second run with its own three dates and its own seats. Currently "Run it again" exists on the class page but drops me on step 1 of a six step composer with a message that does not say what to do next, and the dates step never says whether to add the new group's dates here or make a new run.

**Summary.** Run it again opens the new run on its Dates step with plain instructions, and the series help text says when to add dates versus when to run the class again.

## How it works today (keep)

- A series is one class with several dates and one ticket (`scheduling_type = series_package`); a student enrolls once for every date.
- "Run it again" (`teach_class_duplicate_run`, `ClassOffering.duplicate_as_new_run`) clones the class as a draft with no dates, same title, same `grouping_key`, so the catalog shows one card with a "Pick a session set" list and each run keeps its own seats. The clone goes through review again. That policy stands in this PR (Felix decides separately whether a re-run of an approved class may skip review).
- An instructor's published class locks its dates; "Request a change" asks an admin.

## Expected behavior

1. **Land on the dates.** `teach_class_duplicate_run` redirects to the new run's composer at step 3 (`?step=3`, read by `clamp_step`) and the success message reads: **"This is a new run of {title}. Add its dates below, then submit it for review. Everything else came across from the original."**
2. **Confirm modal copy** (`class_overview.html`, the `run-again` confirm): title "Run this class again?", message **"We make a draft copy with no dates. Add the new group's dates and submit it for review. Students sign up for each run separately, and the catalog shows both runs under one card."**, button "Make a Draft Copy".
3. **Series help text** (`scheduling_type_field.html`, the series radio): **"A course that meets on several dates. Students enroll once and attend every date, so add only this group's dates here. Teaching it again to a new group? Use Run it again on the class page."**
4. **Dates step note** (`class_composer.html`, the fixed block's `pl-compose-section__note`): keep the first sentence and add: "Adding dates for a second group? Do not add them here; use Run it again on the class page so each group gets its own seats."
5. **Published light edit** (`class_form_published.html`, the locked summary): under the dates list add one muted line: "To offer this class to another group, use Run it again on the class page." (The class page is `teach_class_detail`; link the words "Run it again" to it.)

No dashes in any of this copy.

## Acceptance criteria

- [ ] View spec: POST to `teach_class_duplicate_run` as the instructor redirects to `teach_class_edit` for the new run with `?step=3`, and the message text is in the messages storage.
- [ ] Template specs for 2 to 5 anchor on markup where possible (the modal id `run-again`, the radio value `series_package`, a `data-run-again-hint` attribute on the new lines) and on the factory title for the message.
- [ ] The composer step walk e2e (`tests/e2e/class_composer_steps_spec.py`) still passes; run it locally.
- [ ] Screenshot of the dates step with the new note and the message after Run it again.
- [ ] Both themes.

## Out of scope

Skipping review for a re-run (policy question for Felix), adding dates to a published series from the instructor's page, a "Run it again" button on the Manage My Classes list.

## Files

`classes/views.py` (`teach_class_duplicate_run`), `templates/classes/teach/class_overview.html`, `templates/classes/_components/scheduling_type_field.html`, `templates/classes/_components/class_composer.html`, `templates/classes/teach/class_form_published.html`, specs under `classes/spec/views/`.
