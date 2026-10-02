# Registration closes a set number of hours before a class starts

Round: 2026-10-02 instructor feedback round, PR 4 of 7.

**User story.** As an instructor, when my class is two days away, I would like registration to close so I can buy materials and set up for the people who are coming. Currently anyone can register right up to the minute the first session starts.

**Summary.** Each class closes registration a number of hours before its first session (48 by default); the class can change the number or turn the cutoff off, and the class page says when registration closes and why it is closed.

## Design decisions (locked)

- **One nullable field on the class**: `ClassOffering.registration_cutoff_hours = PositiveIntegerField(null=True, blank=True, default=48, help_text=...)`. Null means the cutoff is off. The migration gives every existing class 48, which is what Felix asked for; the PR body says so plainly (a class starting within 48 hours of the deploy closes at once).
- **The catalog keeps listing the class until it starts.** `ClassOfferingQuerySet.bookable()` and `ClassOffering.is_bookable` keep their meaning ("not started", read by the catalog, the hub calendar, Discord posts and 20 other call sites) and are **not** changed. The cutoff is a new, narrower gate:
  - `ClassOffering.registration_closes_at -> datetime | None`: first session start minus the hours; None when the class is flexible, has no sessions or the cutoff is off.
  - `ClassOffering.registration_open -> bool`: `is_bookable` and (`registration_closes_at` is None or still in the future).
  - `ClassOfferingQuerySet.registration_open()`: `bookable()` filtered to `Q(registration_cutoff_hours__isnull=True) | Q(scheduling_model=FLEXIBLE) | Q(first_session_at__gt=Now() + F("registration_cutoff_hours") * timedelta(hours=1))` (an `ExpressionWrapper` with `output_field=DurationField()`). It must pass on SQLite (local) and PostgreSQL (CI, `DATABASE_URL=postgres://plfog:plfog@localhost:5433/plfog`); prove both.
- Where the new gate applies: the `register` view (redirect to the detail page with the closed message), the detail page rail (CTA and copy), the register page's run switcher (`_bookable_run_options`), the detail page's "Other Dates for This Class" (only runs still open). The waitlist closes with registration: a closed class offers no waitlist.
- Flexible classes are untouched (no start time to count from); the field is hidden for them in the composer and ignored by every gate.
- A series counts from its **first** session.

## Expected behavior

**Composer** (step 3, "Dates, Seats And Price", inside the fixed scheduling block under the dates, in both `ClassOfferingForm` and `TeachClassOfferingForm`): a toggle **"Close registration before the class starts"** (`components/form_field.html` renders a checkbox as a toggle) and a number field **"Hours before the first session"** (min 1, max 720, default 48) that shows only while the toggle is on (`x-show`). The form fields are `registration_cutoff_enabled` (BooleanField, not a model field) and `registration_cutoff_hours`; `clean()` writes null when the toggle is off and errors "Enter how many hours before the class registration should close." when it is on and blank. Both fields join step 3 in `classes/composer.py` (the step map guard spec enforces this). Field hint: "Students cannot register once this many hours remain before the first session. Turn it off to take sign-ups right up to the start."

The published class light edit (instructor) does not expose it; the dates are locked there and this rides with them. An admin changes it through the admin composer.

**Class page rail** (`templates/classes/public/detail.html`), in this order of precedence:
- registration closed by the cutoff (class not started, `registration_open` false): the red "Registration closed" line, then the hint **"Registration has closed. Sign-ups end {N} hours before class starts."** No CTA, no waitlist.
- open with a cutoff: under the CTA (or the waitlist CTA), a muted line **"Registration closes {N} hours before class starts ({closes_at|date:"D, M j, g:i A"})."**
- open without a cutoff, started, flexible, site switch off: exactly as today.

**Register view**: a closed class redirects to its page with the message "Registration has closed. Sign-ups end {N} hours before class starts."

**Catalog card** (`_schedule_option_row.html` and the single run rows): a run that is listed but closed shows a `cls-spots` pill reading **Closed** in place of the spots count. Read `registration_open` off the prefetched sessions without a query per row (`earliest_session_at` runs a query; add a prefetch aware path or annotate `first_session_at` on the catalog queryset and read it; prove with `django_assert_num_queries` or `assertNumQueries` that the catalog's query count does not grow with the number of classes).

**Reminder and other jobs**: untouched.

## Acceptance criteria

- [ ] Model: `registration_closes_at`, `registration_open`, `registration_open()` behave as above for single, series, flexible, undated, cutoff off, cutoff on, boundary exactly at the cutoff instant (closed at and after).
- [ ] The queryset filter passes on SQLite and PostgreSQL; the PR body shows both commands.
- [ ] Register view: closed → redirect with the message and no Registration row created; open → unchanged.
- [ ] Detail page: the three rail states above (anchor assertions on markup: a `data-registration-closed`/`data-registration-closes-at` attribute, not on copy).
- [ ] Catalog: a closed run shows the Closed pill; an open run shows spots; the query count is flat.
- [ ] Composer: toggle off saves null; toggle on with 24 saves 24; on with blank errors; the fields sit on step 3 in `classes/composer.py`; the flexible block hides them.
- [ ] Migration adds the field with default 48; `python manage.py check` and `makemigrations --check` clean.
- [ ] `tests/e2e/`: grep for the composer step walk specs (`class_composer_steps_spec.py`) and run them locally; the new fields must not break the step walk.
- [ ] Screenshots: the composer fields, the rail open with the closes line, the rail closed, the catalog Closed pill.
- [ ] Both themes.

## Out of scope

A site wide default in `ClassSettings` (48 is the field default), changing when the catalog hides a class, the orientations and community events.

## Files

`classes/models.py`, `classes/migrations/`, `classes/forms.py`, `classes/composer.py`, `classes/views.py` (register, detail context, catalog, run options), `templates/classes/_components/class_composer.html`, `templates/classes/public/detail.html`, `templates/classes/public/_schedule_option_row.html`, `templates/classes/public/_class_card.html`, specs under `classes/spec/`.
