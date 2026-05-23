"""BDD specs for the demo_data management command."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from classes.models import ClassOffering, Instructor, Registration
from core.management.commands.demo_data import (
    DEMO_EMAIL_DOMAIN,
    DEMO_SLUG_PREFIX,
    GUEST_ORDER_NUMBER,
    PERSONA_INSTRUCTOR_EMAIL,
    PERSONA_STUDENT_EMAIL,
)

pytestmark = pytest.mark.django_db


def describe_demo_data_seed():
    def it_creates_student_instructor_and_guest_registrations():
        call_command("demo_data")

        User = get_user_model()
        assert User.objects.filter(email=PERSONA_STUDENT_EMAIL).exists()
        assert User.objects.filter(email=PERSONA_INSTRUCTOR_EMAIL).exists()
        # Guest is registration-only, no user
        assert not User.objects.filter(email__startswith="guest@").exists()
        assert Registration.objects.filter(order_number=GUEST_ORDER_NUMBER, last_name="Guest").exists()

    def it_creates_three_demo_classes_with_sessions():
        call_command("demo_data")

        demo_classes = ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX)
        assert demo_classes.count() == 3
        # Every demo class has at least one session so the public list shows them.
        for c in demo_classes:
            assert c.sessions.count() >= 1

    def it_does_not_inflate_member_count():
        from membership.models import Member

        call_command("demo_data")

        assert Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0

    def it_is_idempotent_across_runs():
        call_command("demo_data")
        first_user_count = get_user_model().objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        first_reg_count = Registration.objects.filter(class_offering__slug__startswith=DEMO_SLUG_PREFIX).count()

        call_command("demo_data")
        assert get_user_model().objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == first_user_count
        assert Registration.objects.filter(class_offering__slug__startswith=DEMO_SLUG_PREFIX).count() == first_reg_count


def describe_demo_data_remove():
    def it_removes_every_seeded_object():
        from membership.models import Member

        User = get_user_model()
        call_command("demo_data")

        call_command("demo_data", "--remove")

        assert User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0
        assert ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX).count() == 0
        assert Instructor.objects.filter(slug__startswith=DEMO_SLUG_PREFIX).count() == 0
        assert Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0
        assert Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0

    def it_is_safe_to_run_on_an_empty_database():
        # No prior seed — remove should no-op cleanly.
        call_command("demo_data", "--remove")
