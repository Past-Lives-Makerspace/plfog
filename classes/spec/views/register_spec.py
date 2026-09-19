"""BDD specs for the public registration views."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, CmsActivity, Registration

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_offering(db):
    offering = ClassOfferingFactory(
        title="Forge Basics",
        slug="forge-basics",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        member_discount_pct=10,
        capacity=4,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


@pytest.fixture
def free_offering(db):
    offering = ClassOfferingFactory(
        title="Free Demo",
        slug="free-demo",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=0,
        member_discount_pct=0,
        capacity=4,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=3),
        ends_at=timezone.now() + timedelta(days=3, hours=2),
    )
    return offering


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


def _verify(member_user) -> None:
    """Make the fixture member findable by email: the register view matches VERIFIED addresses only."""
    from allauth.account.models import EmailAddress

    EmailAddress.objects.update_or_create(
        user=member_user, email="member@example.com", defaults={"verified": True, "primary": True}
    )


def _price_summary(body: str) -> str:
    """The #reg-price-summary block and nothing after the form opens, so the changelog modal can't leak in."""
    return body[body.index('id="reg-price-summary"') : body.index('id="reg-form"')]


def describe_register_view():
    def it_renders_the_form(paid_offering, client):
        response = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        assert b"Liability Waiver" in response.content
        assert b"Next" in response.content

    def it_prefills_form_for_a_logged_in_member(paid_offering, client, member_user):
        member = member_user.member
        member.full_legal_name = "Robin Hood"
        member.phone = "503-555-0100"
        member.pronouns = "they/them"
        member.save()
        client.force_login(member_user)
        response = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'value="Robin"' in body
        assert 'value="Hood"' in body
        assert 'value="member@example.com"' in body
        assert 'value="503-555-0100"' in body
        assert 'value="they/them"' in body

    def it_404s_for_unpublished_class(db, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, slug="hidden")
        response = client.get(reverse("classes:register", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_confirms_immediately_for_a_free_class(free_offering, client):
        response = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=_post_data())
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": free_offering.slug})
        registration = Registration.objects.get(class_offering=free_offering)
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.confirmed_at is not None
        assert registration.amount_paid_cents == 0
        assert len(mail.outbox) == 2  # confirmation + instructor notification
        assert "confirmed" in mail.outbox[0].subject.lower()

    def it_also_sends_the_instructor_welcome_email_when_enabled(free_offering, client):
        free_offering.welcome_email_enabled = True
        free_offering.welcome_email_subject = "Welcome to the demo"
        free_offering.welcome_email_body = "Bring your curiosity."
        free_offering.save(update_fields=["welcome_email_enabled", "welcome_email_subject", "welcome_email_body"])
        client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=_post_data())
        subjects = [m.subject for m in mail.outbox]
        assert "Welcome to the demo" in subjects  # fires alongside the order confirmation

    def it_attributes_free_class_confirmation_to_the_registrant(free_offering, client, member_user):
        from classes.models import CmsActivity

        client.force_login(member_user)
        client.post(
            reverse("classes:register", kwargs={"slug": free_offering.slug}),
            data=_post_data(email="member@example.com"),
        )
        registration = Registration.objects.get(class_offering=free_offering)
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_CONFIRMED, registration=registration)
        assert row.actor == member_user

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_kicks_off_stripe_checkout_for_paid_classes(mock_checkout, paid_offering, client):
        mock_checkout.return_value = {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"}

        response = client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())

        assert response.status_code == 302
        assert response.url == "https://checkout.stripe.com/c/pay/cs_test_123"
        registration = Registration.objects.get(class_offering=paid_offering)
        assert registration.status == Registration.Status.PENDING
        assert registration.stripe_session_id == "cs_test_123"
        assert registration.amount_paid_cents == 10000  # provisional, no member match

        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["amount_cents"] == 10000
        assert kwargs["customer_email"] == "sam@example.com"
        assert kwargs["metadata"]["registration_id"] == str(registration.pk)
        assert kwargs["metadata"]["kind"] == "class_registration"

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_rolls_back_registration_when_stripe_fails(mock_checkout, paid_offering, client):
        mock_checkout.side_effect = RuntimeError("stripe down")
        with pytest.raises(RuntimeError):
            client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())
        assert not Registration.objects.filter(class_offering=paid_offering).exists()

    def it_joins_waitlist_when_sold_out(paid_offering, client):
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        response = client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())
        assert response.status_code == 302
        registration = Registration.objects.get(email="sam@example.com", class_offering=paid_offering)
        assert registration.status == Registration.Status.WAITLISTED

    def it_logs_waitlist_joined_activity_not_registration_created(paid_offering, client):
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        client.post(reverse("classes:register", kwargs={"slug": paid_offering.slug}), data=_post_data())
        registration = Registration.objects.get(email="sam@example.com", class_offering=paid_offering)
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.WAITLIST_JOINED, registration=registration).exists()
        assert not CmsActivity.objects.filter(
            kind=CmsActivity.Kind.REGISTRATION_CREATED, registration=registration
        ).exists()

    def it_subscribes_to_mailchimp_when_free_registrant_opts_in(free_offering, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()

        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            client.post(
                reverse("classes:register", kwargs={"slug": free_offering.slug}),
                data=_post_data(wants_newsletter="on"),
            )
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["email"] == "sam@example.com"
        assert "class-registrant" in kwargs["tags"]
        registration = Registration.objects.get(class_offering=free_offering)
        assert registration.subscribed_to_mailchimp is True

    def it_does_not_subscribe_free_registrant_who_did_not_opt_in(free_offering, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()

        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            client.post(
                reverse("classes:register", kwargs={"slug": free_offering.slug}),
                data=_post_data(),  # wants_newsletter not set
            )
        spy.assert_not_called()


def describe_register_view_when_class_registration_disabled():
    def _disable_registration(note="Online registration is paused right now."):
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.class_registration_enabled = False
        config.class_registration_disabled_note = note
        config.save()

    def it_redirects_a_get_to_the_detail_page_with_the_note(free_offering, client):
        _disable_registration(note="Call the studio to sign up.")
        response = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))
        assert response.status_code == 302
        assert response.url == reverse("classes:public_class_detail", kwargs={"slug": free_offering.slug})

        from django.contrib.messages import get_messages

        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert "Call the studio to sign up." in messages

    def it_refuses_a_post_and_creates_no_registration(free_offering, client):
        _disable_registration()
        response = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=_post_data())
        assert response.status_code == 302
        assert response.url == reverse("classes:public_class_detail", kwargs={"slug": free_offering.slug})
        assert not Registration.objects.filter(class_offering=free_offering).exists()

    def it_falls_back_to_a_generic_message_when_the_note_is_blank(free_offering, client):
        _disable_registration(note="")
        response = client.get(reverse("classes:register", kwargs={"slug": free_offering.slug}))

        from django.contrib.messages import get_messages

        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert "Online registration is currently unavailable." in messages

    def it_allows_registration_when_the_switch_is_on(free_offering, client):
        # Default is enabled — the free class still confirms normally.
        response = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=_post_data())
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": free_offering.slug})
        assert Registration.objects.filter(class_offering=free_offering).exists()


