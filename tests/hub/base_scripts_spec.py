"""BDD specs for the scripts hub/base.html loads on every hub page (issue #378).

The hub boosts the whole body, so where a script tag sits decides how many times it runs:
a body script runs again on every boosted navigation, a head script once per document.
htmx and Alpine inside <body> gave every in-app arrival a second htmx (a stale Back
handler) and a second Alpine that initialised the page before its component scripts had
registered, which is what killed the composer's card focus widget. These specs pin the
load order the fix depends on and fail if either library ever lands back in the body.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from classes.factories import InstructorFactory
from tests.membership.factories import MembershipPlanFactory, UserFactory

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_JS = REPO_ROOT / "static" / "js"
TEMPLATES_DIR = REPO_ROOT / "templates"
HEAD_ORDER = (
    "js/htmx.min.js",
    "js/htmx-ext-head-support.js",
    "js/hub_boot.js",
    "js/hero_placement.js",
    "js/session_calendar.js",
    "js/card_focus.js",
    "js/space_map.js",
    "js/pl_help.js",
    "js/alpine.min.js",
    "js/biometric-auth.js",
    "js/app-store-badges.js",
)


def _split(html: str) -> tuple[str, str]:
    """The rendered document's <head> and <body> halves."""
    head, sep, body = html.partition("<body")
    assert sep, "no <body> in the rendered page"
    return head, body


def _script_tags(fragment: str) -> list[str]:
    return re.findall(r"<script[^>]*\bsrc=[^>]*>", fragment)


def _registers_without_guard(template_text: str) -> bool:
    """True when a template registers an Alpine component but never checks ``window.Alpine``.

    An inline registration behind a plain ``alpine:init`` listener runs on a hard load and
    never on a boosted arrival, where Alpine (loaded once, from the head) started long ago
    and the event does not fire again; the component is silently missing on every in-app
    navigation to that page. The guard, ``if (window.Alpine) register(); else listen``,
    is what keeps both paths alive.
    """
    return "Alpine.data(" in template_text and "window.Alpine" not in template_text


@pytest.fixture
def hub_user(db):
    MembershipPlanFactory()
    return UserFactory(username="base-scripts@example.com")


@pytest.fixture
def home_html(hub_user, client) -> str:
    client.force_login(hub_user)
    return client.get(reverse("hub_home")).content.decode()


def describe_hub_base_scripts():
    def it_loads_htmx_and_alpine_once_from_the_head_and_never_from_the_body(home_html):
        head, body = _split(home_html)
        assert '<script defer src="/static/js/htmx.min.js"></script>' in head
        assert '<script defer src="/static/js/alpine.min.js"></script>' in head
        assert home_html.count("js/htmx.min.js") == 1
        assert home_html.count("js/alpine.min.js") == 1
        assert "htmx" not in "".join(_script_tags(body))
        assert "alpine" not in "".join(_script_tags(body))

    def it_runs_the_boot_wiring_and_every_component_registration_before_alpine_starts(home_html):
        head, _ = _split(home_html)
        positions = [head.index(name) for name in HEAD_ORDER]
        assert positions == sorted(positions), HEAD_ORDER
        # All deferred: they wait for the parse, and the browser keeps this order.
        for name in HEAD_ORDER:
            assert f'<script defer src="/static/{name}"></script>' in head, name

    def it_never_repeats_a_head_script_in_the_body(home_html):
        # A body copy would run again on every boosted swap, which is the whole defect.
        _, body = _split(home_html)
        assert [name for name in HEAD_ORDER if name in "".join(_script_tags(body))] == []

    def it_carries_the_csrf_token_for_htmx_in_the_head(home_html):
        head, _ = _split(home_html)
        assert re.search(r'<meta name="csrf-token" content="[A-Za-z0-9]{64}">', head)

    def it_loads_every_static_script_that_registers_an_alpine_component_before_alpine(home_html):
        # Alpine initialises a boosted page a microtask after htmx inserts it, before any
        # script the page itself loads could arrive, so a component registered from a page's
        # body or extra_head is dead on every in-app arrival. Every registrant lives here.
        head, _ = _split(home_html)
        registrants = sorted(
            f"js/{path.name}"
            for path in STATIC_JS.glob("*.js")
            if "Alpine.data(" in path.read_text(encoding="utf-8") and not path.name.endswith(".min.js")
        )
        assert registrants, "expected at least the known component scripts under static/js"
        assert [name for name in registrants if name not in HEAD_ORDER] == []
        for name in registrants:
            assert head.index(name) < head.index("js/alpine.min.js"), name

    def it_guards_every_inline_template_registration_for_a_boosted_arrival():
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in sorted(TEMPLATES_DIR.rglob("*.html"))
            if _registers_without_guard(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], (
            "Alpine.data(...) inside a template must register on whichever side of alpine:init "
            "it lands, like hub/wiki_edit.html: if (window.Alpine) register(); else "
            "document.addEventListener('alpine:init', register). Unguarded:\n  " + "\n  ".join(offenders)
        )

    def it_actually_detects_an_unguarded_inline_registration():
        # Self-test so a refactor can't quietly neuter the lint.
        unguarded = "<script>document.addEventListener('alpine:init', () => { Alpine.data('x', () => ({})) })</script>"
        guarded = (
            "<script>function define() { Alpine.data('x', () => ({})) }\n"
            "if (window.Alpine) { define() } else { document.addEventListener('alpine:init', define) }</script>"
        )
        assert _registers_without_guard(unguarded)
        assert not _registers_without_guard(guarded)
        assert not _registers_without_guard('<div x-data="{ open: false }"></div>')

    def it_keeps_the_composer_page_free_of_a_second_card_focus_script(db, client):
        MembershipPlanFactory()
        instructor = InstructorFactory(user=UserFactory(username="composer-head@example.com"))
        client.force_login(instructor.user)
        html = client.get(reverse("classes:teach_class_create")).content.decode()
        head, body = _split(html)
        assert html.count("js/card_focus.js") == 1
        assert "js/card_focus.js" in head and "card_focus.js" not in body

    def it_loads_alpine_once_on_the_community_calendar(hub_user, client):
        # The calendar page used to add a second copy of Alpine to the body.
        client.force_login(hub_user)
        html = client.get(reverse("hub_community_calendar")).content.decode()
        assert html.count("js/alpine.min.js") == 1
