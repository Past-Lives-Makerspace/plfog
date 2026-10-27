from django.apps import AppConfig


class ToursConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tours"
    verbose_name = "Facility Tours"

    def ready(self) -> None:
        # The Google Calendar sync (#951) hangs off the booking seam, sent after commit.
        from django.db.models.signals import pre_delete

        from tours import calendar_sync, signals
        from tours.models import TourBooking

        signals.tour_booking_changed.connect(calendar_sync.queue_booking_sync, dispatch_uid="tours.calendar_sync")
        # A booking deleted outright (admin, shell) still gets its Google event removed.
        pre_delete.connect(
            calendar_sync.forget_deleted_booking, sender=TourBooking, dispatch_uid="tours.calendar_sync.deleted"
        )
