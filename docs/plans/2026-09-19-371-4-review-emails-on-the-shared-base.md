# 371 item 4: the four review emails move onto the shared email shell

**Issue:** #371, item 4 ("Review and approval emails lack a refined layout").
**Shape:** a template port. Markup changes, content does not.

## The problem, stated exactly

`templates/membership/emails/_base.html` is the branded shell: masthead link, content card,
palette, shared footer, with `_button.html`, `_hero.html` and `_feature_card.html` beside it.
Eleven membership emails extend it. **Zero of the fourteen in `templates/classes/emails/` do.**

The four in scope are each a hand rolled `<!DOCTYPE html>` document:

| Template | Lines | Sent to |
|---|---|---|
| `review_request.html` | 45 | guild lead or admin, stage one |
| `admin_validation_request.html` | 45 | admin, stage two |
| `review_decision.html` | 62 | the instructor, outcome |
| `review_submitted_instructor.html` | 32 | the instructor, explainer |

They are not ugly. They are **divergent**: no masthead, their own spacing and type scale, and no
route to inherit a future design change. Thirteen of the fourteen already include the shared text
footer, so the footer is the one thing they have in common with everything else.

## Scope

**In:** those four `.html` templates, extending `membership/emails/_base.html` and using
`membership/emails/_button.html` for the CTA.

**Out, and these are traps if touched:**

- **The `.txt` twins.** Plain text has no shell to inherit. Do not touch them.
- **`_review_pipeline.html`.** A genuinely class specific component; the base has nothing like it.
  Keep it rendering inside the content block, unchanged.
- **The other ten templates in `classes/emails/`.** The issue calls them follow up.
- **Copy.** Not one user visible sentence changes. This is the acceptance test for the whole item:
  if a spec asserting on wording fails, the port is wrong, not the spec.
- **`classes/emails.py`.** No send path changes. Same context, same recipients, same subjects.

## What "ported" means here

1. `{% extends "membership/emails/_base.html" %}` and put everything in `{% block content %}`.
2. Delete the template's own `<!DOCTYPE>`, `<html>`, `<head>`, `<body>` and outer wrapper tables.
   The base owns them.
3. Delete the template's own `{% include "membership/emails/_footer.html" %}`. **The base already
   includes it.** Leaving it in means two footers, which is the single most likely bug here.
4. Replace the hand rolled CTA anchor with
   `{% include "membership/emails/_button.html" with href=... label=... %}`. Keep the label text
   exactly as it reads now.
5. Keep every `{% load %}` the template needs (`classes_tags` for `review_pipeline` and
   `cents_as_price`). A `{% load %}` must be the first tag, before `{% extends %}` is fine as it is
   written today; follow whatever the eleven membership templates already do.
6. Inline styles stay inline. Email clients strip `<style>`. Match the base's palette rather than
   reintroducing a second one: `#092E4C` headings, `#33424F` body, `#55697a` muted, `#0d4876` brand.

## Traps, each one already paid for by somebody

- **`{# #}` comments must be single line.** A wrapped one renders as visible text. Run
  `tests/template_comment_lint_spec.py`.
- **The email gallery is a real consumer.** `tests/e2e/email_gallery/registry.py:341` registers
  these four, and `tests/core/email_gallery_completeness_spec.py` asserts the registry stays
  complete. The gallery renders every template, so a broken `{% block %}` shows up there.
- **The `e2e` lane is separate** and a normal `pytest <paths>` run deselects it.
  `tests/e2e/email_gallery_spec.py` must be run explicitly:
  `.venv/bin/pytest tests/e2e/email_gallery_spec.py -m e2e --no-cov -o addopts="" -q`
- **No dashes in member facing copy.** Not that copy is changing, but do not introduce any.
- **The CHANGELOG renders into every hub page.** Not this PR's problem unless a fragment is written;
  the orchestrator owns the fragment.

## Acceptance

1. All four extend the base and render with exactly one masthead and exactly one footer.
2. `classes/spec/emails_review_spec.py` passes untouched. If a spec must change, say why in the
   report rather than changing it quietly.
3. The four still render the review pipeline strip.
4. The CTA in each is `_button.html`.
5. `tests/e2e/email_gallery_spec.py` passes in the e2e lane.
6. `tests/template_comment_lint_spec.py` passes.
7. No `.txt` file, no `classes/emails.py`, no `_review_pipeline.html` in the diff.
