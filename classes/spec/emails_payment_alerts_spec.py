"""BDD specs for the two class payment alerts: forced email to every Admin, one per payment (#524)."""

from __future__ import annotations

from django.core import mail

from classes.emails import send_duplicate_payment_alert, send_orphaned_payment_alert
from classes.factories import RegistrationFactory, UserFactory
from core.models import NotificationPreference, TransactionalEmailLog
from membership.models import Member

ALERTS = {
    "classes.duplicate_payment_alert": (send_duplicate_payment_alert, "Duplicate payment:"),
    "classes.orphaned_payment_alert": (send_orphaned_payment_alert, "Payment needs a decision:"),
}


def _admin(email: str) -> Member:
    member = UserFactory(username=email, email=email).member  # type: ignore[attr-defined]
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    return member


def _alerts(prefix: str) -> list:
    return [m for m in mail.outbox if m.subject.startswith(prefix)]


def describe_class_payment_alerts():
    def it_emails_every_admin_one_copy_labelled_with_the_old_trigger_kind(db, settings):
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        _admin("a1@example.com")
        _admin("a2@example.com")
        for key, (send, prefix) in ALERTS.items():
            mail.outbox.clear()
            registration = RegistrationFactory(first_name="Pat", last_name="Lee")

            send(registration, amount_cents=4500, payment_intent=f"pi_{key}", session_id=f"cs_{key}")

            alerts = _alerts(prefix)
            assert sorted(m.to[0] for m in alerts) == ["a1@example.com", "a2@example.com"], key
            assert "$45.00" in alerts[0].body
            assert f"https://dashboard.stripe.com/payments/pi_{key}" in alerts[0].body
            assert f"Checkout session: cs_{key}" in alerts[0].body
            # The key is the old trigger_kind, so the email log reads as one series.
            assert TransactionalEmailLog.objects.filter(trigger_kind=key).count() == 2, key

    def it_reaches_an_admin_who_switched_the_email_off(db, settings):
        # Money is owed, so the email is forced: no switch can stop it.
        settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
        admin = _admin("forced@example.com")
        for key, (send, prefix) in ALERTS.items():
            NotificationPreference.objects.create(user=admin.user, event_key=key, channel="email", enabled=False)
            mail.outbox.clear()

            send(RegistrationFactory(), amount_cents=100, payment_intent=f"pi_off_{key}", session_id="cs_off")

            assert [m.to for m in _alerts(prefix)] == [["forced@example.com"]], key

    def describe_the_dedupe_period():
        def it_sends_a_second_alert_for_a_second_payment_on_the_same_registration(db, settings):
            # The trap: an empty period would claim one ledger slot per admin forever and
            # swallow every later alert as a repeat.
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            _admin("twice@example.com")
            for send, prefix in ALERTS.values():
                registration = RegistrationFactory()
                mail.outbox.clear()

                send(registration, amount_cents=100, payment_intent="pi_one", session_id="cs_one")
                send(registration, amount_cents=100, payment_intent="pi_two", session_id="cs_two")

                assert len(_alerts(prefix)) == 2, prefix

        def it_sends_separate_alerts_for_two_registrations_paid_the_same_way(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            _admin("tworegs@example.com")
            for send, prefix in ALERTS.values():
                mail.outbox.clear()

                send(RegistrationFactory(), amount_cents=100, payment_intent="pi_same", session_id="cs_same")
                send(RegistrationFactory(), amount_cents=100, payment_intent="pi_same", session_id="cs_same")

                assert len(_alerts(prefix)) == 2, prefix

        def it_does_not_repeat_an_alert_for_the_same_payment(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            _admin("once@example.com")
            for send, prefix in ALERTS.values():
                registration = RegistrationFactory()
                mail.outbox.clear()

                send(registration, amount_cents=100, payment_intent="pi_again", session_id="cs_again")
                send(registration, amount_cents=100, payment_intent="pi_again", session_id="cs_again")

                assert len(_alerts(prefix)) == 1, prefix

        def it_keys_on_the_session_when_stripe_sent_no_payment_intent(db, settings):
            settings.CLASS_ADMIN_NOTIFY_EMAILS = ""
            _admin("nopi@example.com")
            for send, prefix in ALERTS.values():
                registration = RegistrationFactory()
                mail.outbox.clear()

                send(registration, amount_cents=100, payment_intent="", session_id="cs_first")
                send(registration, amount_cents=100, payment_intent="", session_id="cs_second")

                assert len(_alerts(prefix)) == 2, prefix
