"""Starter content for a new wiki page — one entry per card on the ``/wiki/new/`` chooser.

Module data, not hardcoded template markup, so the starter chooser (``/wiki/new/``) and
the create form (``/wiki/new/<kind>/``) render from one source, and spec D can add its
seventh "Safety & Rules" starter card without editing spec A's template (brief §9.1).

Each entry supplies:

- The chooser card copy (``label`` / ``description`` / ``icon``).
- ``body`` — a starter body (headings only) for the Quill editor, so a member opens a
  structured page instead of a blank box. The ``blank`` starter's is empty on purpose.

There is deliberately no ``fact_prompts`` key any more. Create mode used to pre-seed one
Quick Answers row per prompt, which put three or four list-editor cards — each with a
drag grip, two fields, two reorder arrows and a Remove button — between "The Basics" and
the box the member actually came to type in, and labelled them "Question: Tools needed",
which is not a question. Quick Answers is opt-in now: the section renders empty with one
"+ Add A Quick Answer" button. The machine seeder still writes prompt rows, because on an
auto-created stub the prompts ARE the content; its list lives in that command.
"""

from __future__ import annotations

from typing import TypedDict


class WikiStarter(TypedDict):
    """One starter card / template on the ``/wiki/new/`` chooser.

    The dict key is the URL segment. For the six content starters it is also the
    :class:`~membership.models.WikiPage.Kind` value; spec D's Safety & Rules starter and
    the Blank page one are the two whose segment is not a kind, which is why
    ``page_kind`` is a field of its own.
    """

    label: str
    description: str
    icon: str
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
        "body": ("<h2>Before You Start</h2><p></p><h2>Steps</h2><p></p><h2>What Goes Wrong</h2><p></p>"),
        "page_kind": "howto",
        "status": "",
    },
    "material": {
        "label": "Material",
        "description": "Wood, resin, filament, fabric. What it is and how to work with it.",
        "icon": "material",
        "body": ("<h2>What It Is</h2><p></p><h2>Working With It</h2><p></p><h2>Where To Get It</h2><p></p>"),
        "page_kind": "material",
        "status": "",
    },
    "project": {
        "label": "Project write-up",
        "description": "Something you made. How you did it, so someone can make it too.",
        "icon": "project",
        "body": ("<h2>What I Made</h2><p></p><h2>How I Did It</h2><p></p><h2>What I'd Do Differently</h2><p></p>"),
        "page_kind": "project",
        "status": "",
    },
    "guild_info": {
        "label": "How this guild works",
        "description": "Meeting times, how to join, who to ask.",
        "icon": "guild_info",
        "body": "<h2>How To Get Involved</h2><p></p><h2>What We Do</h2><p></p>",
        "page_kind": "guild_info",
        "status": "",
    },
    "reference": {
        "label": "Reference table or chart",
        "description": "A cheat sheet, a chart, a lookup table.",
        "icon": "reference",
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
        "body": (
            "<h2>The Rules</h2><p></p>"
            "<h2>What Can Go Wrong</h2><p></p>"
            "<h2>Required Gear</h2><p></p>"
            "<h2>Who To Ask</h2><p></p>"
        ),
        "page_kind": "guild_info",
        "status": "official",
    },
    # Last on the chooser on purpose: the seven above are the guided route, and this is the
    # escape hatch for the member who already knows what they want to write and does not
    # want four headings to delete first. Filed as HOWTO because the chooser's own "not
    # sure?" hint already points there and the Kind select is right on the form — the point
    # of this card is the empty body, not the filing.
    "blank": {
        "label": "Blank page",
        "description": "No sections and no prompts. Start from nothing and write it your way.",
        "icon": "blank",
        "body": "",
        "page_kind": "howto",
        "status": "",
    },
}
