"""BDD specs for the roster/waitlist kebab actions menu (components/row_actions.html).

Every assertion is scoped to a single row's menu markup via the ``menu_region``
helper (conftest.py) so it cannot be fooled by the Paid-column help bubble, the
confirm modals, or "Remove" appearing as a substring of "Remove Student". All
rendering goes through the real teach + admin views, which also proves the two
row partials are shared across all four tabs.
"""

from __future__ import annotations

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from billing.models import PaymentRefund
from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import Registration
from tests.billing.factories import PaymentRefundFactory

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


def _login_instructor(client, username: str, slug: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug=slug)
    client.force_login(user)
    return member


def _paid(offering, **kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.CONFIRMED)
    kwargs.setdefault("payment_due_cents", 5000)
    kwargs.setdefault("amount_paid_cents", 5000)
    return RegistrationFactory(class_offering=offering, **kwargs)


def _unpaid(offering, **kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.CONFIRMED)
    kwargs.setdefault("payment_due_cents", 4500)
    kwargs.setdefault("amount_paid_cents", 0)
    return RegistrationFactory(class_offering=offering, **kwargs)


def _admin_reg_url(offering) -> str:
    return reverse("classes:admin_class_registrations", args=[offering.pk])


def describe_registration_row_menu():
    def it_always_offers_view_details_and_email_student(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        paid = _paid(offering, email="paid@example.com")
        cancelled = RegistrationFactory(
            class_offering=offering, status=Registration.Status.CANCELLED, amount_paid_cents=0, email="cx@example.com"
        )
        content = client.get(_admin_reg_url(offering)).content.decode()

        paid_menu = menu_region(content, f"reg-row-{paid.pk}")
        assert f'href="{reverse("classes:admin_registration_detail", args=[paid.pk])}"' in paid_menu
        assert 'href="mailto:paid@example.com"' in paid_menu
        assert ">View Details</a>" in paid_menu
        assert ">Email Student</a>" in paid_menu

        # A cancelled row (reduced opacity) still keeps the two always-on items.
        cx_menu = menu_region(content, f"reg-row-{cancelled.pk}")
        assert f'href="{reverse("classes:admin_registration_detail", args=[cancelled.pk])}"' in cx_menu
        assert 'href="mailto:cx@example.com"' in cx_menu

    def it_shows_payment_actions_only_for_an_unpaid_managed_row(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        unpaid = _unpaid(offering, email="owes@example.com")
        paid = _paid(offering, email="settled@example.com")
        content = client.get(_admin_reg_url(offering)).content.decode()

        unpaid_menu = menu_region(content, f"reg-row-{unpaid.pk}")
        assert ">Send Payment Link</button>" in unpaid_menu
        assert ">Mark as Paid</button>" in unpaid_menu

        paid_menu = menu_region(content, f"reg-row-{paid.pk}")
        assert ">Send Payment Link</button>" not in paid_menu
        assert ">Mark as Paid</button>" not in paid_menu

    def it_shows_refund_only_with_authority_and_a_refundable_balance(admin_user, client, menu_region):
        client.force_login(admin_user)  # admin carries refund authority
        offering = ClassOfferingFactory()
        paid = _paid(offering)  # refundable_cents = 5000
        unpaid = _unpaid(offering)  # refundable_cents = 0
        content = client.get(_admin_reg_url(offering)).content.decode()

        assert ">Refund</button>" in menu_region(content, f"reg-row-{paid.pk}")
        assert ">Refund</button>" not in menu_region(content, f"reg-row-{unpaid.pk}")

    def it_hides_refund_from_a_viewer_without_authority(client, menu_region):
        member = _login_instructor(client, "menu-norefund@example.com", "menu-norefund")
        offering = ClassOfferingFactory(instructor=member)
        paid = _paid(offering)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        menu = menu_region(content, f"reg-row-{paid.pk}")
        assert ">Refund</button>" not in menu
        assert ">Retry Refund</button>" not in menu
        assert ">View Details</a>" in menu  # the always-on items still render

    def it_swaps_refund_for_retry_when_the_last_attempt_failed(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _paid(offering, stripe_payment_id="pi_menu_failed")
        PaymentRefundFactory(registration=reg, amount_cents=5000, status=PaymentRefund.Status.FAILED)
        content = client.get(_admin_reg_url(offering)).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert ">Retry Refund</button>" in menu
        assert ">Refund</button>" not in menu

    def it_labels_remove_as_remove_student_and_gates_it_by_status(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        confirmed = _paid(offering, email="here@example.com")
        cancelled = RegistrationFactory(
            class_offering=offering, status=Registration.Status.CANCELLED, amount_paid_cents=0, email="gone@example.com"
        )
        content = client.get(_admin_reg_url(offering)).content.decode()

        confirmed_menu = menu_region(content, f"reg-row-{confirmed.pk}")
        assert ">Remove Student</button>" in confirmed_menu
        assert ">Remove</button>" not in confirmed_menu  # never the bare old label

        cancelled_menu = menu_region(content, f"reg-row-{cancelled.pk}")
        assert ">Remove Student</button>" not in cancelled_menu

    def it_preserves_the_htmx_contract_on_send_payment_link(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _unpaid(offering)
        content = client.get(_admin_reg_url(offering)).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert f'hx-post="{reverse("classes:registration_send_payment_link", args=[reg.pk])}"' in menu
        assert f'hx-target="#reg-row-{reg.pk}"' in menu
        assert 'hx-swap="outerHTML"' in menu

    def it_preserves_the_htmx_contract_on_refund(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _paid(offering)
        content = client.get(_admin_reg_url(offering)).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert f'hx-get="{reverse("classes:admin_registration_refund_form", args=[reg.pk])}"' in menu
        assert 'hx-target="#refund-modal-body"' in menu

    def describe_divider_logic():
        def it_renders_two_dividers_and_no_payment_group_for_a_paid_managed_row(admin_user, client, menu_region):
            client.force_login(admin_user)
            offering = ClassOfferingFactory()
            reg = _paid(offering)
            content = client.get(_admin_reg_url(offering)).content.decode()
            menu = menu_region(content, f"reg-row-{reg.pk}")
            # View/Email, a rule before Move Student, and one before the Refund/Remove group.
            assert menu.count("pl-row-menu__divider") == 2
            assert ">Send Payment Link</button>" not in menu

        def it_renders_three_dividers_for_a_partially_paid_refundable_row(admin_user, client, menu_region):
            client.force_login(admin_user)
            offering = ClassOfferingFactory()
            reg = RegistrationFactory(
                class_offering=offering,
                status=Registration.Status.CONFIRMED,
                payment_due_cents=4500,
                amount_paid_cents=2000,
                stripe_payment_id="pi_menu_partial",
            )
            content = client.get(_admin_reg_url(offering)).content.decode()
            menu = menu_region(content, f"reg-row-{reg.pk}")
            assert menu.count("pl-row-menu__divider") == 3
            assert ">Send Payment Link</button>" in menu
            assert ">Refund</button>" in menu
            assert ">Remove Student</button>" in menu

        def it_renders_only_the_move_rule_for_a_cancelled_row(admin_user, client, menu_region):
            client.force_login(admin_user)
            offering = ClassOfferingFactory()
            reg = RegistrationFactory(
                class_offering=offering,
                status=Registration.Status.CANCELLED,
                amount_paid_cents=0,
                email="only-two@example.com",
            )
            content = client.get(_admin_reg_url(offering)).content.decode()
            menu = menu_region(content, f"reg-row-{reg.pk}")
            # View/Email plus the mover's Move Student group — no payment, refund, or remove rules.
            assert menu.count("pl-row-menu__divider") == 1
            assert ">Move Student</button>" in menu
            assert ">Remove Student</button>" not in menu

    def it_limits_the_menu_to_view_email_and_refund_for_refund_only_authority(db):
        # can_manage is hardcoded True on all four roster views, so this state is
        # only reachable by rendering the row menu directly. It documents the
        # component gate: refund authority WITHOUT manage rights shows exactly
        # View Details, Email Student, and Refund — no payment actions, no Remove.
        reg = _paid(ClassOfferingFactory(), email="r@example.com")
        html = render_to_string(
            "classes/partials/registration_row_menu.html",
            {"reg": reg, "can_manage": False, "viewer_has_refund_authority": True},
        )
        assert ">View Details</a>" in html
        assert ">Email Student</a>" in html
        assert ">Refund</button>" in html
        assert ">Send Payment Link</button>" not in html
        assert ">Mark as Paid</button>" not in html
        assert ">Remove Student</button>" not in html


def describe_waitlist_row_menu():
    def it_orders_add_to_class_first_then_view_email_then_remove(client, menu_region):
        member = _login_instructor(client, "wlmenu@example.com", "wlmenu")
        offering = ClassOfferingFactory(instructor=member, capacity=5)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED, email="w@example.com")
        content = client.get(reverse("classes:teach_class_waitlist", args=[offering.pk])).content.decode()
        menu = menu_region(content, f"wl-row-{reg.pk}")

        assert ">Add to Class</button>" in menu
        assert ">View Details</a>" in menu
        assert ">Email Student</a>" in menu
        assert ">Remove from Waitlist</button>" in menu
        # Order: Add to Class is the primary first item; Remove is last.
        assert menu.index("Add to Class") < menu.index("View Details") < menu.index("Remove from Waitlist")
        # Remove from Waitlist is the danger item; Add to Class is not.
        remove_btn = menu.rfind("<button", 0, menu.index("Remove from Waitlist"))
        assert "pl-row-menu__item--danger" in menu[remove_btn : menu.index("Remove from Waitlist")]
        add_btn = menu.find("<button")
        assert "pl-row-menu__item--danger" not in menu[add_btn : menu.index("Add to Class")]

    def it_renders_no_kebab_on_a_promoted_stub_row(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(price_cents=0, member_discount_pct=0, capacity=5)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        swap = client.post(reverse("classes:registration_promote", args=[reg.pk]), headers=HTMX).content.decode()
        assert f'id="wl-row-{reg.pk}"' in swap
        assert "Added to class ✓" in swap
        assert "pl-row-menu" not in swap  # the stub keeps an empty actions cell

    def it_renders_no_kebab_on_a_removed_waitlist_stub(admin_user, client):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=5)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        swap = client.post(
            reverse("classes:registration_remove", args=[reg.pk]), {"row": "wl"}, headers=HTMX
        ).content.decode()
        assert f'id="wl-row-{reg.pk}"' in swap
        assert "Removed from the waitlist." in swap
        assert "pl-row-menu" not in swap


def describe_component_accessibility():
    def it_names_the_trigger_and_marks_menu_and_item_roles(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _paid(offering, first_name="Jane", last_name="Doe")
        content = client.get(_admin_reg_url(offering)).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert 'aria-haspopup="menu"' in menu
        assert 'aria-label="Actions for Jane Doe"' in menu
        assert 'role="menu"' in menu
        assert menu.count('role="menuitem"') >= 2  # at least View Details + Email Student
        assert "pl-row-menu__item--danger" in menu  # Refund + Remove render for admin on a paid row


def describe_admin_surface():
    def it_renders_the_kebab_on_the_admin_registrations_tab(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        reg = _unpaid(offering)
        content = client.get(_admin_reg_url(offering)).content.decode()
        menu = menu_region(content, f"reg-row-{reg.pk}")
        assert "pl-row-menu__trigger" in menu
        assert ">Send Payment Link</button>" in menu

    def it_renders_the_kebab_on_the_teach_registrations_tab(client, menu_region):
        member = _login_instructor(client, "teachkebab@example.com", "teachkebab")
        offering = ClassOfferingFactory(instructor=member)
        reg = _unpaid(offering)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "pl-row-menu__trigger" in menu_region(content, f"reg-row-{reg.pk}")

    def it_renders_the_kebab_on_the_admin_waitlist_tab(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = ClassOfferingFactory(capacity=5)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        content = client.get(reverse("classes:admin_class_waitlist", args=[offering.pk])).content.decode()
        menu = menu_region(content, f"wl-row-{reg.pk}")
        assert "pl-row-menu__trigger" in menu
        assert ">Add to Class</button>" in menu
