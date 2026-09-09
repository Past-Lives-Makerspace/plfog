"""BDD specs proving the instructor tour's create-page stops still resolve on the five step composer.

The tour (core/tours.py) points at data-help-key elements that now sit on hidden step
panes; static/js/pl_tour.js reveals the pane through the composer's composer-goto-step
contract before highlighting. That only works if every targeted key is actually in the
rendered page, which is what this spec pins down.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

from classes.factories import InstructorFactory, UserFactory
from core.tours import TOURS

pytestmark = pytest.mark.django_db

CREATE_PAGE = "classes:teach_class_create"
HELP_KEY = re.compile(r'\[data-help-key="([^"]+)"\]')


def _create_page_targets() -> list[str]:
    """The target selectors of every instructor tour stop that sits on the create page.

    A stop belongs to the page the most recent ``navigate`` loaded, so the stops after the
    create-page hop and before the next hop are the create-page segment.
    """
    page: str | None = TOURS["instructor"].entry_url_name
    targets: list[str] = []
    for step in TOURS["instructor"].steps:
        if step.navigate:
            page = step.navigate
        if page == CREATE_PAGE and step.target:
            targets.append(step.target)
    return targets


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="tour-targets-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-tt")


def describe_instructor_tour_on_the_composer():
    def it_has_stops_on_the_create_page():
        # The walk itself must be alive: an empty segment would let the check below pass vacuously.
        targets = _create_page_targets()
        assert len(targets) >= 4
        assert '[data-help-key="teach.class-basics"]' in targets
        assert '[data-help-key="teach.class-pricing"]' in targets

    def it_resolves_every_create_page_target_in_the_rendered_composer(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        html = client.get(reverse(CREATE_PAGE)).content.decode()
        for target in _create_page_targets():
            match = HELP_KEY.fullmatch(target)
            assert match, f"tour target is not a help-key selector: {target}"
            assert f'data-help-key="{match.group(1)}"' in html, target

    def it_keeps_every_hidden_pane_target_inside_a_stamped_pane(instructor_fixture, client):
        # Each targeted element must sit under a data-composer-step ancestor or at the top of the
        # form, so pl_tour.js can find the pane to reveal. Checked by document order.
        client.force_login(instructor_fixture.user)
        html = client.get(reverse(CREATE_PAGE)).content.decode()
        pane_positions = [(m.start(), int(m.group(1))) for m in re.finditer(r'data-composer-step="(\d)"', html)]
        for target in _create_page_targets():
            key = HELP_KEY.fullmatch(target).group(1)  # type: ignore[union-attr]
            at = html.index(f'data-help-key="{key}"')
            panes_before = [n for pos, n in pane_positions if pos < at]
            assert panes_before, f"{key} renders before the first step pane"