def describe_register_success_view():
    def it_renders_a_thanks_page(paid_offering, client):
        response = client.get(reverse("classes:register_success", kwargs={"slug": paid_offering.slug}))
        assert response.status_code == 200
        assert b"You're registered" in response.content


def describe_register_cancelled_view():
    def it_deletes_the_pending_registration_when_token_provided(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.PENDING)
        url = reverse("classes:register_cancelled", kwargs={"slug": paid_offering.slug})
        response = client.get(f"{url}?reg={registration.self_serve_token}")
        assert response.status_code == 200
        assert not Registration.objects.filter(pk=registration.pk).exists()

    def it_keeps_confirmed_registrations_intact(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:register_cancelled", kwargs={"slug": paid_offering.slug})
        client.get(f"{url}?reg={registration.self_serve_token}")
        assert Registration.objects.filter(pk=registration.pk).exists()


def describe_my_registration_view():
    def it_renders_via_token(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        response = client.get(reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}))
        assert response.status_code == 200
        assert paid_offering.title.encode() in response.content

    def it_404s_on_unknown_token(db, client):
        response = client.get(reverse("classes:my_registration", kwargs={"token": "nope"}))
        assert response.status_code == 404

    def it_self_cancels_via_post(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token})
        response = client.post(url)
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CANCELLED
        assert registration.cancelled_at is not None

    def it_redirects_get_on_cancel_endpoint_back_to_self_serve(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        url = reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token})
        response = client.get(url)
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED  # unchanged

    def it_does_nothing_when_registration_already_cancelled(paid_offering, client):
        registration = RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CANCELLED)
        url = reverse("classes:my_registration_cancel", kwargs={"token": registration.self_serve_token})
        response = client.post(url)
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CANCELLED


