"""BDD specs for RegistrationMoveForm."""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from classes.forms import RegistrationMoveForm

pytestmark = pytest.mark.django_db


def describe_RegistrationMoveForm():
    def it_excludes_the_current_class_from_choices():
        current = ClassOfferingFactory(slug="cur")
        other = ClassOfferingFactory(slug="oth")
        form = RegistrationMoveForm(current=current)
        choices = list(form.fields["target"].queryset)
        assert other in choices
        assert current not in choices

    def it_is_valid_with_a_different_class():
        current = ClassOfferingFactory(slug="cur2")
        other = ClassOfferingFactory(slug="oth2")
        form = RegistrationMoveForm({"target": other.pk}, current=current)
        assert form.is_valid()

    def it_is_invalid_when_target_is_the_current_class():
        current = ClassOfferingFactory(slug="cur3")
        form = RegistrationMoveForm({"target": current.pk}, current=current)
        assert not form.is_valid()

    def it_lists_all_classes_when_no_current_is_given():
        offering = ClassOfferingFactory(slug="all-a")
        form = RegistrationMoveForm()
        assert offering in list(form.fields["target"].queryset)
