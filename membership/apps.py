from django.apps import AppConfig


class MembershipConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "membership"
    verbose_name = "Makerspace Membership"

    def ready(self) -> None:
        import membership.signals  # noqa: F401
