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
    """One starter card / template for a wiki :class:`~membership.models.WikiPage.Kind`."""

    label: str
    description: str
    icon: str
    fact_prompts: list[str]
    body: str


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
    },
    "howto": {
        "label": "How to do something",
        "description": "A process, a technique, a fix. Steps someone can follow.",
        "icon": "howto",
        "fact_prompts": ["Tools needed", "Time it takes", "Skill level"],
        "body": ("<h2>Before You Start</h2><p></p><h2>Steps</h2><p></p><h2>What Goes Wrong</h2><p></p>"),
    },
    "material": {
        "label": "Material",
        "description": "Wood, resin, filament, fabric. What it is and how to work with it.",
        "icon": "material",
        "fact_prompts": ["Where to buy it", "Typical cost", "Best used for"],
        "body": ("<h2>What It Is</h2><p></p><h2>Working With It</h2><p></p><h2>Where To Get It</h2><p></p>"),
    },
    "project": {
        "label": "Project write-up",
        "description": "Something you made. How you did it, so someone can make it too.",
        "icon": "project",
        "fact_prompts": ["Time it took", "Skill level", "Materials used"],
        "body": ("<h2>What I Made</h2><p></p><h2>How I Did It</h2><p></p><h2>What I'd Do Differently</h2><p></p>"),
    },
    "guild_info": {
        "label": "How this guild works",
        "description": "Meeting times, how to join, who to ask.",
        "icon": "guild_info",
        "fact_prompts": ["Meeting time", "How to join", "Who to ask"],
        "body": "<h2>How To Get Involved</h2><p></p><h2>What We Do</h2><p></p>",
    },
    "reference": {
        "label": "Reference table or chart",
        "description": "A cheat sheet, a chart, a lookup table.",
        "icon": "reference",
        "fact_prompts": ["Source", "Last updated"],
        "body": "<h2>Reference</h2><p></p>",
    },
}
