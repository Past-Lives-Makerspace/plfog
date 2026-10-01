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


def describe_a_flexible_class():
    """No seat cap (#545): spots_remaining and spots_remaining_map answer None, and the capacity column is never read."""

    def it_answers_none_however_many_hold_a_seat(db):
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(capacity=2, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        for _ in range(5):
            RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        assert offering.seats_taken == 5
        assert offering.spots_remaining is None

    def it_maps_to_none_beside_a_fixed_classs_count(db):
        from classes.models import ClassOffering

        flexible = ClassOfferingFactory(capacity=2, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        fixed = ClassOfferingFactory(capacity=6)
        for offering in (flexible, fixed):
            for _ in range(2):
                RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        spots = ClassOffering.objects.filter(pk__in=[flexible.pk, fixed.pk]).spots_remaining_map()
        assert spots == {flexible.pk: None, fixed.pk: 4}

    def it_leaves_a_fixed_class_at_zero_when_full(db):
        from classes.models import ClassOffering

        fixed = ClassOfferingFactory(capacity=1)
        RegistrationFactory(class_offering=fixed, status=Registration.Status.CONFIRMED)
        assert fixed.spots_remaining == 0
        assert ClassOffering.objects.filter(pk=fixed.pk).spots_remaining_map() == {fixed.pk: 0}
