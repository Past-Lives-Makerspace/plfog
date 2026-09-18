"""Declared registry of every member feature an admin can switch off — one source of truth.

**The switch owns the sidebar and nothing else. It is purely cosmetic.**

Three states, not two. ``ON`` is today's entry, byte for byte. ``SOON`` still renders the entry,
inert, revealing the admin's own message on hover and on keyboard focus. ``HIDDEN`` removes it.

No route is gated, for anyone. A saved URL keeps working in every state — that is intended, not
a gap. Jo: *"They can still use the tools if they have the URLs saved but this is purely
cosmetic."* So **Hidden is not a security boundary and must never be used as one**: nothing here
withholds a feature that must not be reached. This replaced an earlier design in which both off
states 404'd every route in the family; ``wiki_feature_required`` and
``equipment_feature_required`` were that behaviour for two features and are gone with it.

Beyond the sidebar, two surfaces follow the switch for cosmetic consistency rather than access:
the lobby kiosk deck and the release-email screenshot registry. A wall screen must not advertise
a feature the sidebar has just hidden.

Features are declared **here**; the database holds one :class:`core.models.FeatureSwitch` row
per key carrying its state and message. That is exactly the shape ``core/scheduled_jobs.py``
and ``ScheduledJobState`` already use for Automations, and it is why adding the eighth feature
is one entry in :data:`FEATURES` and no migration at all.

This module deliberately imports ``core.models`` only inside functions. The model reads
:class:`FeatureState` and :data:`FEATURES` at class-definition time, so a module-level import
the other way would be a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models

# What the bubble says when an admin flips a feature to Coming soon and writes nothing.
# Applied once, in FeatureView, so no template and no caller has to know the fallback.
DEFAULT_SOON_MESSAGE = "Coming soon!"


class FeatureState(models.TextChoices):
    ON = "on", "On"
    SOON = "soon", "Coming soon"
    HIDDEN = "hidden", "Hidden"


@dataclass(frozen=True)
class Feature:
    """One row of the registry.

    ``key`` is the ``FeatureSwitch.feature_key`` the state is stored under and the name every
    template and spec refers to. ``off_description`` is the "what turning this off does" line the
    admin card shows, kept next to the key instead of scattered across ``help_text`` — and it
    describes the SIDEBAR, because the sidebar is all this switch touches.
    """

    key: str
    name: str
    off_description: str


FEATURES: list[Feature] = [
    Feature(
        key="teach",
        name="Host a Workshop",
        off_description=(
            "Takes the Host a Workshop invitation out of the sidebar for members who cannot teach "
            "yet. Instructors keep their Teaching entry either way."
        ),
    ),
    Feature(
        key="meetings",
        name="Meetings",
        off_description="Takes Meetings out of the sidebar, and the Meetings tab off guild pages.",
    ),
    Feature(
        key="directory",
        name="Member Directory",
        off_description=(
            "Takes Member Directory out of the sidebar and off the home page. Whether signed-out "
            "visitors can see it while it IS on is the separate Public member directory switch below."
        ),
    ),
    Feature(
        key="spaces",
        name="Spaces",
        off_description="Takes Spaces out of the sidebar, and the Spaces card off the Help page.",
    ),
    Feature(
        key="equipment",
        name="Equipment",
        off_description="Takes Equipment out of the sidebar.",
    ),
    Feature(
        key="voting",
        name="Voting",
        off_description="Takes Voting out of the sidebar and off the home page.",
    ),
    Feature(
        key="wiki",
        name="Wiki",
        off_description="Takes the Wiki out of the sidebar, and the Wiki tab off guild pages.",
    ),
    Feature(
        key="guilds",
        name="Guild Pages",
        off_description=(
            "Takes the whole Guilds section out of the sidebar — the heading and every guild listed under it."
        ),
    ),
]

FEATURES_BY_KEY: dict[str, Feature] = {feature.key: feature for feature in FEATURES}


@dataclass(frozen=True)
class FeatureView:
    """One feature's live state, in the shape templates and the kiosk read it.

    ``message`` already carries the :data:`DEFAULT_SOON_MESSAGE` fallback, so a blank admin
    message is indistinguishable from a written one by the time anything renders it.
    """

    key: str
    name: str
    state: str
    message: str

    @property
    def is_on(self) -> bool:
        return self.state == FeatureState.ON

    @property
    def is_soon(self) -> bool:
        return self.state == FeatureState.SOON

    @property
    def is_hidden(self) -> bool:
        return self.state == FeatureState.HIDDEN


def is_on(key: str) -> bool:
    """Whether ``key``'s feature is fully on.

    With no state row the answer is ON, which is what makes the deploy a no-op and what makes an
    eighth feature need no migration: a database that has never seen the key behaves exactly as
    it did before the key existed.
    """
    from core.models import FeatureSwitch

    return FeatureSwitch.objects.state_of(key) == FeatureState.ON
