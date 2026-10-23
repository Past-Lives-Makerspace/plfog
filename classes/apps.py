from django.apps import AppConfig


class ClassesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "classes"
    verbose_name = "Classes"

    def ready(self) -> None:
        from classes import signals  # noqa: F401  # connects the receivers
