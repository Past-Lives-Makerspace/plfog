from unittest.mock import patch

from django.apps import apps

from core.apps import CoreConfig


def describe_core_config():
    def it_has_correct_app_name():
        assert CoreConfig.name == "core"

    def it_has_correct_default_auto_field():
        assert CoreConfig.default_auto_field == "django.db.models.BigAutoField"

    def describe_ready():
        def it_calls_register_all_models():
            with (
                patch("plfog.auto_admin.register_all_models") as mock_register,
                patch("plfog.auto_admin.unregister_hidden_models"),
            ):
                config = apps.get_app_config("core")
                config.ready()
                mock_register.assert_called_once()

        def it_calls_unregister_hidden_models():
            with (
                patch("plfog.auto_admin.register_all_models"),
                patch("plfog.auto_admin.unregister_hidden_models") as mock_unregister,
            ):
                config = apps.get_app_config("core")
                config.ready()
                mock_unregister.assert_called_once()

        def it_calls_unregister_after_register():
            call_order = []

            def record_register(*args, **kwargs):
                call_order.append("register_all_models")

            def record_unregister(*args, **kwargs):
                call_order.append("unregister_hidden_models")

            with (
                patch("plfog.auto_admin.register_all_models", side_effect=record_register),
                patch("plfog.auto_admin.unregister_hidden_models", side_effect=record_unregister),
            ):
                config = apps.get_app_config("core")
                config.ready()
                assert call_order == ["register_all_models", "unregister_hidden_models"]
