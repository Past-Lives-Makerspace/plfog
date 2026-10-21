from django.apps import AppConfig


class HqConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hq"
    verbose_name = "Event HQ"

    def ready(self) -> None:
        import hq.signals  # noqa: F401  registers the receivers that delete files after a row goes
