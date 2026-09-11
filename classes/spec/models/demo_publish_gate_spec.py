"""BDD specs for the ``demo-`` / ``[DEMO]`` marker on a class offering.

A demo class exists so a walkthrough can be run end to end on production, final
approval included, without telling 200 members about a workshop that does not exist.
Publishing no longer announces anything site-wide at all, so the marker is no longer
consulted at publish time; what keeps demo content off the public catalog, the
calendar and the #classes post is ``ClassOfferingQuerySet.public()``, which excludes
a ``demo-`` slug unless the ``display_demo_classes`` site setting is on.
"""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory

pytestmark = pytest.mark.django_db


def describe_is_demo():
    def it_is_true_for_a_demo_slug():
        assert ClassOfferingFactory(slug="demo-map-compass", title="Compass Basics").is_demo is True

    def it_is_true_for_a_bracketed_demo_title():
        assert ClassOfferingFactory(slug="compass-basics", title="[DEMO] Compass Basics").is_demo is True

    def it_ignores_the_case_and_the_leading_whitespace_of_the_title():
        assert ClassOfferingFactory(slug="c-b", title="  [demo] Compass Basics").is_demo is True

    def it_is_false_for_an_ordinary_class():
        assert ClassOfferingFactory(slug="compass-basics", title="Compass Basics").is_demo is False

    def it_does_not_match_a_class_that_merely_mentions_a_demo():
        """The marker is a prefix, not a substring — "Live Demo Day" is a real class."""
        assert ClassOfferingFactory(slug="live-demo-day", title="Live Demo Day").is_demo is False
