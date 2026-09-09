"""Starter content for a new wiki page — one entry per :class:`~membership.models.WikiPage.Kind`.

Module data, not hardcoded template markup, so the starter chooser (``/wiki/new/``) and
the create form (``/wiki/new/<kind>/``) render from one source, and spec D can add its
seventh "Safety & Rules" starter card without editing spec A's template (brief §9.1).

Each entry supplies:

- The chooser card copy (``label`` / ``description`` / ``icon``).
- ``fact_prompts`` — blank-value Quick Answers rows pre-seeded on create, because the
  brief's starter template must prompt for the 4-8 facts people came for *first*.
- ``body`` — a starter body (headings only) for the Quill editor, so a member opens a
  structured page instead of a blank box.
"""

from __future__ import annotations

from typing import TypedDict


class WikiStarter(TypedDict):
    """One starter card / template on the ``/wiki/new/`` chooser.

    The dict key is the URL segment. For the six content starters it is also the
    :class:`~membership.models.WikiPage.Kind` value; spec D's Safety & Rules starter is
    the one whose segment is not a kind, which is why ``page_kind`` is a field of its own.
    """

    label: str
    description: str
    icon: str
    fact_prompts: list[str]
    body: str
    # The WikiPage.Kind the page is filed under. Same as the key for the six content
    # starters; the Safety starter picks one because "safety" is not a kind (the brief
    # locks the six) and it is the status, not the kind, that makes a page safety content.
    page_kind: str
    # A WikiPage.Status the starter forces, or "" for the COMMUNITY default. Only the
    # Safety starter sets one: safety content IS Official content (the brief's Official is
    # "policy, safety, membership terms"), so it needs no field of its own and there is no
    # box on any form for a member to untick.
    status: str


STARTERS: dict[str, WikiStarter] = {
    "machine": {
        "label": "Machine or tool",
        "description": "A saw, a kiln, a press. What it does and how not to break it.",
        "icon": "machine",
        "fact_prompts": ["Blade or bit", "Max size", "Where the manual is", "Common mistake"],
        "body": (
            "<h2>What It Does</h2><p></p>"
            "<h2>How To Use It</h2><p></p>"
            "<h2>What Goes Wrong</h2><p></p>"
            "<h2>Tips From Members</h2><p></p>"
        ),
        "page_kind": "machine",
        "status": "",
    },
    "howto": {
        "label": "How to do something",
        "description": "A process, a technique, a fix. Steps someone can follow.",
        "icon": "howto",
        "fact_prompts": ["Tools needed", "Time it takes", "Skill level"],
        "body": ("<h2>Before You Start</h2><p></p><h2>Steps</h2><p></p><h2>What Goes Wrong</h2><p></p>"),
        "page_kind": "howto",
        "status": "",
    },
    "material": {
        "label": "Material",
        "description": "Wood, resin, filament, fabric. What it is and how to work with it.",
        "icon": "material",
        "fact_prompts": ["Where to buy it", "Typical cost", "Best used for"],
        "body": ("<h2>What It Is</h2><p></p><h2>Working With It</h2><p></p><h2>Where To Get It</h2><p></p>"),
        "page_kind": "material",
        "status": "",
    },
    "project": {
        "label": "Project write-up",
        "description": "Something you made. How you did it, so someone can make it too.",
        "icon": "project",
        "fact_prompts": ["Time it took", "Skill level", "Materials used"],
        "body": ("<h2>What I Made</h2><p></p><h2>How I Did It</h2><p></p><h2>What I'd Do Differently</h2><p></p>"),
        "page_kind": "project",
        "status": "",
    },
    "guild_info": {
        "label": "How this guild works",
        "description": "Meeting times, how to join, who to ask.",
        "icon": "guild_info",
        "fact_prompts": ["Meeting time", "How to join", "Who to ask"],
        "body": "<h2>How To Get Involved</h2><p></p><h2>What We Do</h2><p></p>",
        "page_kind": "guild_info",
        "status": "",
    },
    "reference": {
        "label": "Reference table or chart",
        "description": "A cheat sheet, a chart, a lookup table.",
        "icon": "reference",
        "fact_prompts": ["Source", "Last updated"],
        "body": "<h2>Reference</h2><p></p>",
        "page_kind": "reference",
        "status": "",
    },
    # Spec D's safety gate, and the ONLY starter that forces a status. A non-moderator
    # saving this lands an unpublished page in the guild's review queue instead of
    # publishing live; a lead or admin publishes straight through. Filed as GUILD_INFO
    # because that carries a twelve-month review clock, which is the interval safety
    # content needs — the label is changeable on the form, the clock is the point.
    "safety": {
        "label": "Safety and rules",
        "description": "Rules, hazards, required gear. A guild lead reads these before they go live.",
        "icon": "safety",
        "fact_prompts": ["Required gear", "Who may use it", "Who to ask"],
        "body": (
            "<h2>The Rules</h2><p></p>"
            "<h2>What Can Go Wrong</h2><p></p>"
            "<h2>Required Gear</h2><p></p>"
            "<h2>Who To Ask</h2><p></p>"
        ),
        "page_kind": "guild_info",
        "status": "official",
    },
}
