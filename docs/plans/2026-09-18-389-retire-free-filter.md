# 389: Retire the public Free classes filter and the Register Free label

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/389
Branch: fog/retire-free-filter
Brief for this round: surgical, pragmatic, YAGNI. Smallest change that meets the acceptance.

## Why
PR #387 made $1.00 the floor for every class. Once the two legacy $0 demo rows are re-seeded
nothing on production is $0, so the catalog's "Free classes" filter always returns an empty
catalog and the detail page's "Register — Free" label has no class left to render for. Dead UI,
plus an em dash in member copy.

## Changes (all of them)
1. `templates/classes/public/list.html` ~118-129: delete the "Free classes" checkbox block.
   Keep "Member discount available" and "Has upcoming session".
2. `classes/views.py`:
   - `_apply_browse_filters` ~203: delete the `if request.GET.get("free") == "1"` branch.
   - `public_list` ~240 / ~309 / ~333: delete `free_only` (the variable, its
     `active_filter_count` term, the context key).
3. `templates/classes/public/detail.html` ~388: the CTA reads `Register now` unconditionally.
4. `membership/help_content.py` ~454: "... for price range, instructor, and member discounts."
5. `classes/spec/views/public_spec.py::it_filters_free_classes` becomes
   `it_ignores_the_retired_free_filter`: one legacy $0 row and one paid row, GET `?free=1`,
   assert BOTH titles render and "Free classes" is not in the response. Keeps the evidence.
6. A detail-page spec: a $0 legacy row renders "Register now" and never "Register — Free".

## Do not touch
- `templates/classes/public/register.html` ~198 and `_confirm_free_registration`: a $0 total
  via a 100% discount is a different thing and stays.
- `register_spec.py` `free_offering` fixtures (registration side, unrelated).
- Help screenshots.

## Acceptance
- `?free=1` is ignored; every browsable class still lists.
- No "Free classes" control; `active_filter_count` no longer counts it.
- A $0 legacy class's CTA says "Register now".
- The Register and Pay guide no longer mentions free classes.
- `grep -rn --exclude-dir=spec "free_only\|Register — Free" classes/ templates/ membership/help_content.py`
  is empty (the PR's own negative assertions are the only permitted hits).

## Test plan
`.venv/bin/pytest classes/spec/views/public_spec.py tests/template_comment_lint_spec.py -q`
plus any spec that snapshots help_content (grep "free classes" under tests/ and membership/spec first).
