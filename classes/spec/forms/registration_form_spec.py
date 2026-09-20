"""BDD specs for the public RegistrationForm."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    RegistrationFactory,
)
from classes.forms import RegistrationForm
from classes.models import ClassOffering, ClassSettings, Registration, Waiver

pytestmark = pytest.mark.django_db


@pytest.fixture
def offering(db):
    return ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        member_discount_pct=10,
        capacity=4,
    )


@pytest.fixture
def settings_obj(db):
    return ClassSettings.load()


def _build_form(user=None):
    """Build an unbound RegistrationForm for checkbox-visibility checks."""
    return RegistrationForm(
        offering=ClassOfferingFactory(),
        settings_obj=ClassSettings.load(),
        user=user,
    )


def _post_data(**overrides):
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "",
        # The member discount toggle, checked as the page renders it. A non-member's form has
        # no such box and the key is ignored; a member who declines sends "" (see the specs).
        "apply_member_discount": "on",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    data.update(overrides)
    return data


def describe_RegistrationForm():
    def it_force_uppercases_the_discount_code_input(offering, settings_obj):
        attrs = _build_form().fields["discount_code"].widget.attrs
        assert "text-transform:uppercase" in attrs["style"]
        assert attrs["oninput"] == "this.value = this.value.toUpperCase()"

    def describe_validation():
        def it_is_valid_with_minimum_required_fields(offering, settings_obj):
            form = RegistrationForm(data=_post_data(), offering=offering, settings_obj=settings_obj)
            assert form.is_valid(), form.errors

        def it_requires_liability_acceptance(offering, settings_obj):
            data = _post_data()
            data.pop("accepts_liability")
            form = RegistrationForm(data=data, offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()
            assert "accepts_liability" in form.errors

        def it_rejects_when_class_is_sold_out(offering, settings_obj):
            for _ in range(offering.capacity):
                RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
            form = RegistrationForm(data=_post_data(), offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()
            assert "sold out" in str(form.errors).lower()

        def it_rejects_unknown_discount_code(offering, settings_obj):
            form = RegistrationForm(data=_post_data(discount_code="NOPE"), offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()
            assert "discount_code" in form.errors

        def it_rejects_expired_discount_code(offering, settings_obj):
            DiscountCodeFactory(code="OLD", discount_pct=20, valid_until=date.today() - timedelta(days=1))
            form = RegistrationForm(data=_post_data(discount_code="OLD"), offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()

        def it_rejects_when_final_price_is_below_stripe_minimum(offering, settings_obj):
            # 99% off a $100 class = $1.00, still fine. 99% off a $40 class = $0.40, below Stripe's $0.50 floor.
            offering.price_cents = 4000
            offering.member_discount_pct = 0
            offering.save()
            DiscountCodeFactory(code="DEEP", discount_pct=99)
            form = RegistrationForm(data=_post_data(discount_code="DEEP"), offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()
            assert "$0.50" in str(form.errors)

        def it_requires_model_release_when_class_demands_it(offering, settings_obj):
            offering.requires_model_release = True
            offering.save()
            data = _post_data()
            # Don't include accepts_model_release / model_release_signature
            form = RegistrationForm(data=data, offering=offering, settings_obj=settings_obj)
            assert not form.is_valid()
            assert "accepts_model_release" in form.errors

    def describe_compute_final_price_cents():
        def it_returns_full_price_for_non_member_without_discount(offering, settings_obj):
            form = RegistrationForm(data=_post_data(), offering=offering, settings_obj=settings_obj)
            assert form.is_valid()
            assert form.compute_final_price_cents() == 10000

        def it_applies_member_discount_when_member_is_set(offering, settings_obj):
            sentinel_member = object()  # we only check truthiness, not identity
            form = RegistrationForm(
                data=_post_data(), offering=offering, settings_obj=settings_obj, member=sentinel_member
            )
            assert form.is_valid()
            assert form.compute_final_price_cents() == 9000  # 10% off

        def it_applies_discount_code_on_top_of_member_discount(offering, settings_obj):
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            sentinel_member = object()
            form = RegistrationForm(
                data=_post_data(discount_code="save20"),
                offering=offering,
                settings_obj=settings_obj,
                member=sentinel_member,
            )
            assert form.is_valid(), form.errors
            # 10000 -> 9000 (member) -> 7200 (20% off)
            assert form.compute_final_price_cents() == 7200

        def it_floors_at_zero(offering, settings_obj):
            DiscountCodeFactory(code="FREE", discount_pct=None, discount_fixed_cents=999_999)
            form = RegistrationForm(data=_post_data(discount_code="FREE"), offering=offering, settings_obj=settings_obj)
            assert form.is_valid()
            assert form.compute_final_price_cents() == 0

    def describe_with_a_sale():
        @pytest.fixture
        def sale_offering(offering):
            offering.sale_enabled = True
            offering.sale_kind = ClassOffering.SaleKind.PERCENT
            offering.sale_percent = 20  # 10000 -> 8000
            offering.save()
            return offering

        def it_orders_sale_then_member_then_coupon_when_stacking_is_allowed(sale_offering, settings_obj):
            sale_offering.sale_allow_discount_codes = True
            sale_offering.save()
            code = DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                data=_post_data(discount_code="SAVE20"),
                offering=sale_offering,
                settings_obj=settings_obj,
                member=object(),
            )
            assert form.is_valid(), form.errors
            # 10000 -> 8000 (sale) -> 7200 (member 10%) -> 5760 (code 20%)
            member_of_sale = int(sale_offering.sale_price_cents * 90 / 100)
            assert form.compute_final_price_cents() == code.apply_to(member_of_sale) == 5760

        def it_applies_the_member_discount_off_the_sale_price(sale_offering, settings_obj):
            form = RegistrationForm(
                data=_post_data(), offering=sale_offering, settings_obj=settings_obj, member=object()
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 7200  # 10% off the 8000 sale price

        def describe_when_the_sale_blocks_codes():
            def it_drops_the_discount_code_field(sale_offering, settings_obj):
                form = RegistrationForm(offering=sale_offering, settings_obj=settings_obj)
                assert form.sale_blocks_codes is True
                assert "discount_code" not in form.fields

            def it_ignores_a_submitted_code_instead_of_erroring(sale_offering, settings_obj):
                DiscountCodeFactory(code="SAVE20", discount_pct=20)
                form = RegistrationForm(
                    data=_post_data(discount_code="SAVE20"), offering=sale_offering, settings_obj=settings_obj
                )
                assert form.is_valid(), form.errors
                assert form.compute_final_price_cents() == 8000  # sale price, code silently dropped

            def it_skips_the_auto_apply_prefill(sale_offering, settings_obj):
                DiscountCodeFactory(code="AUTO", discount_pct=20, class_offering=sale_offering, auto_apply=True)
                form = RegistrationForm(offering=sale_offering, settings_obj=settings_obj)
                assert form.auto_applied_discount is None

        def it_flags_sale_blocks_codes_false_when_stacking_is_allowed(sale_offering, settings_obj):
            sale_offering.sale_allow_discount_codes = True
            sale_offering.save()
            form = RegistrationForm(offering=sale_offering, settings_obj=settings_obj)
            assert form.sale_blocks_codes is False
            assert "discount_code" in form.fields

        def it_auto_applies_off_the_sale_base_when_stacking_is_on(sale_offering, settings_obj):
            # Which auto-apply code wins depends on the base: off the full 10000,
            # HALF (5000) beats FLAT45 (5500); off the 8000 sale base, FLAT45
            # (3500) beats HALF (4000). Picking FLAT45 proves the sale base won.
            sale_offering.sale_allow_discount_codes = True
            sale_offering.member_discount_pct = 0
            sale_offering.save()
            DiscountCodeFactory(code="HALF", discount_pct=50, class_offering=sale_offering, auto_apply=True)
            flat = DiscountCodeFactory(
                code="FLAT45",
                discount_pct=None,
                discount_fixed_cents=4500,
                class_offering=sale_offering,
                auto_apply=True,
            )
            form = RegistrationForm(offering=sale_offering, settings_obj=settings_obj)
            assert form.auto_applied_discount == flat
            assert form.fields["discount_code"].initial == "FLAT45"

        def it_behaves_exactly_as_before_when_the_sale_is_off(offering, settings_obj):
            # sale-OFF regression: fields present, sale price collapses to the full
            # price, and the pre-feature member+code math is unchanged.
            offering.sale_enabled = False
            offering.sale_percent = 20  # dormant settings must be inert
            offering.save()
            assert offering.sale_price_cents == offering.price_cents == 10000
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                data=_post_data(discount_code="SAVE20"),
                offering=offering,
                settings_obj=settings_obj,
                member=object(),
            )
            assert "discount_code" in form.fields
            assert form.sale_blocks_codes is False
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 7200  # 10000 -> 9000 -> 7200, pre-feature result

        def it_rejects_a_total_landing_below_the_stripe_floor(sale_offering, settings_obj):
            # 8000 sale base with a fixed code leaving 40¢ — the friendly error fires.
            sale_offering.sale_allow_discount_codes = True
            sale_offering.member_discount_pct = 0
            sale_offering.save()
            DiscountCodeFactory(code="ALMOST", discount_pct=None, discount_fixed_cents=7960)
            form = RegistrationForm(
                data=_post_data(discount_code="ALMOST"), offering=sale_offering, settings_obj=settings_obj
            )
            assert not form.is_valid()
            assert "$0.50" in str(form.errors)

        def it_takes_the_free_path_when_the_total_reaches_exactly_zero(sale_offering, settings_obj):
            sale_offering.sale_allow_discount_codes = True
            sale_offering.member_discount_pct = 0
            sale_offering.save()
            DiscountCodeFactory(code="ZERO", discount_pct=None, discount_fixed_cents=8000)
            form = RegistrationForm(
                data=_post_data(discount_code="ZERO"), offering=sale_offering, settings_obj=settings_obj
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 0

    def describe_save():
        def it_creates_registration_with_offering_attached(offering, settings_obj):
            form = RegistrationForm(data=_post_data(), offering=offering, settings_obj=settings_obj)
            assert form.is_valid()
            registration = form.save()
            assert registration.class_offering_id == offering.pk
            assert registration.email == "sam@example.com"
            assert registration.self_serve_token

        def it_creates_a_liability_waiver_record(offering, settings_obj):
            form = RegistrationForm(
                data=_post_data(), offering=offering, settings_obj=settings_obj, client_ip="10.0.0.1"
            )
            assert form.is_valid()
            registration = form.save()
            waiver = registration.waivers.get(kind=Waiver.Kind.LIABILITY)
            assert waiver.signature_text == "Sam Smith"
            assert waiver.ip_address == "10.0.0.1"
            assert "ASSUMPTION OF RISK" in waiver.waiver_text

        def it_creates_both_waivers_when_model_release_required(offering, settings_obj):
            offering.requires_model_release = True
            offering.save()
            data = _post_data(model_release_signature="Sam Smith", accepts_model_release="on")
            form = RegistrationForm(data=data, offering=offering, settings_obj=settings_obj)
            assert form.is_valid(), form.errors
            registration = form.save()
            kinds = set(registration.waivers.values_list("kind", flat=True))
            assert kinds == {Waiver.Kind.LIABILITY, Waiver.Kind.MODEL_RELEASE}

    def describe_newsletter_checkbox_visibility():
        def it_shows_checkbox_for_an_anonymous_user(db):
            form = _build_form(user=None)
            assert "wants_newsletter" in form.fields

        def it_shows_checkbox_for_a_user_who_has_not_opted_in(db):
            from django.contrib.auth import get_user_model

            from core.models import UserProfile

            user = get_user_model().objects.create_user(username="fresh", email="fresh@example.com", password="x")
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=None)
            form = _build_form(user=user)
            assert "wants_newsletter" in form.fields

        def it_hides_checkbox_for_a_user_who_already_opted_in(db):
            from django.contrib.auth import get_user_model
            from django.utils import timezone

            from core.models import UserProfile

            user = get_user_model().objects.create_user(username="opted", email="opted@example.com", password="x")
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=timezone.now())
            form = _build_form(user=user)
            assert "wants_newsletter" not in form.fields

        def it_shows_checkbox_for_a_user_with_no_profile(db):
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.create_user(username="noprofile", email="np@example.com", password="x")
            form = _build_form(user=user)
            assert "wants_newsletter" in form.fields

    def describe_newsletter_opt_in_recorded_on_save(offering, settings_obj):
        def _opted_in_user():
            from django.contrib.auth import get_user_model
            from django.utils import timezone

            from core.models import UserProfile

            user = get_user_model().objects.create_user(username="back", email="back@example.com", password="x")
            UserProfile.objects.create(user=user, subscribed_to_mailchimp_at=timezone.now())
            return user

        def it_records_the_opt_in_when_the_checkbox_was_hidden(db):
            # The field is popped, so it can never bind. save() has to record the
            # opt-in this person already has, or the row is indistinguishable from
            # a deliberate untick and the class tags are silently dropped.
            form = RegistrationForm(
                data=_post_data(),
                offering=offering,
                settings_obj=settings_obj,
                user=_opted_in_user(),
            )
            assert "wants_newsletter" not in form.fields
            assert form.is_valid(), form.errors
            assert form.save().wants_newsletter is True

        def it_honours_an_explicit_untick_when_the_checkbox_was_shown(db):
            # A previously opted-in person booking while logged out still SEES the
            # box (visibility keys off the request user). Leaving it unticked must
            # mean no, even though Registration.save() auto-links them by email.
            _opted_in_user()
            form = RegistrationForm(
                data=_post_data(email="back@example.com"),
                offering=offering,
                settings_obj=settings_obj,
                user=None,
            )
            assert "wants_newsletter" in form.fields
            assert form.is_valid(), form.errors
            assert form.save().wants_newsletter is False

        def it_records_a_ticked_checkbox_normally(db):
            form = RegistrationForm(
                data=_post_data(wants_newsletter="on"),
                offering=offering,
                settings_obj=settings_obj,
                user=None,
            )
            assert form.is_valid(), form.errors
            assert form.save().wants_newsletter is True

    def describe_create_account_checkbox_visibility():
        def it_shows_checkbox_for_an_anonymous_user(db):
            form = _build_form(user=None)
            assert "create_account" in form.fields

        def it_defaults_the_checkbox_to_checked_for_an_anonymous_user(db):
            form = _build_form(user=None)
            assert form.fields["create_account"].initial is True

        def it_makes_the_checkbox_optional(db):
            form = _build_form(user=None)
            assert form.fields["create_account"].required is False

        def it_hides_checkbox_for_an_authenticated_user(db):
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.create_user(username="member", email="member@example.com", password="x")
            form = _build_form(user=user)
            assert "create_account" not in form.fields

        def it_persists_create_account_on_save(offering, settings_obj):
            data = _post_data(create_account="on")
            form = RegistrationForm(data=data, offering=offering, settings_obj=settings_obj)
            assert form.is_valid(), form.errors
            registration = form.save()
            registration.refresh_from_db()
            assert registration.create_account is True


def describe_the_member_discount_toggle():
    """#369 items 2 and 3: one price engine behind the quote and the charge, and a member may decline."""

    def it_is_offered_to_a_member_and_starts_on(offering, settings_obj):
        form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object())
        field = form.fields["apply_member_discount"]
        assert field.initial is True and field.required is False
        assert field.label == "Apply my member discount"
        assert field.help_text == "Turn this off to pay full price and give the difference to the space."
        assert field.widget.attrs["form"] == "reg-form"  # rendered outside the <form>, submitted with it

    def it_is_not_offered_to_a_non_member(offering, settings_obj):
        form = RegistrationForm(offering=offering, settings_obj=settings_obj)
        assert "apply_member_discount" not in form.fields

    def it_is_not_offered_on_the_waitlist(offering, settings_obj):
        form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object(), is_waitlist=True)
        assert "apply_member_discount" not in form.fields
        assert "discount_code" not in form.fields

    def describe_compute_final_price_cents():
        def it_charges_the_full_price_when_declined(offering, settings_obj):
            form = RegistrationForm(
                data=_post_data(apply_member_discount=""), offering=offering, settings_obj=settings_obj, member=object()
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 10000

        def it_still_takes_the_code_off_the_full_price_when_declined(offering, settings_obj):
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                data=_post_data(discount_code="SAVE20", apply_member_discount=""),
                offering=offering,
                settings_obj=settings_obj,
                member=object(),
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 8000  # not 7200

        def it_declines_off_the_sale_price(offering, settings_obj):
            offering.sale_enabled = True
            offering.sale_kind = ClassOffering.SaleKind.PERCENT
            offering.sale_percent = 20
            offering.save()
            form = RegistrationForm(
                data=_post_data(apply_member_discount=""), offering=offering, settings_obj=settings_obj, member=object()
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 8000  # the sale still applies; the member step does not

        def it_applies_the_discount_for_a_non_member_form_that_has_no_box(offering, settings_obj):
            # No toggle to turn off: the key in the POST is ignored and the (zero) member step runs.
            form = RegistrationForm(
                data=_post_data(apply_member_discount=""), offering=offering, settings_obj=settings_obj
            )
            assert form.is_valid(), form.errors
            assert form.compute_final_price_cents() == 10000

        def it_keeps_the_discount_when_the_post_never_carried_the_toggle(offering, settings_obj):
            # A member's email typed and submitted before (or without) the refresh: the box was
            # never on the page, so absence is not a decline. The quote agrees with the charge.
            data = _post_data()
            del data["apply_member_discount"]
            form = RegistrationForm(data=data, offering=offering, settings_obj=settings_obj, member=object())
            assert form.is_valid(), form.errors
            assert form.cleaned_data["apply_member_discount"] is True
            assert form.compute_final_price_cents() == 9000
            assert form.quoted_price_cents() == 9000

    def describe_the_auto_apply_choice():
        @pytest.fixture
        def two_codes(offering):
            # Off the full 10000, HALF (5000) beats FLAT46 (5400); off the 9000 member base,
            # FLAT46 (4400) beats HALF (4500). Which one wins proves which base was used.
            half = DiscountCodeFactory(code="HALF", discount_pct=50, class_offering=offering, auto_apply=True)
            flat = DiscountCodeFactory(
                code="FLAT46", discount_pct=None, discount_fixed_cents=4600, class_offering=offering, auto_apply=True
            )
            return half, flat

        def it_picks_against_the_member_price_when_the_box_is_on(offering, settings_obj, two_codes):
            _half, flat = two_codes
            form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object())
            assert form.auto_applied_discount == flat

        def it_picks_against_the_full_price_when_the_box_is_off(offering, settings_obj, two_codes):
            half, _flat = two_codes
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"apply_member_discount": False}
            )
            assert form.auto_applied_discount == half

    def describe_quoted_price_cents():
        def it_quotes_the_member_price_on_first_render(offering, settings_obj):
            form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object())
            assert form.quoted_price_cents() == 9000
            assert form.quotes_member_discount is True

        def it_quotes_the_full_price_to_a_non_member(offering, settings_obj):
            form = RegistrationForm(offering=offering, settings_obj=settings_obj)
            assert form.quoted_price_cents() == 10000
            assert form.quotes_member_discount is False

        def it_quotes_the_full_price_when_the_initial_turns_the_box_off(offering, settings_obj):
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"apply_member_discount": False}
            )
            assert form.quoted_price_cents() == 10000
            assert form.quotes_member_discount is False

        def it_quotes_the_auto_applied_code_the_box_shows_on_first_render(offering, settings_obj):
            DiscountCodeFactory(code="AUTO", discount_pct=20, class_offering=offering, auto_apply=True)
            form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object())
            assert form.fields["discount_code"].initial == "AUTO"
            assert form.quoted_price_cents() == 7200  # 9000 less 20%

        def it_quotes_no_code_once_the_registrant_clears_the_box(offering, settings_obj):
            # The refresh carries the cleared box as ""; the quote must match what a submit would charge.
            DiscountCodeFactory(code="AUTO", discount_pct=20, class_offering=offering, auto_apply=True)
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"discount_code": ""}
            )
            assert form["discount_code"].value() == ""
            assert form.quoted_price_cents() == 9000

        def it_quotes_a_typed_code_leniently(offering, settings_obj):
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"discount_code": " save20 "}
            )
            assert form.quoted_price_cents() == 7200

        def it_quotes_no_code_for_an_unknown_or_expired_one_and_never_raises(offering, settings_obj):
            DiscountCodeFactory(code="OLD", discount_pct=20, valid_until=date.today() - timedelta(days=1))
            for raw in ("NOPE", "OLD"):
                form = RegistrationForm(
                    offering=offering, settings_obj=settings_obj, member=object(), initial={"discount_code": raw}
                )
                assert form.quoted_price_cents() == 9000, raw

        def it_ignores_a_code_scoped_to_another_class(offering, settings_obj):
            DiscountCodeFactory(code="ELSEWHERE", discount_pct=50, class_offering=ClassOfferingFactory())
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"discount_code": "ELSEWHERE"}
            )
            assert form.quoted_price_cents() == 9000

        def it_reads_the_post_when_bound_and_matches_the_charge(offering, settings_obj):
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                data=_post_data(discount_code="SAVE20", apply_member_discount=""),
                offering=offering,
                settings_obj=settings_obj,
                member=object(),
            )
            assert form.is_valid(), form.errors
            assert form.quoted_price_cents() == form.compute_final_price_cents() == 8000
            assert form.quotes_member_discount is False

        def it_quotes_no_code_when_the_sale_blocks_codes(offering, settings_obj):
            offering.sale_enabled = True
            offering.sale_kind = ClassOffering.SaleKind.PERCENT
            offering.sale_percent = 20
            offering.save()
            DiscountCodeFactory(code="SAVE20", discount_pct=20)
            form = RegistrationForm(
                offering=offering, settings_obj=settings_obj, member=object(), initial={"discount_code": "SAVE20"}
            )
            assert "discount_code" not in form.fields
            assert form.quoted_price_cents() == 7200  # 8000 sale price less the member 10%

        def it_quotes_the_member_price_on_the_waitlist_where_the_box_is_hidden(offering, settings_obj):
            # A promoted waitlister always gets the discount, so that is the number quoted.
            form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object(), is_waitlist=True)
            assert form.quoted_price_cents() == 9000
            assert form.quotes_member_discount is True

    def describe_the_price_refresh():
        def it_wires_the_email_the_toggle_and_the_code_box_alike(offering, settings_obj):
            form = RegistrationForm(offering=offering, settings_obj=settings_obj, member=object())
            for name in ("email", "apply_member_discount", "discount_code"):
                attrs = form.fields[name].widget.attrs
                assert attrs["hx-get"] == f"/classes/{offering.slug}/register/", name
                assert attrs["hx-trigger"] == "change", name
                assert attrs["hx-include"] == "[name=email],[name=apply_member_discount],[name=discount_code]", name
                assert attrs["hx-target"] == attrs["hx-select"] == "#reg-price-summary", name
                assert attrs["hx-swap"] == "outerHTML", name
                assert attrs["hx-select-oob"] == "#reg-submit-label", name

        def it_also_refreshes_the_questions_block_when_the_class_asks_questions(offering, settings_obj):
            from classes.models import RegistrationQuestion

            RegistrationQuestion.objects.create(prompt="Allergies?", sort_order=1)
            form = RegistrationForm(offering=offering, settings_obj=settings_obj)
            assert form.fields["email"].widget.attrs["hx-select-oob"] == "#reg-submit-label,#custom-questions-block"
