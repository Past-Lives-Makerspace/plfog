"""BDD specs for the demo_data management command."""

from __future__ import annotations

import io
from datetime import time

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from classes.models import ClassApproval, ClassOffering, Registration, RegistrationQuestion
from core.management.commands.demo_data import (
    DEMO_EMAIL_DOMAIN,
    DEMO_IMAGE_DIRNAME,
    DEMO_ORIENTATION_GUILDS,
    DEMO_ORIENTATION_MARKER,
    DEMO_SLUG_PREFIX,
    GUEST_ORDER_NUMBER,
    PERSONA_INSTRUCTOR_EMAIL,
    PERSONA_MEMBER_EMAIL,
    PERSONA_STUDENT_EMAIL,
)

pytestmark = pytest.mark.django_db


def _seed_demo_guilds():
    """Create the three guilds the orientation seed targets, each with a lead."""
    from tests.membership.factories import GuildFactory, MemberFactory

    return [GuildFactory(name=name, guild_lead=MemberFactory()) for name in DEMO_ORIENTATION_GUILDS]


def _write_demo_image(path) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 120, 40)).save(buf, "JPEG")
    path.write_bytes(buf.getvalue())


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


def describe_demo_data_local_dev_personas():
    def it_creates_a_pending_approval_class_and_a_member_persona(settings):
        settings.DEBUG = True
        call_command("demo_data")

        pending = ClassOffering.objects.get(slug=f"{DEMO_SLUG_PREFIX}pending-review")
        assert pending.status == ClassOffering.Status.PENDING
        # Exactly one open admin gate so it shows in the review queue.
        assert pending.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").count() == 1
        # The test member persona is a real, active, logged-in-capable Member.
        assert get_user_model().objects.filter(email=PERSONA_MEMBER_EMAIL).exists()


def describe_demo_data_orientations():
    def it_enables_orientations_and_seeds_bookings_in_debug(settings):
        settings.DEBUG = True
        from membership.models import (
            GuildOrientationSettings,
            OrientationAvailability,
            OrientationBooking,
            OrientationSlot,
        )

        guilds = _seed_demo_guilds()
        # One guild already has a (blank) settings row — exercises the "fill blanks
        # on an existing row" path rather than create-with-defaults.
        GuildOrientationSettings.objects.create(guild=guilds[2], is_enabled=False)

        call_command("demo_data")

        assert GuildOrientationSettings.objects.filter(
            guild__name__in=DEMO_ORIENTATION_GUILDS, is_enabled=True
        ).count() == len(DEMO_ORIENTATION_GUILDS)
        assert OrientationAvailability.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).exists()
        assert OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).exists()
        bookings = OrientationBooking.objects.filter(member__user__email__endswith=f"@{DEMO_EMAIL_DOMAIN}")
        assert bookings.filter(status=OrientationBooking.Status.REQUESTED).exists()
        assert bookings.filter(status=OrientationBooking.Status.CONFIRMED).exists()
        assert bookings.filter(is_completed=True).exists()

    def it_is_idempotent_for_orientation_data(settings):
        settings.DEBUG = True
        from membership.models import OrientationBooking, OrientationSlot

        _seed_demo_guilds()
        call_command("demo_data")
        slots = OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).count()
        bookings = OrientationBooking.objects.count()

        call_command("demo_data")
        assert OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).count() == slots
        assert OrientationBooking.objects.count() == bookings

    def it_reports_orientations_in_seed_summary_and_status(settings):
        settings.DEBUG = True
        _seed_demo_guilds()

        seed_out = io.StringIO()
        call_command("demo_data", stdout=seed_out)
        assert "Orientations" in seed_out.getvalue()

        status_out = io.StringIO()
        call_command("demo_data", "--status", stdout=status_out)
        assert "enabled on" in status_out.getvalue()


def describe_demo_data_remove_orientations():
    def it_clears_demo_orientation_data_and_disables_emptied_guilds(settings):
        settings.DEBUG = True
        from membership.models import GuildOrientationSettings, OrientationAvailability, OrientationSlot

        _seed_demo_guilds()
        call_command("demo_data")
        call_command("demo_data", "--remove")

        assert not OrientationAvailability.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).exists()
        assert not OrientationSlot.objects.filter(location__icontains=DEMO_ORIENTATION_MARKER).exists()
        # Guilds we enabled but left empty are switched back off.
        assert not GuildOrientationSettings.objects.filter(is_enabled=True).exists()

    def it_spares_a_guild_that_has_its_own_orientation_data(settings):
        settings.DEBUG = True
        from membership.models import GuildOrientationSettings, OrientationAvailability

        guilds = _seed_demo_guilds()
        call_command("demo_data")
        # A non-demo recurring rule (no marker) — this guild owns real orientation data.
        OrientationAvailability.objects.create(
            guild=guilds[0],
            weekday=OrientationAvailability.Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            location="Real Studio",
        )

        call_command("demo_data", "--remove")
        assert GuildOrientationSettings.objects.get(guild=guilds[0]).is_enabled is True


def describe_demo_data_class_images():
    def it_attaches_hero_and_gallery_images_when_present(settings, tmp_path):
        settings.DEBUG = True
        settings.MEDIA_ROOT = str(tmp_path)
        seed_dir = tmp_path / DEMO_IMAGE_DIRNAME
        seed_dir.mkdir()
        # gallery_2 is intentionally absent so the "source file missing" branch runs.
        for name in [
            "hero_intro.jpg",
            "hero_advanced.jpg",
            "hero_pending.jpg",
            "gallery_1.jpg",
            "gallery_3.jpg",
            "gallery_4.jpg",
        ]:
            _write_demo_image(seed_dir / name)

        call_command("demo_data")

        free = ClassOffering.objects.get(slug=f"{DEMO_SLUG_PREFIX}free-intro")
        assert free.image
        assert free.gallery_images.count() == 3  # 4 requested, gallery_2 missing
        pending = ClassOffering.objects.get(slug=f"{DEMO_SLUG_PREFIX}pending-review")
        assert pending.image
        assert pending.gallery_images.count() == 2
