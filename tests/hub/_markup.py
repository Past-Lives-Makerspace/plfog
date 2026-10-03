"""Markup anchored assertions shared by the guild edit page specs.

The changelog renders on every hub page, so a negative assertion on UI copy ("Save FAQ" not in
the page) fails the day a fragment uses the phrase (STANDARDS.md, section 8). These anchor on
the markup instead.
"""

from __future__ import annotations

import re

_AUTOSAVE_FORM = re.compile(r"<form\b[^>]*\bdata-autosave\b[^>]*>.*?</form>", re.S)


def assert_autosave_forms_have_no_submit(content: str) -> int:
    """Every self saving form on the page carries no submit button; returns how many forms there are."""
    blocks = _AUTOSAVE_FORM.findall(content)
    assert blocks, "no data-autosave form on the page"
    assert [block[:120] for block in blocks if 'type="submit"' in block] == []
    return len(blocks)
