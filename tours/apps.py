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
        # Tours find their visitor's account by verified email (Morlock, 2026-11-22), in place of making
        # one at booking: at sign in, signup and an email's confirmation.
        from allauth.account.signals import email_confirmed, user_logged_in, user_signed_up

        from tours import claims

        user_logged_in.connect(claims.on_sign_in, dispatch_uid="tours.claims.sign_in")
        user_signed_up.connect(claims.on_sign_in, dispatch_uid="tours.claims.sign_up")
        email_confirmed.connect(claims.on_email_confirmed, dispatch_uid="tours.claims.email_confirmed")
