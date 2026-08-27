"""BDD specs for the roster/waitlist management endpoints, gating, and roster UI states."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import CmsActivity, Registration
from core.models import EventDelivery

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


def _waitlisted(offering, **kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.WAITLISTED)
    return RegistrationFactory(class_offering=offering, **kwargs)


def _unpaid(offering, **kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.CONFIRMED)
    kwargs.setdefault("payment_due_cents", 4500)
    kwargs.setdefault("amount_paid_cents", 0)
    return RegistrationFactory(class_offering=offering, **kwargs)


def _login_instructor(client, username: str, slug: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug=slug)
    client.force_login(user)
    return member


def _login_plain_member(client, username: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug="")
    client.force_login(user)
    return member


def describe_gating():
    def it_admits_the_class_instructor(client):
        member = _login_instructor(client, "gate-own@example.com", "gate-own")
        reg = _waitlisted(ClassOfferingFactory(instructor=member))
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200

    def it_rejects_another_instructor(client):
        _login_instructor(client, "gate-other@example.com", "gate-other")
        reg = _waitlisted(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 403

    def it_admits_the_guild_lead_of_the_class_guild(client):
        from classes.factories import CategoryFactory
        from tests.membership.factories import GuildFactory

        member = _login_plain_member(client, "gate-lead@example.com")
        guild = GuildFactory(guild_lead=member)
        reg = _waitlisted(ClassOfferingFactory(category=CategoryFactory(guild=guild)))
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200

    def it_rejects_a_plain_member(client):
        _login_plain_member(client, "gate-plain@example.com")
        reg = _waitlisted(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 403

    def it_redirects_anonymous_to_login(client):
        reg = _waitlisted(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 302
        assert "login" in response["Location"]

    def it_admits_an_actual_admin_even_while_previewing_another_role(admin_user, client):
        client.force_login(admin_user)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        reg = _waitlisted(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200

    def it_gates_the_followup_partial_identically(client):
        _login_instructor(client, "gate-fu@example.com", "gate-fu")
        reg = _unpaid(ClassOfferingFactory())
        response = client.get(reverse("classes:registration_promote_followup", args=[reg.pk]))
        assert response.status_code == 403


def describe_promote_endpoint():
    def it_takes_the_paid_path_with_the_followup_trigger(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(price_cents=4500, member_discount_pct=0)
        reg = _waitlisted(offering)
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["promote-followup"] == {"pk": reg.pk}
        assert "Added to class ✓" in response.content.decode()
        assert len(mailoutbox) == 0  # email waits for the modal choice

    def it_takes_the_free_path_for_a_free_class(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _waitlisted(ClassOfferingFactory(price_cents=0, member_discount_pct=0))
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        triggers = json.loads(response["HX-Trigger"])
        assert "promote-followup" not in triggers
        assert len(mailoutbox) == 1
        assert mailoutbox[0].subject.startswith("You're in!")

    def it_branches_on_computed_due_not_sticker_price(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(price_cents=4500, member_discount_pct=0)
        reg = _waitlisted(offering, discount_code=DiscountCodeFactory(discount_pct=100))
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        triggers = json.loads(response["HX-Trigger"])
        assert "promote-followup" not in triggers
        assert len(mailoutbox) == 1

    def it_toasts_the_guard_message_on_a_stale_row(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        response = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX)
        assert response.status_code == 200
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["showToast"]["type"] == "error"
        assert "Only waitlisted registrations" in triggers["showToast"]["message"]

    def it_renders_the_followup_partial_with_name_and_amount(admin_user, client):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory(), first_name="Jane")
        content = client.get(reverse("classes:registration_promote_followup", args=[reg.pk])).content.decode()
        assert "Jane" in content
        assert "45.00" in content


def describe_promote_notify():
    def it_sends_the_pay_link_email_and_stamps_the_row(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "send"}, headers=HTMX
        )
        assert response.status_code == 204
        reg.refresh_from_db()
        assert reg.payment_link_sent_at is not None
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.PAYMENT_LINK_SENT, registration=reg).exists()
        assert len(mailoutbox) == 1
        assert "Complete your payment" in mailoutbox[0].subject

    def it_sends_the_plain_promoted_email_on_skip(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "skip"}, headers=HTMX
        )
        assert response.status_code == 204
        assert len(mailoutbox) == 1
        assert "Complete your payment" not in mailoutbox[0].subject

    def it_noops_a_skip_after_a_send(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        client.post(reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "send"}, headers=HTMX)
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "skip"}, headers=HTMX
        )
        assert response.status_code == 204
        assert not response.has_header("HX-Trigger")
        assert len(mailoutbox) == 1  # the pay-link email only — no plain email stacked on

    def it_noops_a_skip_after_a_prior_skip(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        client.post(reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "skip"}, headers=HTMX)
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "skip"}, headers=HTMX
        )
        assert response.status_code == 204
        assert len(mailoutbox) == 1

    def it_rejects_an_unknown_choice(admin_user, client):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "maybe"}, headers=HTMX
        )
        assert response.status_code == 400

    def it_collapses_double_sends_in_the_same_minute_but_delivers_later_resends(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        url = reverse("classes:registration_send_payment_link", args=[reg.pk])
        first_minute = datetime(2026, 8, 26, 12, 0, 10, tzinfo=dt_timezone.utc)
        with patch("classes.emails.timezone.now", return_value=first_minute):
            client.post(url, headers=HTMX)
            client.post(url, headers=HTMX)
        assert len(mailoutbox) == 1
        with patch("classes.emails.timezone.now", return_value=first_minute + timedelta(minutes=2)):
            client.post(url, headers=HTMX)
        assert len(mailoutbox) == 2


def describe_roster_chip():
    def _roster_url(member, offering):
        return reverse("classes:teach_class_registrations", args=[offering.pk])

    def it_shows_no_email_sent_yet_until_either_email_goes_out(client):
        member = _login_instructor(client, "chip@example.com", "chip")
        offering = ClassOfferingFactory(instructor=member)
        reg = _unpaid(offering)
        url = reverse("classes:teach_class_registrations", args=[offering.pk])
        assert "No email sent yet" in client.get(url).content.decode()
        # After the plain promoted email (the reg:{pk}:promoted delivery), the chip disappears.
        EventDelivery.objects.create(
            event_key="waitlist_promoted",
            target_ref=f"email:{reg.email}",
            channel="email",
            period=f"reg:{reg.pk}:promoted",
        )
        assert "No email sent yet" not in client.get(url).content.decode()

    def it_swaps_the_chip_for_the_link_sent_line_after_a_pay_link(client):
        member = _login_instructor(client, "chip2@example.com", "chip2")
        offering = ClassOfferingFactory(instructor=member)
        reg = _unpaid(offering)
        from django.utils import timezone

        reg.payment_link_sent_at = timezone.now()
        reg.save(update_fields=["payment_link_sent_at"])
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "No email sent yet" not in content
        assert "Link sent" in content

    def it_keeps_the_roster_query_count_constant(client, django_assert_num_queries):
        from django.db import connection

        member = _login_instructor(client, "chip3@example.com", "chip3")
        offering = ClassOfferingFactory(instructor=member)
        _unpaid(offering, email="c1@example.com")
        url = reverse("classes:teach_class_registrations", args=[offering.pk])
        client.get(url)  # warm-up: one-time get_or_create setup (site config, settings) happens here
        with CaptureQueriesContext(connection) as small:
            assert client.get(url).status_code == 200
        _unpaid(offering, email="c2@example.com")
        _unpaid(offering, email="c3@example.com")
        _waitlisted(offering, email="c4@example.com")
        with CaptureQueriesContext(connection) as big:
            assert client.get(url).status_code == 200
        assert len(big) == len(small)


def describe_remove_modal_copy():
    def it_promises_the_claim_link_when_one_will_fire(client):
        member = _login_instructor(client, "modal1@example.com", "modal1")
        offering = ClassOfferingFactory(instructor=member, capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        _waitlisted(offering, email="next@example.com")
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "automatically emailed a claim link" in content

    def it_uses_the_no_email_variant_when_nobody_is_waiting(client):
        member = _login_instructor(client, "modal2@example.com", "modal2")
        offering = ClassOfferingFactory(instructor=member, capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "automatically emailed a claim link" not in content
        assert "No waitlist claim email will go out right now" in content

    def it_uses_the_no_email_variant_for_an_over_capacity_class_that_stays_full(client):
        member = _login_instructor(client, "modal3@example.com", "modal3")
        offering = ClassOfferingFactory(instructor=member, capacity=1)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, email="a@example.com")
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, email="b@example.com")
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, email="c@example.com")
        _waitlisted(offering, email="next@example.com")
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "automatically emailed a claim link" not in content


def describe_mark_paid_and_remove_endpoints():
    def it_marks_paid_with_the_note_in_the_activity_payload(admin_user, client):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(
            reverse("classes:registration_mark_paid", args=[reg.pk]), {"note": "cash", "row": "reg"}, headers=HTMX
        )
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Marked paid."
        assert f'id="reg-row-{reg.pk}"' in response.content.decode()
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_MARKED_PAID, registration=reg)
        assert row.payload == {"note": "cash"}

    def it_removes_with_the_reason_threaded_through(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, first_name="Jane")
        response = client.post(
            reverse("classes:registration_remove", args=[reg.pk]), {"reason": "no-show", "row": "reg"}, headers=HTMX
        )
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Jane removed from the class."
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CANCELLED
        assert reg.cancellation_reason == "no-show"

    def it_returns_the_waitlist_row_partial_when_asked(admin_user, client):
        client.force_login(admin_user)
        reg = _waitlisted(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_remove", args=[reg.pk]), {"row": "wl"}, headers=HTMX)
        assert f'id="wl-row-{reg.pk}"' in response.content.decode()
        assert json.loads(response["HX-Trigger"])["showToast"]["message"].endswith("removed from the waitlist.")

    def it_redirects_non_htmx_posts_back_to_the_detail_page(admin_user, client):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_mark_paid", args=[reg.pk]), {"note": ""})
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk})

    def it_redirects_non_htmx_send_payment_link_with_a_message(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        response = client.post(reverse("classes:registration_send_payment_link", args=[reg.pk]))
        assert response.status_code == 302
        assert len(mailoutbox) == 1

    def it_toasts_the_guard_when_nothing_is_owed(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)
        response = client.post(
            reverse("classes:registration_send_payment_link", args=[reg.pk]), {"row": "reg"}, headers=HTMX
        )
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"


def describe_waitlist_tab():
    def it_renders_the_action_buttons_and_promote_modal_amount(client):
        member = _login_instructor(client, "wl1@example.com", "wl1")
        offering = ClassOfferingFactory(instructor=member, price_cents=4500, member_discount_pct=0, capacity=5)
        _waitlisted(offering, first_name="Jane")
        content = client.get(reverse("classes:teach_class_waitlist", args=[offering.pk])).content.decode()
        assert "Add to Class" in content
        assert "$45.00" in content
        assert "No payment is required to hold the seat." in content

    def it_uses_the_free_copy_for_a_hundred_percent_code(client):
        member = _login_instructor(client, "wl2@example.com", "wl2")
        offering = ClassOfferingFactory(instructor=member, price_cents=4500, member_discount_pct=0, capacity=5)
        _waitlisted(offering, discount_code=DiscountCodeFactory(discount_pct=100))
        content = client.get(reverse("classes:teach_class_waitlist", args=[offering.pk])).content.decode()
        assert "gets a confirmation email" in content
        assert "No payment is required to hold the seat." not in content

    def it_warns_about_over_capacity_only_when_full(client):
        member = _login_instructor(client, "wl3@example.com", "wl3")
        offering = ClassOfferingFactory(instructor=member, capacity=1)
        _waitlisted(offering)
        url = reverse("classes:teach_class_waitlist", args=[offering.pk])
        assert "already full" not in client.get(url).content.decode()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        assert "already full" in client.get(url).content.decode()

    def it_carries_the_followup_url_on_the_row_and_on_the_promoted_stub(admin_user, client):
        # The modal opener reads data-followup-url off the row — both the live
        # waitlist row and the post-promote stub carry it, so the follow-up loads
        # whether the HX-Trigger event fires before or after the swap settles.
        client.force_login(admin_user)
        offering = ClassOfferingFactory(price_cents=4500, member_discount_pct=0, capacity=5)
        reg = _waitlisted(offering)
        followup_url = reverse("classes:registration_promote_followup", args=[reg.pk])
        page = client.get(reverse("classes:admin_class_waitlist", args=[offering.pk])).content.decode()
        assert f'data-followup-url="{followup_url}"' in page
        swap = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX).content.decode()
        assert f'data-followup-url="{followup_url}"' in swap

    def it_renders_the_same_actions_on_the_admin_waitlist_tab(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=5)
        _waitlisted(offering)
        content = client.get(reverse("classes:admin_class_waitlist", args=[offering.pk])).content.decode()
        assert "Add to Class" in content
        assert "promote-followup" in content


def describe_roster_tab_surfaces():
    def it_renders_action_buttons_on_the_admin_registrations_tab(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _unpaid(offering)
        content = client.get(reverse("classes:admin_class_registrations", args=[offering.pk])).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert ">Send Payment Link</button>" in menu
        assert ">Mark as Paid</button>" in menu
        assert ">Remove Student</button>" in menu  # renamed from the bare "Remove" button

    def it_carries_the_refund_action_on_the_admin_registrations_tab(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=5000,
            stripe_payment_id="pi_admin_tab",
        )
        content = client.get(reverse("classes:admin_class_registrations", args=[offering.pk])).content.decode()
        assert reverse("classes:admin_registration_refund_form", args=[reg.pk]) in content
        assert "refund-modal" in content

    def it_hides_the_refund_action_from_an_instructor_without_the_grant(client, menu_region):
        member = _login_instructor(client, "norefund@example.com", "norefund")
        offering = ClassOfferingFactory(instructor=member)
        reg = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=5000,
            stripe_payment_id="pi_no_grant",
        )
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        # "Retry Refund" scoped to the row menu — bare-phrase page negatives are
        # exposed to unrelated content (the changelog-renders-everywhere gotcha).
        assert ">Retry Refund</button>" not in menu
        assert ">Refund</button>" not in menu
        assert 'hx-get="/classes/admin/registrations/' not in content  # no refund-form loads without the grant

    def it_serves_the_admin_table_partial_for_the_refund_refresh(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _unpaid(offering)
        content = client.get(reverse("classes:admin_class_registrations_table", args=[offering.pk])).content.decode()
        assert f'id="reg-row-{reg.pk}"' in content

    def it_shows_the_paid_header_help_bubble(client):
        member = _login_instructor(client, "help@example.com", "help")
        offering = ClassOfferingFactory(instructor=member)
        _unpaid(offering)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "pl-help__bubble" in content
        assert "Their seat is held either way." in content

    def it_renders_the_unpaid_badge_and_the_paid_badge(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        _unpaid(offering, email="u@example.com")
        RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            payment_due_cents=4500,
            amount_paid_cents=4500,
            email="p@example.com",
        )
        content = client.get(reverse("classes:admin_class_registrations", args=[offering.pk])).content.decode()
        assert "Unpaid · $45.00" in content
        assert "Paid $45.00" in content


def describe_registration_detail_payment_actions():
    def it_renders_the_balance_row_and_action_buttons_for_an_unpaid_row(admin_user, client):
        client.force_login(admin_user)
        reg = _unpaid(ClassOfferingFactory())
        content = client.get(reverse("classes:admin_registration_detail", args=[reg.pk])).content.decode()
        assert "Owes $45.00 · Unpaid" in content
        assert "Send Payment Link" in content
        assert "Mark as Paid" in content

    def it_shows_paid_in_full_once_settled(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=4500)
        content = client.get(reverse("classes:admin_registration_detail", args=[reg.pk])).content.decode()
        assert "Paid in full" in content
        assert "Send Payment Link" not in content

    def it_redirects_a_non_htmx_error_with_a_message(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)  # nothing owed
        response = client.post(reverse("classes:registration_send_payment_link", args=[reg.pk]), follow=True)
        assert "no outstanding balance" in response.content.decode()
        response = client.post(reverse("classes:registration_mark_paid", args=[reg.pk]), follow=True)
        assert "no outstanding balance" in response.content.decode()


def describe_remaining_error_branches():
    def it_toasts_a_send_error_from_the_followup_modal(admin_user, client, mailoutbox):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED)  # nothing owed
        response = client.post(
            reverse("classes:registration_promote_notify", args=[reg.pk]), {"choice": "send"}, headers=HTMX
        )
        assert response.status_code == 204
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"
        assert len(mailoutbox) == 0

    def it_toasts_a_remove_error_on_an_already_cancelled_row(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CANCELLED)
        response = client.post(reverse("classes:registration_remove", args=[reg.pk]), {"row": "reg"}, headers=HTMX)
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"

    def it_toasts_a_mark_paid_error_on_a_settled_row(admin_user, client):
        client.force_login(admin_user)
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=4500)
        response = client.post(reverse("classes:registration_mark_paid", args=[reg.pk]), {"row": "reg"}, headers=HTMX)
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"


def describe_admin_registrations_instructor_filter():
    def it_filters_by_instructor(admin_user, client):
        client.force_login(admin_user)
        teacher = InstructorFactory(instructor_slug="filter-a")
        RegistrationFactory(class_offering=ClassOfferingFactory(instructor=teacher), email="mine@example.com")
        RegistrationFactory(email="other@example.com")
        content = client.get(reverse("classes:admin_registrations"), {"instructor": teacher.pk}).content.decode()
        assert "mine@example.com" in content
        assert "other@example.com" not in content

    def it_styles_the_filter_controls_with_the_shared_theme_class(admin_user, client):
        # Rule 13: no inline background/color on controls — the filter bar's
        # selects share one pl- class with theme tokens.
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registrations")).content.decode()
        assert content.count('class="pl-admin-control"') >= 3  # status, class, instructor

    def it_ignores_a_bogus_instructor_value(admin_user, client):
        client.force_login(admin_user)
        RegistrationFactory(email="anyone@example.com")
        content = client.get(reverse("classes:admin_registrations"), {"instructor": "bogus"}).content.decode()
        assert "anyone@example.com" in content

    def it_renders_my_classes_for_an_admin_with_an_instructor_profile(admin_user, client):
        member = admin_user.member
        member.instructor_slug = "admin-teaches"
        member.save(update_fields=["instructor_slug"])
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registrations")).content.decode()
        assert "My Classes" in content

    def it_still_shows_my_classes_for_an_admin_with_no_instructor_profile(admin_user, client):
        # The old Mine Only toggle was gated on instructor_slug — that gate was the
        # bug. My Classes is always visible now.
        assert not admin_user.member.instructor_slug
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registrations")).content.decode()
        assert "My Classes" in content

    def it_preserves_other_get_params_on_the_toggle(admin_user, client):
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registrations"), {"status": "confirmed"}).content.decode()
        # The My Classes toggle keeps the active status filter and turns mine on.
        assert "status=confirmed&amp;mine=1" in content

    def it_applies_the_filter_to_the_csv_export(admin_user, client):
        client.force_login(admin_user)
        teacher = InstructorFactory(instructor_slug="filter-csv")
        RegistrationFactory(class_offering=ClassOfferingFactory(instructor=teacher), email="csv-mine@example.com")
        RegistrationFactory(email="csv-other@example.com")
        response = client.get(reverse("classes:admin_registrations_export"), {"instructor": teacher.pk})
        body = b"".join(response.streaming_content).decode()
        assert "csv-mine@example.com" in body
        assert "csv-other@example.com" not in body

    def it_never_shows_the_filter_ui_to_a_non_admin_instructor(client):
        member = _login_instructor(client, "noadmin@example.com", "noadmin")
        RegistrationFactory(class_offering=ClassOfferingFactory(instructor=member), email="scoped@example.com")
        RegistrationFactory(email="unscoped@example.com")
        content = client.get(reverse("classes:admin_registrations")).content.decode()
        assert 'name="instructor"' not in content
        # The instructor <select> stays admin-only, but My Classes is shown to
        # everyone who reaches the page (the non-admin instructor can narrow too).
        assert "My Classes" in content
        assert "scoped@example.com" in content
        assert "unscoped@example.com" not in content
