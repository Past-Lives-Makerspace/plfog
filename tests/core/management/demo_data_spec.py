"""BDD specs for the demo_data management command."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings

from classes.models import ClassOffering, Registration, RegistrationQuestion
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

        # The demo instructor is now a Member (instructor == Member in the new model).
        # The student and guest personas must never create Members — only the 1 instructor.
        assert Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 1

    def it_is_idempotent_across_runs():
        call_command("demo_data")
        first_user_count = get_user_model().objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        first_reg_count = Registration.objects.filter(class_offering__slug__startswith=DEMO_SLUG_PREFIX).count()

        call_command("demo_data")
        assert get_user_model().objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == first_user_count
        assert Registration.objects.filter(class_offering__slug__startswith=DEMO_SLUG_PREFIX).count() == first_reg_count


def describe_demo_data_registration_questions():
    @override_settings(DEBUG=True)
    def it_seeds_questions_only_in_debug():
        call_command("demo_data")

        # The three demo questions are global; they only belong on a dev DB.
        assert RegistrationQuestion.objects.filter(is_active=True).count() >= 3

    @override_settings(DEBUG=False)
    def it_does_not_seed_questions_on_a_real_environment():
        call_command("demo_data")

        assert not RegistrationQuestion.objects.exists()


def describe_demo_data_remove():
    def it_removes_every_seeded_object():
        from membership.models import Member

        User = get_user_model()
        call_command("demo_data")

        call_command("demo_data", "--remove")

        assert User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0
        assert ClassOffering.objects.filter(slug__startswith=DEMO_SLUG_PREFIX).count() == 0
        assert Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0
        assert Member.objects.filter(user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == 0

    def it_is_safe_to_run_on_an_empty_database():
        # No prior seed — remove should no-op cleanly.
        call_command("demo_data", "--remove")


def describe_demo_data_seed_email_repair():
    def it_repairs_blank_email_on_existing_user():
        """The _ensure_user branch `if not user.email: user.email = email` is hit
        when a user row already exists but its email column was cleared externally."""
        User = get_user_model()
        # Create the user manually with a blank email so get_or_create finds it.
        user = User.objects.create_user(username=PERSONA_STUDENT_EMAIL, email="", password="x")
        assert user.email == ""

        # Seeding should repair the email.
        call_command("demo_data")

        user.refresh_from_db()
        assert user.email == PERSONA_STUDENT_EMAIL


def describe_demo_data_status():
    def it_prints_counts_without_modifying_data():
        import io

        from django.core.management import call_command as cc

        # Seed first so there's something to report.
        cc("demo_data")

        User = get_user_model()
        user_count_before = User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()
        reg_count_before = Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count()

        out = io.StringIO()
        cc("demo_data", "--status", stdout=out)

        # No objects were created or destroyed.
        assert User.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == user_count_before
        assert Registration.objects.filter(email__endswith=f"@{DEMO_EMAIL_DOMAIN}").count() == reg_count_before
        # Output contains summary labels.
        output = out.getvalue()
        assert "Users:" in output
        assert "Classes:" in output
        assert "Registrations:" in output

    def it_lists_each_seeded_user_email_in_status_output():
        import io

        from django.core.management import call_command as cc

        cc("demo_data")
        out = io.StringIO()
        cc("demo_data", "--status", stdout=out)
        output = out.getvalue()
        assert PERSONA_STUDENT_EMAIL in output
        assert PERSONA_INSTRUCTOR_EMAIL in output

    def it_prints_status_even_when_no_data_seeded():
        import io

        from django.core.management import call_command as cc

        out = io.StringIO()
        cc("demo_data", "--status", stdout=out)
        output = out.getvalue()
        assert "Users:" in output
