"""The instructor's new-registration email counts seats the way every screen does."""

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration


def describe_seats_taken():
    def it_counts_only_the_statuses_that_consume_capacity(db):
        offering = ClassOfferingFactory(capacity=12)
        for _ in range(10):
            RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        for _ in range(2):
            RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        for _ in range(3):
            RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        assert offering.seats_taken == 10
        assert offering.spots_remaining == 2

    def it_is_what_the_instructor_email_reports(db, mailoutbox):
        from classes.emails import emit_instructor_new_registration

        offering = ClassOfferingFactory(capacity=6)
        for _ in range(4):
            RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        newest = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        emit_instructor_new_registration(newest)
        body = "\n".join(m.body for m in mailoutbox)
        assert "1/6" in body
        assert "5/6" not in body