def describe_registration_initial_for_user():
    def it_returns_email_when_user_has_no_member_record(db, client):
        """Authenticated user whose Member was deleted — only email is pre-filled."""
        from classes.factories import UserFactory

        user = UserFactory(username="nomember@example.com", email="nomember@example.com")
        # The signal auto-creates a Member; delete it so user.member raises RelatedObjectDoesNotExist,
        # causing getattr(user, "member", None) to return None (line 188 branch).
        user.member.delete()
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            slug="nomember-test",
            category=CategoryFactory(),
            instructor=InstructorFactory(),
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=5),
            ends_at=timezone.now() + timedelta(days=5, hours=2),
        )
        client.force_login(user)
        response = client.get(reverse("classes:register", kwargs={"slug": offering.slug}))
        assert response.status_code == 200
        assert b"nomember@example.com" in response.content


def describe_register_with_discount_code():
    def it_bumps_discount_use_count_on_free_class_with_code(db, client):
        """Free class + active discount code → use_count increments on confirm."""
        from classes.factories import DiscountCodeFactory

        code = DiscountCodeFactory(code="FREEBIE", discount_pct=100, is_active=True)
        offering = ClassOfferingFactory(
            title="Free With Code",
            slug="free-with-code",
            category=CategoryFactory(),
            instructor=InstructorFactory(),
            status=ClassOffering.Status.PUBLISHED,
            price_cents=5000,
            member_discount_pct=0,
            capacity=10,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=7),
            ends_at=timezone.now() + timedelta(days=7, hours=2),
        )
        data = {
            "first_name": "Dee",
            "last_name": "Count",
            "pronouns": "",
            "email": "dee@example.com",
            "phone": "",
            "prior_experience": "",
            "looking_for": "",
            "discount_code": "FREEBIE",
            "liability_signature": "Dee Count",
            "accepts_liability": "on",
        }
        response = client.post(reverse("classes:register", kwargs={"slug": offering.slug}), data=data)
        assert response.status_code == 302
        code.refresh_from_db()
        assert code.use_count == 1


