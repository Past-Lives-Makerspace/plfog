"""BDD specs for Members only discount codes at checkout (#1072).

A Members only code works only for a buyer who is signed in with a member whose status is
Active. Everyone else is refused with one sentence, the quoted price never shows the
discount, and auto apply never picks the code for them. Codes without the switch behave as
before for everyone.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string

from classes.factories import CategoryFactory, ClassOfferingFactory, DiscountCodeFactory, InstructorFactory
from classes.forms import MEMBERS_ONLY_REFUSAL, DiscountCodeForm, RegistrationForm
from classes.models import ClassOffering, ClassSettings, DiscountCode
from membership.models import Member

pytestmark = pytest.mark.django_db

#: Every status a signed in buyer can have, and whether it counts as a member for the code.
STATUSES = [
    (Member.Status.ACTIVE, True),
    (Member.Status.FORMER, False),
    (Member.Status.GUEST, False),
    (Member.Status.INVITED, False),
    (Member.Status.SUSPENDED, False),
]
LOCKED_OUT = [status for status, counts in STATUSES if not counts]


@pytest.fixture
def offering(db):
    return ClassOfferingFactory(
        title="Lathe Night",
        slug="lathe-night",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        capacity=4,
    )


@pytest.fixture
def member_code(db):
    return DiscountCodeFactory(code="MEMBER10", discount_pct=10, members_only=True)


def _user_with_status(status: str, name: str = "buyer"):
    """A signed in account whose member has ``status``: the account signal makes the member, the spec sets its status."""
    user = get_user_model().objects.create(username=f"{name}@example.com", email=f"{name}@example.com")
    Member.objects.filter(pk=Member.objects.get(user=user).pk).update(status=status)
    user.refresh_from_db()
    return user


def _post(code: str) -> dict[str, str]:
    return {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": code,
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }


def _bound(offering, code: str, user=None, member=None) -> RegistrationForm:
    return RegistrationForm(
        data=_post(code), offering=offering, settings_obj=ClassSettings.load(), user=user, member=member
    )


def _quote(offering, code: str, user=None) -> int:
    """The quoted price for an unbound page whose code box holds ``code``, as the price refresh renders it."""
    return RegistrationForm(
        initial={"discount_code": code}, offering=offering, settings_obj=ClassSettings.load(), user=user
    ).quoted_price_cents()


def describe_DiscountCode_usable_by():
    def it_lets_anyone_use_a_code_without_the_switch():
        code = DiscountCodeFactory(members_only=False)
        assert code.usable_by(None)
        assert code.usable_by(Member(status=Member.Status.GUEST))

    def it_lets_only_an_active_member_use_a_members_only_code(member_code):
        assert not member_code.usable_by(None)
        for status, counts in STATUSES:
            assert member_code.usable_by(Member(status=status)) is counts, status

    def it_filters_a_queryset_the_same_way(member_code):
        ordinary = DiscountCodeFactory(code="ANYONE5")
        assert set(DiscountCode.objects.usable_by(None)) == {ordinary}
        assert set(DiscountCode.objects.usable_by(Member(status=Member.Status.FORMER))) == {ordinary}
        assert set(DiscountCode.objects.usable_by(Member(status=Member.Status.ACTIVE))) == {ordinary, member_code}

    def it_starts_off_for_a_new_code():
        assert DiscountCode.objects.create(code="FRESH", discount_pct=5).members_only is False


def describe_a_members_only_code_at_checkout():
    def it_refuses_a_signed_out_buyer(offering, member_code):
        form = _bound(offering, "MEMBER10", user=AnonymousUser())
        assert not form.is_valid()
        assert form.errors["discount_code"] == [MEMBERS_ONLY_REFUSAL]
        assert MEMBERS_ONLY_REFUSAL == "This code is for members. Sign in to use it."

    def it_refuses_a_buyer_with_no_user_at_all(offering, member_code):
        form = _bound(offering, "MEMBER10")
        assert not form.is_valid()
        assert form.errors["discount_code"] == [MEMBERS_ONLY_REFUSAL]

    def it_refuses_a_signed_in_account_with_no_member(offering, member_code):
        user = get_user_model().objects.create(username="nomember@example.com", email="nomember@example.com")
        Member.objects.filter(user=user).delete()
        user = get_user_model().objects.get(pk=user.pk)
        form = _bound(offering, "MEMBER10", user=user)
        assert form.member is None
        assert form.errors["discount_code"] == [MEMBERS_ONLY_REFUSAL]

    @pytest.mark.parametrize("status", LOCKED_OUT)
    def it_refuses_a_signed_in_buyer_who_is_not_active(offering, member_code, status):
        form = _bound(offering, "MEMBER10", user=_user_with_status(status))
        assert not form.is_valid()
        assert form.errors["discount_code"] == [MEMBERS_ONLY_REFUSAL]

    def it_gives_a_signed_in_active_member_ten_percent_off(offering, member_code):
        form = _bound(offering, "MEMBER10", user=_user_with_status(Member.Status.ACTIVE))
        assert form.is_valid(), form.errors
        assert form.validated_discount == member_code
        assert form.compute_final_price_cents() == 9000

    def it_takes_an_explicit_buyer_over_the_signed_in_user(offering, member_code):
        active = Member.objects.get(user=_user_with_status(Member.Status.ACTIVE, "active"))
        form = _bound(offering, "MEMBER10", user=_user_with_status(Member.Status.FORMER, "former"), member=active)
        assert form.is_valid(), form.errors

    def it_still_says_a_stale_code_is_not_valid_before_asking_who_is_buying(offering):
        DiscountCodeFactory(code="OLDMEMBER", members_only=True, is_active=False)
        form = _bound(offering, "OLDMEMBER", user=AnonymousUser())
        assert form.errors["discount_code"] == ["That discount code isn't valid right now."]


def describe_the_quoted_price():
    def it_shows_no_discount_to_a_signed_out_buyer(offering, member_code):
        assert _quote(offering, "MEMBER10", user=AnonymousUser()) == 10000

    @pytest.mark.parametrize("status", LOCKED_OUT)
    def it_shows_no_discount_to_a_buyer_who_is_not_active(offering, member_code, status):
        assert _quote(offering, "MEMBER10", user=_user_with_status(status)) == 10000

    def it_shows_the_discount_to_an_active_member(offering, member_code):
        assert _quote(offering, "MEMBER10", user=_user_with_status(Member.Status.ACTIVE)) == 9000


def describe_auto_apply():
    @pytest.fixture
    def auto_code(offering):
        return DiscountCodeFactory(
            code="LATHEMEMBERS", discount_pct=25, class_offering=offering, auto_apply=True, members_only=True
        )

    def _page(offering, user) -> RegistrationForm:
        return RegistrationForm(offering=offering, settings_obj=ClassSettings.load(), user=user)

    def it_never_applies_a_members_only_code_to_a_signed_out_buyer(offering, auto_code):
        form = _page(offering, AnonymousUser())
        assert form.auto_applied_discount is None
        assert form.fields["discount_code"].initial is None
        assert form.quoted_price_cents() == 10000

    @pytest.mark.parametrize("status", LOCKED_OUT)
    def it_never_applies_a_members_only_code_to_a_buyer_who_is_not_active(offering, auto_code, status):
        assert _page(offering, _user_with_status(status)).auto_applied_discount is None

    def it_applies_it_to_an_active_member(offering, auto_code):
        form = _page(offering, _user_with_status(Member.Status.ACTIVE))
        assert form.auto_applied_discount == auto_code
        assert form.quoted_price_cents() == 7500

    def it_falls_back_to_an_ordinary_auto_code_for_everyone_else(offering, auto_code):
        ordinary = DiscountCodeFactory(code="LATHE5", discount_pct=5, class_offering=offering, auto_apply=True)
        assert _page(offering, AnonymousUser()).auto_applied_discount == ordinary
        assert _page(offering, _user_with_status(Member.Status.ACTIVE)).auto_applied_discount == auto_code

    def it_skips_it_in_the_manager_too(offering, auto_code):
        assert DiscountCode.objects.best_auto_apply_for(offering, 10000, member=None) is None
        assert DiscountCode.objects.best_auto_apply_for(offering, 10000, member=Member(status="active")) == auto_code


def describe_an_ordinary_code():
    @pytest.mark.parametrize("status", [None, *[status for status, _counts in STATUSES]])
    def it_works_for_every_buyer_as_before(offering, status):
        DiscountCodeFactory(code="EVERYONE20", discount_pct=20)
        user = AnonymousUser() if status is None else _user_with_status(status)
        form = _bound(offering, "EVERYONE20", user=user)
        assert form.is_valid(), form.errors
        assert form.compute_final_price_cents() == 8000
        assert _quote(offering, "EVERYONE20", user=user) == 8000


def describe_the_admin_switch():
    def it_is_offered_with_its_label_and_hint_when_asked():
        field = DiscountCodeForm(offers_members_only=True).fields["members_only"]
        assert field.label == "Members only"
        assert field.help_text == "Only signed in members with an active membership can use it."
        assert DiscountCodeForm(offers_members_only=True)["members_only"].value() is False

    def it_is_left_off_the_instructor_and_request_forms():
        assert "members_only" not in DiscountCodeForm().fields

    def it_switches_on_and_off_and_an_instructor_edit_keeps_it():
        data = {"code": "GUILDMEMBERS", "discount_pct": "10", "is_active": "on", "members_only": "on"}
        code = DiscountCodeForm(data=data, offers_members_only=True)
        assert code.is_valid(), code.errors
        saved = code.save()
        assert saved.members_only is True

        instructor_edit = DiscountCodeForm(data={**data, "members_only": ""}, instance=saved)
        assert instructor_edit.is_valid(), instructor_edit.errors
        assert instructor_edit.save().members_only is True

        admin_edit = DiscountCodeForm(data={**data, "members_only": ""}, instance=saved, offers_members_only=True)
        assert admin_edit.is_valid(), admin_edit.errors
        assert admin_edit.save().members_only is False


def describe_the_read_only_code_table():
    def it_tags_a_members_only_code(member_code):
        ordinary = DiscountCodeFactory(code="ANYONE5")
        html = render_to_string(
            "classes/_components/discount_codes_read_only_table.html",
            {"codes": [member_code, ordinary], "empty_text": "None"},
        )
        member_row, ordinary_row = html.split("<tr>")[2:4]
        assert "data-members-only>Members only</span>" in member_row
        assert "data-members-only" not in ordinary_row
