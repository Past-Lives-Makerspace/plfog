"""BDD specs for registration notification emails (instructor + admin)."""

from __future__ import annotations

from django.core import mail

from classes.emails import emit_instructor_new_registration, send_admin_registration_notification
from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering
from core.events.registry import Channel
from core.events.scheduler import flush_digests
from core.models import EventDelivery, Notification, NotificationPreference, TransactionalEmailLog
from membership.models import Member
from tests.core.events.digest_support import next_8am


def describe_emit_instructor_new_registration():
    def it_emails_the_instructor_with_registration_details(db):
        user = UserFactory(email="teach@example.com")
        instructor = InstructorFactory(user=user, full_legal_name="Ms. Paint")
        offering = ClassOfferingFactory(
            instructor=instructor, title="Watercolors 101", capacity=8, status=ClassOffering.Status.PUBLISHED
        )
        registration = RegistrationFactory(
            class_offering=offering,
            first_name="Sam",
            last_name="Park",
            email="sam@example.com",
            amount_paid_cents=5000,
            status="confirmed",
        )

        emit_instructor_new_registration(registration)

        # Exactly one notice email — no second generic email from a separate dispatch.
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert msg.to == ["teach@example.com"]
        assert "Sam Park" in msg.subject
        assert "Watercolors 101" in msg.subject
        assert "$50.00" in msg.body
        assert "1/8" in msg.body
        # And exactly one in-app row for the instructor.
        assert Notification.objects.filter(trigger="instructor_new_registration", user=user).count() == 1

    def it_skips_when_instructor_has_no_email(db):
        user = UserFactory(email="")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
        registration = RegistrationFactory(class_offering=offering)

        emit_instructor_new_registration(registration)

        assert len(mail.outbox) == 0

    def it_reaches_every_instructor_once(db):
        # #686: one email at each instructor's own address and one bell row each, the payee no different.
        first_user = UserFactory(email="alex@example.com")
        second_user = UserFactory(email="jenelle@example.com")
        first = InstructorFactory(user=first_user, full_legal_name="Alex Eason")
        second = InstructorFactory(user=second_user, full_legal_name="Jenelle Giordano")
        offering = ClassOfferingFactory(
            instructor=first, instructors=[first, second], status=ClassOffering.Status.PUBLISHED
        )
        registration = RegistrationFactory(
            class_offering=offering, first_name="Sam", last_name="Park", status="confirmed"
        )

        emit_instructor_new_registration(registration)

        assert sorted(addr for m in mail.outbox for addr in m.to) == ["alex@example.com", "jenelle@example.com"]
        assert len(mail.outbox) == 2
        for user in (first_user, second_user):
            assert Notification.objects.filter(trigger="instructor_new_registration", user=user).count() == 1

    def it_still_reaches_the_rest_when_one_instructor_has_no_email(db):
        first = InstructorFactory(user=UserFactory(email="alex@example.com"))
        second = InstructorFactory(user=UserFactory(email=""))
        offering = ClassOfferingFactory(
            instructor=first, instructors=[first, second], status=ClassOffering.Status.PUBLISHED
        )
        registration = RegistrationFactory(class_offering=offering)

        emit_instructor_new_registration(registration)

        assert [m.to for m in mail.outbox] == [["alex@example.com"]]


def _admin(email: str) -> Member:
    """An Admin with a login: the admin copy reaches only people who hold switches."""
    member = UserFactory(username=email, email=email).member  # type: ignore[attr-defined]
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    return member


def _admin_copies(address: str) -> list:
    """The instant admin email, which #894 part 4 retired: it should never be in the outbox."""
    return [m for m in mail.outbox if m.to == [address] and m.subject.startswith("[Classes] New registration:")]


def _morning(address: str) -> list:
    """Send the next 8 AM staff digest, then return every digest addressed to ``address``."""
    flush_digests(next_8am())
    return [m for m in mail.outbox if m.to == [address] and m.subject.endswith("Since the Last Email")]


