"""BDD specs for the member picker helpers in ``membership.forms`` (#805).

Three admin forms pick a member by label: Add Inquiry, Record Orientation and Give Credit.
36 production members carry a full legal name with a stray space (``'Maria Amelia Randall '``),
and the pickers refused them: the label kept the space, the lookup keyed on it, and every
caller stripped what was posted. Labels and lookups now both collapse and trim spaces.
"""

from __future__ import annotations

from typing import Any

import pytest

from classes.forms import InstructorInquiryAddForm
from hub.forms import EquipmentCreditForm, OrientationRecordForm
from membership.forms import member_picker_key, member_picker_labels, member_picker_lookup
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(name: str) -> Member:
    return MemberFactory(full_legal_name=name, preferred_name="", status=Member.Status.ACTIVE)


def _inquiry(member: str) -> InstructorInquiryAddForm:
    return InstructorInquiryAddForm({"member": member})


def _record(member: str) -> OrientationRecordForm:
    return OrientationRecordForm(None, {"member": member})


def _credit(member: str) -> EquipmentCreditForm:
    return EquipmentCreditForm({"member": member, "hours": "1"})


FORMS: dict[str, Any] = {"add inquiry": _inquiry, "record orientation": _record, "give credit": _credit}


def _picked(form: Any) -> Member | None:
    form.is_valid()
    assert "member" not in form.errors, form.errors["member"]
    return form.cleaned_data["member"]


def describe_member_picker_key():
    def it_collapses_and_trims_spaces_and_folds_case():
        assert member_picker_key("  Maria \t Amelia  RANDALL ") == "maria amelia randall"

    def it_leaves_a_tidy_name_as_its_case_fold():
        assert member_picker_key("Sam Lee (#4)") == "sam lee (#4)"


def describe_member_picker_labels():
    def it_labels_a_member_by_their_name_with_spaces_tidied():
        maria = _member(" Maria  Amelia Randall ")
        assert member_picker_labels([maria]) == {"Maria Amelia Randall": maria}

    def it_numbers_two_members_whose_tidied_names_match():
        first = _member("Sam Lee ")
        second = _member("sam  lee")
        assert list(member_picker_labels([first, second])) == [f"Sam Lee (#{first.pk})", f"sam lee (#{second.pk})"]

    def it_keeps_the_members_order():
        zed = _member("Zed Last")
        abe = _member("Abe First")
        assert list(member_picker_labels([zed, abe]).values()) == [zed, abe]


def describe_member_picker_lookup():
    def it_keys_each_label_by_its_picker_key():
        maria = _member("Maria Amelia Randall ")
        assert member_picker_lookup(member_picker_labels([maria])) == {"maria amelia randall": maria}


@pytest.mark.parametrize("form_name", list(FORMS))
def describe_the_three_pickers():
    @pytest.fixture(autouse=True)
    def _plan() -> None:
        MembershipPlanFactory()

    @pytest.mark.parametrize("stored", ["Maria Amelia Randall ", " Maria Amelia Randall", "Maria  Amelia Randall"])
    def it_takes_a_member_whose_stored_name_has_a_stray_space(form_name, stored):
        maria = _member(stored)
        maria.refresh_from_db()
        assert maria.full_legal_name == stored  # stored names are left as they are
        assert "Maria Amelia Randall" in FORMS[form_name]("Maria Amelia Randall").member_options
        assert _picked(FORMS[form_name]("Maria Amelia Randall")) == maria

    def it_takes_a_name_typed_with_doubled_spaces(form_name):
        maria = _member("Maria Amelia Randall")
        assert _picked(FORMS[form_name]("  maria   amelia randall ")) == maria

    def it_still_tells_two_members_with_one_tidied_name_apart(form_name):
        first = _member("Sam Lee ")
        second = _member("Sam Lee")
        options = FORMS[form_name]("x").member_options
        assert f"Sam Lee (#{first.pk})" in options
        assert f"Sam Lee (#{second.pk})" in options
        assert _picked(FORMS[form_name](f"Sam Lee (#{first.pk})")) == first
        assert _picked(FORMS[form_name](f"sam  lee (#{second.pk})")) == second

    def it_refuses_a_name_not_on_the_list(form_name):
        _member("Maria Amelia Randall")
        form = FORMS[form_name]("Maria Randall")
        assert not form.is_valid()
        assert form.errors["member"] == ["Pick a member from the list."]