def describe_register_with_a_sale():
    @pytest.fixture
    def sale_offering(paid_offering):
        paid_offering.sale_enabled = True
        paid_offering.sale_kind = ClassOffering.SaleKind.PERCENT
        paid_offering.sale_percent = 20  # $100 -> $80
        paid_offering.save()
        return paid_offering

    def it_shows_the_struck_original_and_sale_price_in_the_summary(sale_offering, client):
        body = client.get(reverse("classes:register", kwargs={"slug": sale_offering.slug})).content.decode()
        assert '<span class="reg-was">$100</span> $80' in body
        assert 'class="reg-sale-badge"' in body

    def it_shows_the_sale_price_on_the_submit_button(sale_offering, client):
        body = client.get(reverse("classes:register", kwargs={"slug": sale_offering.slug})).content.decode()
        assert 'Next: <span id="reg-submit-label">$80</span>' in body

    def it_hides_the_code_box_and_explains_when_codes_are_blocked(sale_offering, client):
        body = client.get(reverse("classes:register", kwargs={"slug": sale_offering.slug})).content.decode()
        assert "Discount code (optional)" not in body
        assert 'class="reg-sale-note"' in body
        assert "can't be combined with this offer" in body

    def it_keeps_the_code_box_when_the_sale_allows_stacking(sale_offering, client):
        sale_offering.sale_allow_discount_codes = True
        sale_offering.save()
        body = client.get(reverse("classes:register", kwargs={"slug": sale_offering.slug})).content.decode()
        assert "Discount code (optional)" in body
        assert 'class="reg-sale-note"' not in body

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_charges_the_sale_price_and_marks_the_stripe_product_name(mock_checkout, sale_offering, client):
        mock_checkout.return_value = {"id": "cs_test_sale", "url": "https://checkout.stripe.com/c/pay/cs_test_sale"}
        response = client.post(reverse("classes:register", kwargs={"slug": sale_offering.slug}), data=_post_data())
        assert response.status_code == 302
        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["amount_cents"] == 8000
        assert kwargs["product_name"].endswith(" (Sale)")

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_takes_the_free_confirm_path_when_the_sale_total_reaches_zero(mock_checkout, sale_offering, client):
        from classes.factories import DiscountCodeFactory

        sale_offering.sale_allow_discount_codes = True
        sale_offering.member_discount_pct = 0
        sale_offering.save()
        DiscountCodeFactory(code="ZERO", discount_pct=None, discount_fixed_cents=8000)
        response = client.post(
            reverse("classes:register", kwargs={"slug": sale_offering.slug}),
            data=_post_data(discount_code="ZERO"),
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": sale_offering.slug})
        registration = Registration.objects.get(class_offering=sale_offering)
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.amount_paid_cents == 0
        mock_checkout.assert_not_called()


def describe_a_total_that_reaches_zero_through_discounts():
    # #368 item 5 removed the free option from the composer. A $0 total is still reachable through
    # discounts, and it still confirms on the spot without Stripe.

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_confirms_without_stripe_on_a_full_discount_code(mock_checkout, paid_offering, client):
        from classes.factories import DiscountCodeFactory

        DiscountCodeFactory(code="ONTHEHOUSE", discount_pct=100)
        response = client.post(
            reverse("classes:register", kwargs={"slug": paid_offering.slug}),
            data=_post_data(discount_code="ONTHEHOUSE"),
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": paid_offering.slug})
        registration = Registration.objects.get(class_offering=paid_offering)
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.amount_paid_cents == 0
        mock_checkout.assert_not_called()

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_confirms_without_stripe_on_a_full_member_discount(mock_checkout, paid_offering, client, member_user):
        _verify(member_user)
        paid_offering.member_discount_pct = 100
        paid_offering.save(update_fields=["member_discount_pct"])
        response = client.post(
            reverse("classes:register", kwargs={"slug": paid_offering.slug}),
            data=_post_data(email="member@example.com"),
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:register_success", kwargs={"slug": paid_offering.slug})
        registration = Registration.objects.get(class_offering=paid_offering)
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.amount_paid_cents == 0
        mock_checkout.assert_not_called()


def describe_client_ip():
    def it_extracts_ip_from_x_forwarded_for_header(db, client):
        """When X-Forwarded-For is present, the first IP is used — verified via registration."""
        offering = ClassOfferingFactory(
            title="Proxy Class",
            slug="proxy-class",
            category=CategoryFactory(),
            instructor=InstructorFactory(),
            status=ClassOffering.Status.PUBLISHED,
            price_cents=0,
            member_discount_pct=0,
            capacity=10,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        data = {
            "first_name": "Proxy",
            "last_name": "User",
            "pronouns": "",
            "email": "proxy@example.com",
            "phone": "",
            "prior_experience": "",
            "looking_for": "",
            "discount_code": "",
            "liability_signature": "Proxy User",
            "accepts_liability": "on",
        }
        response = client.post(
            reverse("classes:register", kwargs={"slug": offering.slug}),
            data=data,
            HTTP_X_FORWARDED_FOR="203.0.113.42, 10.0.0.1",
        )
        # Registration succeeds — form received the proxied IP without error.
        assert response.status_code == 302
        assert Registration.objects.filter(class_offering=offering).exists()


def describe_the_member_discount_at_checkout():
    """#369 items 2 and 3: the page quotes the price it will charge, and a member may decline the discount."""

    @pytest.fixture
    def verified_member(member_user):
        _verify(member_user)
        return member_user

    def it_quotes_the_member_price_to_a_logged_in_member_on_first_render(paid_offering, client, verified_member):
        client.force_login(verified_member)
        body = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug})).content.decode()
        summary = _price_summary(body)
        assert '<div class="total">$90</div>' in summary
        assert "Member discount applied." in summary
        assert 'name="apply_member_discount"' in summary and "checked" in summary
        assert 'Next: <span id="reg-submit-label">$90</span>' in body

    def it_quotes_the_full_price_for_a_non_member_email(paid_offering, client):
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        body = client.get(url, {"email": "sam@example.com"}).content.decode()
        summary = _price_summary(body)
        assert '<div class="total">$100</div>' in summary
        assert "Member discount applied." not in summary
        assert 'name="apply_member_discount"' not in summary
        assert "Past Lives Members:" not in summary  # the old footnote is gone
        assert 'Next: <span id="reg-submit-label">$100</span>' in body

    def it_quotes_the_full_price_when_the_refresh_says_the_box_is_off(paid_offering, client, verified_member):
        # The hidden twin sends "" for an unchecked box.
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        body = client.get(url + "?email=member@example.com&apply_member_discount=").content.decode()
        summary = _price_summary(body)
        assert '<div class="total">$100</div>' in summary
        assert "Member discount applied." not in summary
        assert 'name="apply_member_discount"' in summary and "checked" not in summary
        assert 'Next: <span id="reg-submit-label">$100</span>' in body

    def it_quotes_the_member_price_when_the_refresh_says_the_box_is_on(paid_offering, client, verified_member):
        # A checked box sends its twin's "" and then "on"; the last value is the state.
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        query = "?email=member@example.com&apply_member_discount=&apply_member_discount=on"
        summary = _price_summary(client.get(url + query).content.decode())
        assert '<div class="total">$90</div>' in summary
        assert "checked" in summary

    def it_defaults_the_discount_on_when_the_page_had_no_box_yet(paid_offering, client, verified_member):
        # A guest typed a member's email into a page rendered for a non-member: no box was on
        # that page, so the refresh carries no key, and the member gets the default.
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        summary = _price_summary(client.get(url, {"email": "member@example.com"}).content.decode())
        assert '<div class="total">$90</div>' in summary
        assert "Member discount applied." in summary
        assert "checked" in summary

    def it_wires_every_input_the_quote_depends_on_to_refresh_the_summary(paid_offering, client, verified_member):
        client.force_login(verified_member)
        body = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug})).content.decode()
        for name in ("email", "apply_member_discount", "discount_code"):
            tag = next(t for t in body.split("<input")[1:] if f'name="{name}"' in t and 'type="hidden"' not in t)
            assert f'hx-get="{reverse("classes:register", kwargs={"slug": paid_offering.slug})}"' in tag, name
            assert 'hx-target="#reg-price-summary"' in tag and 'hx-select="#reg-price-summary"' in tag, name
            assert 'hx-select-oob="#reg-submit-label"' in tag, name
            assert 'hx-include="[name=email],[name=apply_member_discount],[name=discount_code]"' in tag, name

    def it_keeps_the_waitlist_flag_on_the_refresh_url(paid_offering, client, verified_member):
        # A voluntary waitlist page refreshes as a waitlist page, never as a paid form's summary.
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        body = client.get(url + "?waitlist=1").content.decode()
        email = next(t for t in body.split("<input")[1:] if 'name="email"' in t)
        assert f'hx-get="{url}?waitlist=1"' in email

    def it_keeps_the_claim_token_on_the_refresh_url(paid_offering, client, verified_member):
        # A claim link's refresh stays a claim: without the token, taking the last seat would
        # re-render the sold-out waitlist form, with no toggle and the wrong quote.
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        claim = RegistrationFactory(
            class_offering=paid_offering, status=Registration.Status.WAITLISTED, email="member@example.com"
        )
        client.force_login(verified_member)  # the member clicks the link from their own inbox
        url = reverse("classes:register", kwargs={"slug": paid_offering.slug})
        body = client.get(url + f"?waitlist_token={claim.self_serve_token}").content.decode()
        email = next(t for t in body.split("<input")[1:] if 'name="email"' in t)
        assert f'hx-get="{url}?waitlist_token={claim.self_serve_token}"' in email
        assert 'name="apply_member_discount"' in body  # the claim is a paid signup: the toggle is there

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_charges_the_full_price_when_the_member_declines(mock_checkout, paid_offering, client, verified_member):
        from classes.factories import DiscountCodeFactory

        DiscountCodeFactory(code="SAVE20", discount_pct=20)
        mock_checkout.return_value = {"id": "cs_test_full", "url": "https://checkout.stripe.com/c/pay/cs_test_full"}
        response = client.post(
            reverse("classes:register", kwargs={"slug": paid_offering.slug}),
            data=_post_data(email="member@example.com", discount_code="SAVE20", apply_member_discount=""),
        )
        assert response.status_code == 302
        # 10000, no member step, then 20% off: 8000. With the discount it would have been 7200.
        assert mock_checkout.call_args.kwargs["amount_cents"] == 8000
        assert Registration.objects.get(class_offering=paid_offering).amount_paid_cents == 8000

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_charges_the_member_price_when_the_box_stays_checked(mock_checkout, paid_offering, client, verified_member):
        mock_checkout.return_value = {"id": "cs_test_mem", "url": "https://checkout.stripe.com/c/pay/cs_test_mem"}
        response = client.post(
            reverse("classes:register", kwargs={"slug": paid_offering.slug}),
            data=_post_data(email="member@example.com"),
        )
        assert response.status_code == 302
        assert mock_checkout.call_args.kwargs["amount_cents"] == 9000

    def it_hides_the_toggle_on_the_waitlist_form(paid_offering, client, verified_member):
        for _ in range(paid_offering.capacity):
            RegistrationFactory(class_offering=paid_offering, status=Registration.Status.CONFIRMED)
        client.force_login(verified_member)
        body = client.get(reverse("classes:register", kwargs={"slug": paid_offering.slug})).content.decode()
        assert 'name="apply_member_discount"' not in body
        assert 'name="discount_code"' not in body
