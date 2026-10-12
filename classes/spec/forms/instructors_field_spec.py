"""BDD specs for the Instructors control on the two composer forms (#686, part 1).

The checked rows post as ``instructors`` in the order listed; the "Add an instructor" select
appends a row in the browser and posts nothing the server keeps. A POST without the rows leaves
the list alone (the author on a new teaching class), an empty list is refused, and only an active
member with teaching access can be added. A listed member who lost that access stays until unticked.
"""

from __future__ import annotations

import json

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import (
    AT_LEAST_ONE_INSTRUCTOR,
    ClassOfferingForm,
    TeachClassOfferingForm,
    no_teaching_access_message,
)
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _data(**overrides) -> dict:
    data = {
        "title": "Studio Demo",
        "category": str(CategoryFactory().pk),
        "description": "A walkthrough.",
        "price_cents": "20.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
    }
    data.update(overrides)
    return data


def _members(offering: ClassOffering) -> list[int]:
    return [member.pk for member in offering.instructor_list]


def _pks(*members: Member) -> list[str]:
    return [str(member.pk) for member in members]


def describe_the_teaching_composer():
    def it_starts_a_new_class_with_its_author_listed():
        author = InstructorFactory()
        form = TeachClassOfferingForm(data=_data(), teaching_member=author)
        assert form.is_valid(), form.errors
        offering = form.save()
        assert _members(offering) == [author.pk]
        assert offering.instructor_id == author.pk

    def it_adds_the_people_posted_after_the_author():
        author, partner = InstructorFactory(), InstructorFactory()
        form = TeachClassOfferingForm(data=_data(instructors=_pks(author, partner)), teaching_member=author)
        assert form.is_valid(), form.errors
        offering = form.save()
        assert _members(offering) == [author.pk, partner.pk]
        assert offering.instructor_id == author.pk

    def it_lets_the_author_hand_a_new_class_to_a_partner():
        author, partner = InstructorFactory(), InstructorFactory()
        form = TeachClassOfferingForm(data=_data(instructors=_pks(partner)), teaching_member=author)
        assert form.is_valid(), form.errors
        offering = form.save()
        assert _members(offering) == [partner.pk]
        assert offering.instructor_id == partner.pk
        assert offering.created_by_id == author.pk

    def it_leaves_the_list_alone_when_the_rows_were_not_posted():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        form = TeachClassOfferingForm(data=_data(), instance=offering, teaching_member=a)
        assert form.is_valid(), form.errors
        form.save()
        assert _members(offering) == [a.pk, b.pk]

    def it_keeps_the_set_and_the_order_posted():
        a, b, c = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        form = TeachClassOfferingForm(data=_data(instructors=_pks(a, c, b)), instance=offering, teaching_member=a)
        assert form.is_valid(), form.errors
        form.save()
        assert _members(offering) == [a.pk, c.pk, b.pk]

    def it_refuses_an_empty_list():
        a = InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        form = TeachClassOfferingForm(data=_data(instructors=[""]), instance=offering, teaching_member=a)
        assert not form.is_valid()
        assert form.errors["instructors"] == [AT_LEAST_ONE_INSTRUCTOR]
        assert _members(offering) == [a.pk]

    def it_refuses_a_member_without_teaching_access_by_name():
        author = InstructorFactory()
        plain = MemberFactory(full_legal_name="Pat Plain")
        form = TeachClassOfferingForm(data=_data(instructors=_pks(author, plain)), teaching_member=author)
        assert not form.is_valid()
        assert form.errors["instructors"] == ["Pat Plain doesn't have teaching access yet."]
        assert form.errors["instructors"] == [no_teaching_access_message(plain)]

    def it_refuses_a_member_who_is_not_active_even_with_teaching_access():
        author = InstructorFactory()
        invited = InstructorFactory(full_legal_name="Ida Invited", status=Member.Status.INVITED)
        form = TeachClassOfferingForm(data=_data(instructors=_pks(author, invited)), teaching_member=author)
        assert not form.is_valid()
        assert form.errors["instructors"] == ["Ida Invited doesn't have teaching access yet."]

    def it_keeps_a_listed_instructor_who_lost_teaching_access():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        b.instructor_oriented_at = None
        b.save(update_fields=["instructor_oriented_at"])
        form = TeachClassOfferingForm(data=_data(instructors=_pks(a, b)), instance=offering, teaching_member=a)
        assert form.is_valid(), form.errors
        form.save()
        assert _members(offering) == [a.pk, b.pk]

    def it_refuses_an_unknown_member():
        author = InstructorFactory()
        form = TeachClassOfferingForm(data=_data(instructors=["999999"]), teaching_member=author)
        assert not form.is_valid()
        assert "999999" in form.errors["instructors"][0]


def describe_the_admin_composer():
    def it_needs_at_least_one_instructor_on_a_new_class():
        form = ClassOfferingForm(data=_data())
        assert not form.is_valid()
        assert form.errors["instructors"] == [AT_LEAST_ONE_INSTRUCTOR]

    def it_lists_the_people_posted_in_that_order():
        a, b = InstructorFactory(), InstructorFactory()
        form = ClassOfferingForm(data=_data(instructors=_pks(b, a)))
        assert form.is_valid(), form.errors
        offering = form.save()
        assert _members(offering) == [b.pk, a.pk]
        assert offering.instructor_id == b.pk

    def it_leaves_an_existing_list_alone_when_the_rows_were_not_posted():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        form = ClassOfferingForm(data=_data(), instance=offering)
        assert form.is_valid(), form.errors
        form.save()
        assert _members(offering) == [a.pk, b.pk]

    def it_has_no_instructor_select_any_more():
        assert "instructor" not in ClassOfferingForm().fields
        assert "instructor" not in TeachClassOfferingForm().fields


