"""The slash-command registry, dispatcher, and the built-in ``/fog-ping`` command.

One declarative source of truth (:data:`_REGISTRY`) is read by *both* the
:mod:`register_discord_commands` management command (which PUTs the set to Discord) and
the :func:`dispatch` called by the interactions view — so the two never drift. Each app
that adds commands ships a top-level ``<app>/discord_commands.py`` module that calls
:func:`register`; :func:`autodiscover` imports them all at startup (tolerating apps that
ship none). This module also holds the one reference command, ``/fog-ping``, that makes
the whole platform verifiable end-to-end.

Mirrors the event :mod:`core.events.registry` autodiscovery pattern; the reply builders
live next door in :mod:`core.events.discord_interactions`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Literal

from core.events.discord_interactions import (
    ack_deferred,
    error_reply,
    reply,
    send_followup,
    send_modal_via_callback,
    unlinked_reply,
)

if TYPE_CHECKING:
    from django.http import HttpRequest

    from membership.models import Guild, Member

logger = logging.getLogger(__name__)

# An interaction payload is Discord's JSON dict; a handler returns a reply dict.
Interaction = dict
Handler = Callable[[Interaction, "Member | None"], dict]


@dataclass(frozen=True)
class SlashCommand:
    """One registered slash command — its Discord metadata plus its dispatch policy.

    Args:
        name: The command name (no leading slash), e.g. ``"fog-ping"``.
        description: Shown in Discord's command picker.
        handler: ``(interaction, member) -> reply dict``. Never called for an unlinked
            member when ``requires_link`` is set (dispatch returns the connect prompt first).
        options: Discord application-command option objects (default none).
        options_builder: Optional callable producing the options at *serialization* time
            (inside :meth:`to_api_dict`, i.e. ``register_discord_commands``). Lets a command
            derive its options from the DB (e.g. a per-guild choice list) without any
            import-time query; when set it wins over the static ``options``.
        defer: Ack deferred first (§5.4) when the handler can't guarantee a <3s reply.
        ephemeral: Reply visible only to the invoking member (default — personal data).
        requires_link: Unlinked member → the connect prompt; the handler never runs.
        scope: ``"guild"`` (registered to the Past Lives server, instant) or ``"global"``.
    """

    name: str
    description: str
    handler: Handler
    options: list[dict] = field(default_factory=list)
    options_builder: Callable[[], list[dict]] | None = None
    defer: bool = False
    ephemeral: bool = True
    requires_link: bool = True
    scope: Literal["guild", "global"] = "guild"

    def to_api_dict(self) -> dict:
        """Serialize to Discord's application-command JSON (``type: 1`` = CHAT_INPUT).

        ``options_builder`` (when set) is called here to produce the options — the only
        place it runs, so a DB-derived choice list is queried at registration time, never
        at import time. Otherwise the static ``options`` list is used, so every command
        without a builder serializes exactly as before.
        """
        options = self.options_builder() if self.options_builder is not None else self.options
        return {"name": self.name, "description": self.description, "options": list(options), "type": 1}


_REGISTRY: dict[str, SlashCommand] = {}


def register(cmd: SlashCommand) -> None:
    """Add ``cmd`` to the registry, failing loudly on a duplicate name."""
    if cmd.name in _REGISTRY:
        raise ValueError(f"Duplicate slash command name: {cmd.name!r}")
    _REGISTRY[cmd.name] = cmd


@dataclass(frozen=True)
class ComponentHandler:
    """One ``custom_id`` namespace — everything before the first ``:`` routes to ``handler``.

    The message-component (button / select-menu click) counterpart of :class:`SlashCommand`.
    A command that ships components encodes its state into each ``custom_id`` as
    ``"<prefix>:<state…>"``; :func:`dispatch_component` routes the click back to the owning
    handler by that prefix. Registered from the same autodiscovered
    ``<app>/discord_commands.py`` modules as the slash commands.

    Args:
        prefix: The ``custom_id`` namespace (no colon), e.g. ``"members"``.
        handler: ``(interaction, member) -> reply dict`` — usually a type-7 UPDATE_MESSAGE
            (:func:`core.events.discord_interactions.update_message`).
        requires_link: Unlinked clicker → the connect prompt; the handler never runs. The
            clicker of an ephemeral message is always the original invoker, who was linked
            at invoke time — but they may have unlinked between clicks, so the re-check is
            real, not ceremony.
    """

    prefix: str
    handler: Handler
    requires_link: bool = True


_COMPONENT_REGISTRY: dict[str, ComponentHandler] = {}


def register_component(handler: ComponentHandler) -> None:
    """Add ``handler`` to the component registry, failing loudly on a duplicate prefix."""
    if handler.prefix in _COMPONENT_REGISTRY:
        raise ValueError(f"Duplicate component prefix: {handler.prefix!r}")
    _COMPONENT_REGISTRY[handler.prefix] = handler


@dataclass(frozen=True)
class ModalHandler:
    """One MODAL_SUBMIT ``custom_id`` namespace — everything before the first ``:`` routes here.

    The modal-submit counterpart of :class:`ComponentHandler`. A command whose slash handler
    (or a component click) returns a modal encodes its submit route as the modal's
    ``custom_id`` prefix; :func:`dispatch_modal` routes the submit back by that prefix.
    Registered from the same autodiscovered ``<app>/discord_commands.py`` modules.

    Args:
        prefix: The modal ``custom_id`` namespace (no colon), e.g. ``"pollform"``.
        handler: ``(interaction, member) -> reply dict`` — a MODAL_SUBMIT is answered with a
            plain type-4 message (:func:`~core.events.discord_interactions.reply`), never
            another modal.
        requires_link: Unlinked submitter → the connect prompt; the handler never runs.
    """

    prefix: str
    handler: Handler
    requires_link: bool = True


_MODAL_REGISTRY: dict[str, ModalHandler] = {}


def register_modal(handler: ModalHandler) -> None:
    """Add ``handler`` to the modal registry, failing loudly on a duplicate prefix."""
    if handler.prefix in _MODAL_REGISTRY:
        raise ValueError(f"Duplicate modal prefix: {handler.prefix!r}")
    _MODAL_REGISTRY[handler.prefix] = handler


def all_commands() -> list[SlashCommand]:
    """Every registered command, in registration order."""
    return list(_REGISTRY.values())


def autodiscover() -> None:
    """Import each installed app's ``discord_commands`` module so it self-registers.

    Tolerates apps that ship no such module (the common case); a real import error inside
    a module that *does* exist is re-raised, never swallowed.
    """
    from django.apps import apps

    for app_config in apps.get_app_configs():
        module_name = f"{app_config.name}.discord_commands"
        try:
            import_module(module_name)
        except ModuleNotFoundError as exc:
            # Only the module's own absence is tolerated; a missing import *inside* a
            # present discord_commands module has a different exc.name and must surface.
            if exc.name != module_name:
                raise


# --- Resolution helpers -------------------------------------------------------


def resolve_member(interaction: Interaction) -> Member | None:
    """The linked :class:`Member` for the invoking Discord user, or ``None`` if unlinked.

    Handles both payload shapes: ``interaction.member.user.id`` in a guild channel and
    ``interaction.user.id`` in a DM. Looks up the verified, unique ``discord_user_id``
    (never the free-text ``discord_handle``). ``None`` is the expected common case (most
    members haven't linked), not an error.
    """
    from membership.models import Member

    user = interaction.get("member", {}).get("user") or interaction.get("user")
    discord_user_id = (user or {}).get("id", "")
    if not discord_user_id:
        return None
    return Member.objects.filter(discord_user_id=discord_user_id).first()


def _option_value(interaction: Interaction, name: str) -> object | None:
    """The value of the named command option, or ``None`` if it wasn't supplied."""
    for option in interaction.get("data", {}).get("options", []):
        if option.get("name") == name:
            return option.get("value")
    return None


def resolve_guild(interaction: Interaction) -> Guild | None:
    """The guild a guild-scoped command targets: explicit ``guild`` option beats channel.

    1. An explicit ``guild`` option (its value = the guild's pk) wins outright.
    2. Else the guild mapped to ``interaction.channel_id`` (channel auto-detect).
    3. Neither → ``None`` (the caller shows the disambiguation reply, §5.6).
    """
    from membership.models import Guild

    explicit = _option_value(interaction, "guild")
    if explicit:
        return Guild.objects.filter(pk=str(explicit)).first()
    return Guild.objects.for_discord_channel(interaction.get("channel_id", ""))


def guild_disambiguation_reply(member: Member | None) -> dict:
    """The ephemeral "I couldn't tell which guild you mean" reply (§6 Reply E).

    Lists the guilds the member belongs to so they can rerun the command in the right
    channel or pass the ``guild`` option. Never a silent no-op.
    """
    guilds = [gm.guild for gm in member.guild_memberships.select_related("guild").all()] if member is not None else []
    content = (
        "I couldn't tell which guild you mean. Run this in your guild's Discord channel, or add the `guild` option."
    )
    if guilds:
        content += "\n\nYou're in: " + ", ".join(g.name for g in guilds) + "."
    return reply(content, ephemeral=True)


# --- Dispatch -----------------------------------------------------------------


def _link_url(request: HttpRequest) -> str:
    """The absolute one-click Discord-link URL an unlinked member is sent to."""
    from django.urls import reverse

    return request.build_absolute_uri(reverse("hub_discord_link_start"))


def _dispatch_deferred(cmd: SlashCommand, interaction: Interaction, member: Member | None) -> dict:
    """Ack deferred, run the handler synchronously, then PATCH the real reply in (§5.4).

    Returns an empty dict — the interaction was already acked via the callback endpoint,
    so Discord ignores the view's HTTP body.
    """
    ack_deferred(interaction["id"], interaction["token"], ephemeral=cmd.ephemeral)
    try:
        reply_dict = cmd.handler(interaction, member)
    except Exception:
        logger.exception("Discord deferred command %r handler failed", cmd.name)
        reply_dict = error_reply()
    data = reply_dict.get("data", {})
    send_followup(
        interaction["token"],
        content=data.get("content", ""),
        embeds=data.get("embeds"),
        components=data.get("components"),
    )
    return {}


def _route_modal(response: dict, interaction: Interaction) -> dict:
    """Deliver a type-9 modal via the REST callback; anything else passes through.

    Inline type-9 bodies are unreliable from an HTTP interactions endpoint (Discord
    drops them and the member sees a timeout), so a modal response goes out over the
    callback REST call — the same shape as the deferred path, which returns ``{}``
    inline once the REST side has answered. A callback rejection surfaces as an
    ephemeral reply carrying Discord's own error detail, never a silent timeout.
    """
    if response.get("type") != 9:
        return response
    detail = send_modal_via_callback(interaction["id"], interaction["token"], response)
    if detail is None:
        return {}
    return reply(f"Discord would not open the form. It said: {detail}", ephemeral=True)


def dispatch(interaction: Interaction, request: HttpRequest) -> dict:
    """Route an APPLICATION_COMMAND interaction to its handler and return a reply dict.

    1. Unknown command name → :func:`error_reply` (defensive: the registry and Discord's
       registered set drifted).
    2. ``requires_link`` and no linked member → :func:`unlinked_reply` (handler never runs).
    3. ``defer`` → the deferred path (§5.4).
    4. Else → the handler, wrapped so any exception becomes :func:`error_reply` (never a
       500 back to Discord, which would get the endpoint disabled).
    """
    # ``.get`` (not ``dict[key]``) is intentional here: an unknown name is an expected,
    # recoverable drift case, not a bug to fail loudly on — it degrades to error_reply.
    name = interaction["data"]["name"]
    cmd = _REGISTRY.get(name)
    if cmd is None:
        logger.warning("Discord interaction for unknown command %r", name)
        return error_reply()

    member = resolve_member(interaction)
    if cmd.requires_link and member is None:
        return unlinked_reply(_link_url(request))

    if cmd.defer:
        return _dispatch_deferred(cmd, interaction, member)

    try:
        return _route_modal(cmd.handler(interaction, member), interaction)
    except Exception:
        logger.exception("Discord command %r handler failed", name)
        return error_reply()


def dispatch_component(interaction: Interaction, request: HttpRequest) -> dict:
    """Route a MESSAGE_COMPONENT interaction (button / select click) to its handler.

    Mirrors :func:`dispatch` step for step:

    1. Unknown ``custom_id`` prefix → :func:`error_reply` (registered-message drift, e.g. a
       stale message clicked after a prefix rename).
    2. ``requires_link`` and no linked member → :func:`unlinked_reply` (the clicker unlinked
       since invoking; the handler never runs).
    3. Else → the handler, wrapped so any exception becomes :func:`error_reply` (never a
       5xx back to Discord).

    ``error_reply()`` / ``unlinked_reply()`` are type 4 — a fresh ephemeral message, valid
    as a component response — so an error never clobbers the browsable message in place.
    """
    custom_id = interaction["data"]["custom_id"]
    prefix = custom_id.split(":", 1)[0]
    handler = _COMPONENT_REGISTRY.get(prefix)
    if handler is None:
        logger.warning("Discord component interaction for unknown prefix %r", prefix)
        return error_reply()

    member = resolve_member(interaction)
    if handler.requires_link and member is None:
        return unlinked_reply(_link_url(request))

    try:
        return _route_modal(handler.handler(interaction, member), interaction)
    except Exception:
        logger.exception("Discord component %r handler failed", prefix)
        return error_reply()


def dispatch_modal(interaction: Interaction, request: HttpRequest) -> dict:
    """Route a MODAL_SUBMIT interaction to its handler by ``custom_id`` prefix.

    Mirrors :func:`dispatch_component` step for step: unknown prefix → :func:`error_reply`;
    ``requires_link`` and no linked member → :func:`unlinked_reply`; else the handler, wrapped
    so any exception becomes :func:`error_reply` (never a 5xx back to Discord). A MODAL_SUBMIT
    is answered with a type-4 message, so those replies are all valid responses.
    """
    custom_id = interaction["data"]["custom_id"]
    prefix = custom_id.split(":", 1)[0]
    handler = _MODAL_REGISTRY.get(prefix)
    if handler is None:
        logger.warning("Discord modal submit for unknown prefix %r", prefix)
        return error_reply()

    member = resolve_member(interaction)
    if handler.requires_link and member is None:
        return unlinked_reply(_link_url(request))

    try:
        return handler.handler(interaction, member)
    except Exception:
        logger.exception("Discord modal %r handler failed", prefix)
        return error_reply()


# --- The built-in reference command: /fog-ping --------------------------------


def _fog_ping(interaction: Interaction, member: Member | None) -> dict:
    """A linked member's "you're connected" smoke reply, with a link back to the hub.

    ``requires_link=True`` means dispatch has already resolved a linked member before this
    runs (an unlinked member gets the connect prompt instead), so ``member`` is non-None.
    """
    from django.conf import settings
    from django.urls import reverse

    hub_url = f"{settings.MEMBER_BASE_URL}{reverse('hub_home')}"
    button_row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Open the Past Lives hub", "url": hub_url},
        ],
    }
    display_name = member.display_name if member is not None else "there"
    return reply(
        f"**Past Lives** — you're connected as {display_name}. Everything's wired up ✅",
        ephemeral=True,
        components=[button_row],
    )


FOG_PING = SlashCommand(
    name="fog-ping",
    description="Check that your Discord is connected to your Past Lives account.",
    handler=_fog_ping,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(FOG_PING)


# --- The built-in guide command: /guide ---------------------------------------


def _guide(interaction: Interaction, member: Member | None) -> dict:
    """List every registered slash command and what it does, as an ephemeral embed.

    Built straight from :func:`all_commands` (never a hand-kept list), so it always mirrors
    the live registry — a command added anywhere shows up here automatically, and it lists
    itself. ``requires_link=False``: anyone can read the guide. A pure function of the
    registry — no DB, no side effects — so ``interaction`` and ``member`` go unread.
    """
    lines = [f"**/{cmd.name}** — {cmd.description}" for cmd in all_commands()]
    description = "\n".join(lines) + "\n\nSome commands need your account connected — run `/link` first."
    embed = {"title": "Past Lives commands", "description": description}
    return reply("", ephemeral=True, embeds=[embed])


GUIDE = SlashCommand(
    name="guide",
    description="List the Past Lives Discord commands and what they do.",
    handler=_guide,
    requires_link=False,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(GUIDE)
