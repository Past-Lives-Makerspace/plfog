"""Specs for Plan 3 scheduled tasks: reminder emails + calendar sync."""

from __future__ import annotations

from datetime import timedelta

from django.core import mail
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, ClassSettings, Registration
from classes.tasks import send_due_class_reminders


def describe_send_due_class_reminders():
    def it_emails_confirmed_registrants_in_the_window(db, settings):
        settings.DEFAULT_FROM_EMAIL = "noreply@pastlives.space"
        cfg = ClassSettings.load()
        cfg.reminder_hours_before = 24
        cfg.save()
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(hours=24, minutes=1)
        session = ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        registration = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        sent = send_due_class_reminders(window_minutes=30)
        assert sent == 1
        assert len(mail.outbox) == 1
        assert registration.email in mail.outbox[0].to
        # Dedupe now lives on EventDelivery (§2.5) keyed by the per-(registration,
        # session) period bucket — RegistrationReminder is gone.
        from core.events.registry import Channel
        from core.models import EventDelivery

        assert EventDelivery.objects.filter(
            event_key="class_reminder",
            period=f"reg:{registration.pk}:reminder:{session.pk}",
            channel=Channel.EMAIL.value,
        ).exists()

    def it_skips_pending_or_cancelled_registrations(db, settings):
        settings.DEFAULT_FROM_EMAIL = "noreply@pastlives.space"
        cfg = ClassSettings.load()
        cfg.reminder_hours_before = 24
        cfg.save()
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(hours=24, minutes=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        RegistrationFactory(class_offering=offering, status=Registration.Status.PENDING)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        sent = send_due_class_reminders(window_minutes=30)
        assert sent == 0
        assert mail.outbox == []

    def it_does_not_resend_reminders(db, settings):
        settings.DEFAULT_FROM_EMAIL = "noreply@pastlives.space"
        cfg = ClassSettings.load()
        cfg.reminder_hours_before = 24
        cfg.save()
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(hours=24, minutes=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        send_due_class_reminders(window_minutes=30)
        mail.outbox.clear()
        again = send_due_class_reminders(window_minutes=30)
        assert again == 0
        assert mail.outbox == []

    def it_ignores_sessions_outside_the_window(db):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(days=7)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        sent = send_due_class_reminders(window_minutes=15)
        assert sent == 0

    def it_sends_a_single_email_even_when_the_member_opted_into_reminder_email(db, settings):
        # Regression: the dedicated reminder email plus dispatch's generic email
        # used to double-send to a registrant opted into class_reminder email.
        from django.contrib.auth.models import User

        from core.models import NotificationPreference
        from membership.models import Member, MembershipPlan

        settings.DEFAULT_FROM_EMAIL = "noreply@pastlives.space"
        cfg = ClassSettings.load()
        cfg.reminder_hours_before = 24
        cfg.save()
        # A MembershipPlan must exist for the post_save signal to auto-create a Member.
        MembershipPlan.objects.create(name="Standard", monthly_price="50.00")
        user = User.objects.create_user(username="optedin@example.com", email="optedin@example.com")
        member = Member.objects.get(user=user)
        NotificationPreference.objects.create(user=user, event_key="class_reminder", channel="email", enabled=True)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(hours=24, minutes=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        RegistrationFactory(
            class_offering=offering, status=Registration.Status.CONFIRMED, member=member, email=user.email
        )
        mail.outbox.clear()
        sent = send_due_class_reminders(window_minutes=30)
        assert sent == 1
        assert len(mail.outbox) == 1


def describe_sync_local_class_events():
    def it_upserts_published_class_sessions_as_calendar_events(db):
        from hub.calendar_service import sync_local_class_events
        from membership.models import CalendarEvent

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, title="Port Me")
        start = timezone.now() + timedelta(days=3)
        session = ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        count = sync_local_class_events()
        assert count == 1
        event = CalendarEvent.objects.get(uid=f"local-class-{session.pk}")
        assert event.title == "Port Me"
        assert event.source == "classes"

    def it_skips_draft_and_private_classes(db):
        from hub.calendar_service import sync_local_class_events

        draft = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        private = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, is_private=True, slug="priv")
        start = timezone.now() + timedelta(days=3)
        ClassSessionFactory(class_offering=draft, starts_at=start, ends_at=start + timedelta(hours=2))
        ClassSessionFactory(class_offering=private, starts_at=start, ends_at=start + timedelta(hours=2))
        assert sync_local_class_events() == 0

    def it_purges_events_whose_sessions_were_removed(db):
        from hub.calendar_service import sync_local_class_events
        from membership.models import CalendarEvent

        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(days=3)
        session = ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        sync_local_class_events()
        assert CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()
        session.delete()
        sync_local_class_events()
        assert not CalendarEvent.objects.filter(uid=f"local-class-{session.pk}").exists()
