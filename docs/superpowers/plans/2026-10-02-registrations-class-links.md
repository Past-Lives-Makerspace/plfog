# Registrations lists link the class name to its class page

Round: 2026-10-02 instructor feedback round, PR 1 of 7.

**User story.** As an admin, when an instructor asks me which class a payment was for, I would like the class name on the Registrations list to open that class, so I can answer from the list. Currently the class name is plain text and I have to open the registration and click through from there.

**Summary.** On every registrations list the class name is a link to the class's own screen.

## Current behavior

- `templates/classes/admin/registrations.html`: the Class column renders `{{ r.class_offering.title }}` as plain text. Only the order number and the registrant name link, both to the registration detail.
- `templates/classes/teach/registrations.html`: each class group's header shows the title in bold inside a clickable collapse bar (Alpine `open` toggle); the title is not a link.
- `templates/classes/admin/registration_detail.html` already links the class to `classes:teach_class_detail`. That is the target to reuse.

## Expected behavior

- Admin registrations list: the Class cell is `<a href="{% url 'classes:teach_class_detail' pk=r.class_offering.pk %}">{{ r.class_offering.title }}</a>`.
- Teach registrations page: the group header title is the same link. The header bar keeps its collapse behavior; the link carries `@click.stop` so clicking the name navigates instead of toggling. Add `hx-boost` nothing special; the page is already boosted by the hub base.
- No new styling beyond the existing table link look. Verify both themes by eye (links already use theme tokens).

## Acceptance criteria

- [ ] On `/classes/admin/registrations/` every row's class title is a link whose href is that class's `teach_class_detail` URL.
- [ ] On `/classes/teach/registrations/` every group header's class title is a link to the same URL, and clicking it navigates (the collapse toggle does not swallow it).
- [ ] Specs: one for each page asserting the href is in the body (anchor on the URL, not on copy; the changelog renders on every page). Put them beside the existing specs for those views (`classes/spec/views/`).
- [ ] `ruff check .`, `ruff format --check .`, `tests/template_comment_lint_spec.py` pass.

## Out of scope

- The CSV export, the registration detail page (already linked), the public account pages.
- Any change to what the class screen shows.

## Files

`templates/classes/admin/registrations.html`, `templates/classes/teach/registrations.html`, `classes/spec/views/` (new or extended spec).
