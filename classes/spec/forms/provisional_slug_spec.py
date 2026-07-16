"""BDD specs for the provisional-slug helper shared by both class create forms."""

from __future__ import annotations

from classes.factories import ClassOfferingFactory
from classes.forms import _assign_provisional_slug


def describe_assign_provisional_slug():
    def it_leaves_an_existing_slug_untouched(db):
        offering = ClassOfferingFactory(title="Keep Me", slug="keep-me")
        _assign_provisional_slug(offering)
        assert offering.slug == "keep-me"

    def it_slugifies_the_title_when_the_slug_is_blank(db):
        offering = ClassOfferingFactory(title="Fresh Class", slug="fresh-placeholder")
        offering.slug = ""
        _assign_provisional_slug(offering)
        assert offering.slug == "fresh-class"

    def it_suffixes_a_provisional_collision(db):
        ClassOfferingFactory(title="Taken", slug="taken")
        offering = ClassOfferingFactory(title="Taken", slug="taken-placeholder")
        offering.slug = ""
        _assign_provisional_slug(offering)
        assert offering.slug == "taken-2"

    def it_uses_class_when_the_title_has_no_slug_chars(db):
        offering = ClassOfferingFactory(title="!!! ???", slug="punct-placeholder")
        offering.slug = ""
        _assign_provisional_slug(offering)
        assert offering.slug == "class"
