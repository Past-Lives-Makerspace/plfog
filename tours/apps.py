from django.apps import AppConfig


class ToursConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tours"
    verbose_name = "Facility Tours"

    def ready(self) -> None:
        # The Google Calendar sync (#951) hangs off the booking seam, sent after commit.
        from tours import calendar_sync, signals

        signals.tour_booking_changed.connect(calendar_sync.queue_booking_sync, dispatch_uid="tours.calendar_sync")