def describe_send_admin_registration_notification():
    def it_sends_no_email_at_once(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin1@example.com")

        send_admin_registration_notification(RegistrationFactory())

        assert mail.outbox == []

    def it_gives_every_admin_one_morning_line(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin1@example.com")
        _admin("admin2@example.com")
        offering = ClassOfferingFactory(title="Pottery Basics", capacity=6, status=ClassOffering.Status.PUBLISHED)
        registration = RegistrationFactory(
            class_offering=offering,
            first_name="Alex",
            last_name="Doe",
            email="alex@example.com",
            amount_paid_cents=8000,
            status="confirmed",
        )

        send_admin_registration_notification(registration)

        for address in ("admin1@example.com", "admin2@example.com"):
            (digest,) = _morning(address)
            assert digest.subject == "1 Update Since the Last Email"
            assert "NEW CLASS REGISTRATION" in digest.body
            assert "Alex Doe registered for Pottery Basics" in digest.body
            assert "Confirmed, paid $80.00. 1 of 6 seats taken. alex@example.com" in digest.body
            assert f"/classes/{offering.slug}/" in digest.body
            assert _admin_copies(address) == []

    def it_gives_two_registrations_two_lines(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        offering = ClassOfferingFactory(title="Pottery Basics", status=ClassOffering.Status.PUBLISHED)
        for first, last in (("Alex", "Doe"), ("Sam", "Park")):
            registration = RegistrationFactory(
                class_offering=offering, first_name=first, last_name=last, status="confirmed"
            )
            send_admin_registration_notification(registration)

        (digest,) = _morning("admin@example.com")

        assert digest.subject == "2 Updates Since the Last Email"
        assert "Alex Doe registered for Pottery Basics" in digest.body
        assert "Sam Park registered for Pottery Basics" in digest.body

    def it_gives_a_re_sent_registration_one_line(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        admin = _admin("admin@example.com")
        registration = RegistrationFactory(first_name="Alex", last_name="Doe", status="confirmed")

        send_admin_registration_notification(registration)
        send_admin_registration_notification(registration)

        assert (
            EventDelivery.objects.filter(
                event_key="class_registration_admin_notice",
                target_ref=f"user:{admin.user.pk}",
                channel=Channel.DIGEST.value,
            ).count()
            == 1
        )
        (digest,) = _morning("admin@example.com")
        assert digest.subject == "1 Update Since the Last Email"
        assert digest.body.count("Alex Doe registered for") == 1

    def it_gives_registrations_in_two_classes_two_lines(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        for title in ("Pottery Basics", "Intro to Forging"):
            offering = ClassOfferingFactory(title=title, status=ClassOffering.Status.PUBLISHED)
            registration = RegistrationFactory(
                class_offering=offering, first_name="Alex", last_name="Doe", status="confirmed"
            )
            send_admin_registration_notification(registration)

        (digest,) = _morning("admin@example.com")

        assert digest.subject == "2 Updates Since the Last Email"
        assert "Alex Doe registered for Pottery Basics" in digest.body
        assert "Alex Doe registered for Intro to Forging" in digest.body

    def it_writes_apostrophes_and_ampersands_plainly_in_the_text_part(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        offering = ClassOfferingFactory(
            title="Kids & Clay", slug="kids-and-clay", status=ClassOffering.Status.PUBLISHED
        )
        registration = RegistrationFactory(
            class_offering=offering, first_name="Sam", last_name="O'Brien", status="confirmed"
        )

        send_admin_registration_notification(registration)

        (digest,) = _morning("admin@example.com")
        assert "Sam O'Brien registered for Kids & Clay" in digest.body
        assert "&#x27;" not in digest.body
        assert "&amp;" not in digest.body
        html = digest.alternatives[0][0]
        assert "Sam O&#x27;Brien registered for Kids &amp; Clay" in html

    def it_is_its_own_digest_event_with_no_bell(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        admin = _admin("logadmin@example.com")

        registration = RegistrationFactory()
        send_admin_registration_notification(registration)

        assert not TransactionalEmailLog.objects.filter(trigger_kind="class_registration_admin_notice").exists()
        row = EventDelivery.objects.get(
            event_key="class_registration_admin_notice",
            target_ref=f"user:{admin.user.pk}",
            channel=Channel.DIGEST.value,
        )
        assert (row.status, row.period) == (
            EventDelivery.Status.PENDING,
            f"reg:{registration.pk}:admin_notice",
        )
        assert not Notification.objects.filter(user=admin.user).exists()

    def it_reaches_a_configured_address_only_when_it_has_an_account(db, settings):
        UserFactory(username="cfg@example.com", email="cfg@example.com")
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "cfg@example.com, nobody@example.com"

        send_admin_registration_notification(RegistrationFactory())
        flush_digests(next_8am())

        assert {addr for m in mail.outbox for addr in m.to} == {"cfg@example.com"}

    def describe_the_email_switch():
        def it_leaves_out_an_admin_who_switched_it_off(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            quiet = _admin("quietadmin@example.com")
            NotificationPreference.objects.create(
                user=quiet.user, event_key="class_registration_admin_notice", channel="email", enabled=False
            )
            _admin("loudadmin@example.com")

            send_admin_registration_notification(RegistrationFactory())

            assert _morning("quietadmin@example.com") == []
            assert len(_morning("loudadmin@example.com")) == 1

        def it_leaves_the_instructors_own_notice_alone(db, settings):
            # The instructor's notice is a different event with its own switch, and stays
            # instant: an Admin's copy waiting for the morning never delays the instructor.
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            quiet = _admin("quietadmin2@example.com")
            NotificationPreference.objects.create(
                user=quiet.user, event_key="class_registration_admin_notice", channel="email", enabled=False
            )
            instructor = InstructorFactory(user=UserFactory(email="teach2@example.com"))
            offering = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.PUBLISHED)
            registration = RegistrationFactory(class_offering=offering, status="confirmed")

            emit_instructor_new_registration(registration)
            send_admin_registration_notification(registration)

            assert [m.to for m in mail.outbox] == [["teach2@example.com"]]

    def it_counts_capacity_the_way_every_other_surface_does(db, settings):
        """The admin line is written one line after the instructor's email, about the same signup.

        Counting every row ever written had the two disagree by four in the same instant.
        """
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        offering = ClassOfferingFactory(capacity=6, status=ClassOffering.Status.PUBLISHED)
        for _ in range(4):
            RegistrationFactory(class_offering=offering, status="cancelled")
        newest = RegistrationFactory(class_offering=offering, status="confirmed")

        send_admin_registration_notification(newest)

        body = "\n".join(m.body for m in _morning("admin@example.com"))
        assert "1 of 6 seats taken." in body
        assert "5 of 6" not in body

    def it_agrees_with_the_instructor_email_about_the_same_signup(db, settings):
        """Both are written from the same two lines (classes/views.py and webhook_handlers.py).

        The admin half counted every row ever written, so on a class with four cancelled
        signups the instructor was told 1/6 and the admins 5/6 about one registration.
        """
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        user = UserFactory(email="teach@example.com")
        instructor = InstructorFactory(user=user)
        offering = ClassOfferingFactory(instructor=instructor, capacity=6, status=ClassOffering.Status.PUBLISHED)
        for _ in range(4):
            RegistrationFactory(class_offering=offering, status="cancelled")
        for _ in range(2):
            RegistrationFactory(class_offering=offering, status="waitlisted")
        newest = RegistrationFactory(class_offering=offering, status="confirmed")

        emit_instructor_new_registration(newest)
        send_admin_registration_notification(newest)

        (instructor_email,) = [m for m in mail.outbox if m.to == ["teach@example.com"]]
        assert "1/6" in instructor_email.body
        (digest,) = _morning("admin@example.com")
        assert "1 of 6 seats taken." in digest.body

    def it_does_not_count_the_waitlist_against_capacity(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("admin@example.com")
        offering = ClassOfferingFactory(capacity=9, status=ClassOffering.Status.PUBLISHED)
        for _ in range(2):
            RegistrationFactory(class_offering=offering, status="confirmed")
        for _ in range(5):
            RegistrationFactory(class_offering=offering, status="waitlisted")
        newest = RegistrationFactory(class_offering=offering, status="pending")

        send_admin_registration_notification(newest)

        body = "\n".join(m.body for m in _morning("admin@example.com"))
        assert "3 of 9 seats taken." in body
        assert "8 of 9" not in body

    def it_sends_nothing_when_there_is_no_admin(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        registration = RegistrationFactory()

        send_admin_registration_notification(registration)
        flush_digests(next_8am())

        assert len(mail.outbox) == 0

    def it_sends_nothing_when_the_setting_is_only_whitespace(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = "  ,  , "
        registration = RegistrationFactory()

        send_admin_registration_notification(registration)
        flush_digests(next_8am())

        assert len(mail.outbox) == 0
