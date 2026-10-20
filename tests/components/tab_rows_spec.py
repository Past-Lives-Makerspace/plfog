"""Every tab row in a template goes through the section picker (FRONTEND.md, Tab row and section picker).

A bare ``.pl-tabs`` or ``.pl-phase-tabs`` row scrolls off the edge of a phone with nothing to say
more tabs are there (#852), so a row has to sit inside ``{% tab_picker %}``. The per-class row in
``classes/_components/class_sections.html`` is the one exception: it is nested under the Classes
portal row and has its own phone view.

A text scan, so it has limits: ``{% comment %}`` blocks are dropped before counting, a class built
by template logic with double quotes inside it may go unseen, a partial holding a row is flagged
even when its caller wraps the ``{% include %}`` (wrap the row in the partial instead), and rows
built in Python or JavaScript are not scanned.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

#: Nested under another row, with its own phone view (``{% class_sections %}``).
NESTED = {"classes/_components/class_sections.html"}

_ROW = re.compile(r"""class=(["'])(?:(?!\1).)*\b(?:pl-tabs|pl-phase-tabs)\b(?![-\w])""", re.S)
_OPEN = re.compile(r"\{%\s*tab_picker\b")
_CLOSE = re.compile(r"\{%\s*endtab_picker\s*%\}")
_COMMENT = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}", re.S)


def bare_rows(source: str) -> list[int]:
    """The line of every row in ``source`` that no ``{% tab_picker %}`` block encloses."""
    # A comment keeps its newlines so line numbers still point at the file.
    source = _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), source)
    found = []
    for match in _ROW.finditer(source):
        before = source[: match.start()]
        opened = len(_OPEN.findall(before)) - len(_CLOSE.findall(before))
        if opened < 1:
            found.append(before.count("\n") + 1)
    return found


def describe_tab_rows():
    def it_finds_a_bare_row_and_passes_a_wrapped_one():
        assert bare_rows('<nav class="pl-tabs">') == [1]
        assert bare_rows('<div class="x pl-phase-tabs">') == [1]
        assert bare_rows('{% tab_picker "G" %}\n<nav class="pl-tabs">\n{% endtab_picker %}') == []
        assert bare_rows('{% tab_picker "G" %}{% endtab_picker %}\n<nav class="pl-tabs">') == [2]
        assert bare_rows('<div class="pl-tabs-wrap pl-phase-tab">') == []
        assert bare_rows('{% comment %}{% tab_picker "G" %}{% endcomment %}\n<nav class="pl-tabs">') == [2]

    def it_finds_no_bare_row_in_any_template():
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            name = path.relative_to(TEMPLATES).as_posix()
            if name in NESTED:
                continue
            offenders += [f"{name}:{line}" for line in bare_rows(path.read_text())]
        assert offenders == [], "wrap these rows in {% tab_picker %} (FRONTEND.md): " + ", ".join(offenders)
