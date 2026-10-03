"""The line under Flexible Scheduling: the instructor's own text, or the standard line built live."""

from __future__ import annotations

from datetime import date

import pytest

from classes.factories import ClassOfferingFactory, InstructorFactory
from classes.models import ClassOffering

STANDARD = "After you register, you'll book your session directly with {name} and pick a day {window}that works for both of you."


@pytest.mark.django_db
def describe_the_flexible_booking_line():
    def _flexible(**traits) -> ClassOffering:
        traits.setdefault("instructor", InstructorFactory(full_legal_name="Billy Anvil"))
        return ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits)

    def it_names_the_instructor_in_the_standard_line():
        offering = _flexible()
        assert offering.default_flexible_booking_line == STANDARD.format(name="Billy Anvil", window="")
        assert offering.flexible_booking_line == offering.default_flexible_booking_line

    def it_points_inside_the_window_when_the_class_has_one():
        offering = _flexible(flexible_ends_on=date(2026, 12, 1))
        assert offering.default_flexible_booking_line == STANDARD.format(
            name="Billy Anvil", window="inside this window "
        )

    def it_says_your_instructor_before_one_is_chosen():
        offering = ClassOffering(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert offering.default_flexible_booking_line == STANDARD.format(name="your instructor", window="")

    def it_shows_the_instructors_own_text_when_they_wrote_one():
        offering = _flexible(flexible_booking_text="Email me and we will pick a Saturday.")
        assert offering.flexible_booking_line == "Email me and we will pick a Saturday."

    def it_falls_back_to_the_standard_line_when_the_text_is_only_spaces():
        offering = _flexible(flexible_booking_text="   \n ")
        assert offering.flexible_booking_line == offering.default_flexible_booking_line