def describe_when_the_viewer_may_not_change_the_list():
    """The guild's staff on a draft they do not teach: no fields, nothing posted counts, rows stay."""

    def it_carries_neither_field():
        a = InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        form = TeachClassOfferingForm(
            instance=offering, teaching_member=InstructorFactory(), can_edit_instructors=False
        )
        assert "instructors" not in form.fields
        assert "add_instructor" not in form.fields
        assert form.instructors_editable is False
        assert [row["pk"] for row in form.instructor_rows] == [a.pk]

    def it_ignores_a_posted_list_and_keeps_the_rows_and_the_payee():
        a, b, staffer = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        form = TeachClassOfferingForm(
            data=_data(instructors=_pks(staffer), add_instructor=str(staffer.pk), title="Renamed By Staff"),
            instance=offering,
            teaching_member=staffer,
            can_edit_instructors=False,
        )
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.title == "Renamed By Staff"
        assert _members(offering) == [a.pk, b.pk]
        assert offering.instructor_id == a.pk

    def it_saves_a_class_nobody_is_listed_on_without_the_at_least_one_rule():
        offering = ClassOfferingFactory(instructor=None)
        form = TeachClassOfferingForm(
            data=_data(title="Legacy Run"),
            instance=offering,
            teaching_member=InstructorFactory(),
            can_edit_instructors=False,
        )
        assert form.is_valid(), form.errors
        form.save()
        offering.refresh_from_db()
        assert offering.title == "Legacy Run"
        assert _members(offering) == []
        assert offering.instructor_id is None

    def it_applies_to_the_admin_form_too():
        a = InstructorFactory()
        offering = ClassOfferingFactory(instructor=a)
        form = ClassOfferingForm(
            data=_data(instructors=_pks(InstructorFactory())), instance=offering, can_edit_instructors=False
        )
        assert form.is_valid(), form.errors
        form.save()
        assert _members(offering) == [a.pk]


def describe_the_add_an_instructor_select():
    def it_offers_active_members_with_teaching_access():
        teacher = InstructorFactory()
        plain = MemberFactory()
        invited = InstructorFactory(status=Member.Status.INVITED)
        choices = ClassOfferingForm().fields["add_instructor"].queryset  # type: ignore[attr-defined]
        assert teacher in choices
        assert plain not in choices
        assert invited not in choices

    def it_leaves_out_whoever_is_already_listed():
        a, b, c = InstructorFactory(), InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        choices = TeachClassOfferingForm(instance=offering, teaching_member=a).fields["add_instructor"].queryset  # type: ignore[attr-defined]
        assert a not in choices and b not in choices
        assert c in choices

    def it_leaves_out_the_rows_posted_on_a_refused_save():
        author, partner = InstructorFactory(), InstructorFactory()
        form = TeachClassOfferingForm(data=_data(instructors=_pks(author, partner), title=""), teaching_member=author)
        assert not form.is_valid()
        choices = form.fields["add_instructor"].queryset  # type: ignore[attr-defined]
        assert partner not in choices and author not in choices

    def it_keeps_nothing_it_posts():
        author, partner = InstructorFactory(), InstructorFactory()
        form = TeachClassOfferingForm(data=_data(add_instructor=str(partner.pk)), teaching_member=author)
        assert form.is_valid(), form.errors
        offering = form.save()
        assert _members(offering) == [author.pk]
        assert form.cleaned_data["add_instructor"] is None

    def it_appends_a_row_in_the_browser():
        assert ClassOfferingForm().fields["add_instructor"].widget.attrs["@change"] == "add($event.target)"


def describe_the_rows_the_basics_step_lists():
    def it_lists_the_saved_rows_in_order():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        form = TeachClassOfferingForm(instance=offering, teaching_member=a)
        assert form.instructor_rows == [
            {"pk": a.pk, "name": a.display_name},
            {"pk": b.pk, "name": b.display_name},
        ]
        assert json.loads(form.instructor_rows_json) == form.instructor_rows

    def it_lists_the_author_on_a_new_class():
        author = InstructorFactory()
        assert TeachClassOfferingForm(teaching_member=author).instructor_rows == [
            {"pk": author.pk, "name": author.display_name}
        ]
        assert ClassOfferingForm().instructor_rows == []

    def it_lists_what_was_posted_after_a_refused_save():
        author = InstructorFactory()
        plain = MemberFactory(full_legal_name="Pat Plain")
        form = TeachClassOfferingForm(data=_data(instructors=_pks(author, plain), title=""), teaching_member=author)
        assert not form.is_valid()
        assert [row["pk"] for row in form.instructor_rows] == [author.pk, plain.pk]

    def it_lists_the_current_rows_when_the_rows_were_not_posted():
        a, b = InstructorFactory(), InstructorFactory()
        offering = ClassOfferingFactory(instructor=a, instructors=[a, b])
        form = TeachClassOfferingForm(data=_data(title=""), instance=offering, teaching_member=a)
        assert not form.is_valid()
        assert [row["pk"] for row in form.instructor_rows] == [a.pk, b.pk]
