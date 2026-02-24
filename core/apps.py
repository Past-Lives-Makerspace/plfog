from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from plfog.auto_admin import register_all_models, unregister_hidden_models

        register_all_models()
        unregister_hidden_models()
