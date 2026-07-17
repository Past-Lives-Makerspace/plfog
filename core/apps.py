from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _update_default_site(sender: type, **kwargs: object) -> None:
    """Ensure the default Site object reflects Past Lives, not example.com."""
    from django.contrib.sites.models import Site

    try:
        site = Site.objects.get(pk=1)
    except Site.DoesNotExist:
        return
    if site.domain == "example.com":
        site.domain = "pastlives.plaza.codes"
        site.name = "Past Lives Makerspace"
        site.save(update_fields=["domain", "name"])


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self) -> None:
        import core.checks  # noqa: F401
        from core import signals  # noqa: F401  — registers receivers

        from plfog.auto_admin import register_all_models, unregister_hidden_models

        register_all_models()
        unregister_hidden_models()
        post_migrate.connect(_update_default_site, sender=self)

        # Import every app's discord_commands module so slash commands self-register
        # (loads core.events.discord_commands → registers the built-in /fog-ping).
        from core.events import discord_commands

        discord_commands.autodiscover()
