"""Declared registry of every member feature an admin can switch off — one source of truth.

Three states, not two. ``ON`` is today's behaviour, byte for byte. ``SOON`` still renders the
nav entry, inert, revealing the admin's own message on hover and on keyboard focus. ``HIDDEN``
removes the entry entirely. **Both off states 404 every route in the family**, because a
disabled feature is fully dark — that is what ``wiki_feature_required`` already did for the
wiki, and this generalises it to all seven.

Nobody bypasses, admins included: an admin who Hides Voting loses the admin voting screens
too. That is recoverable rather than alarming, because Site Settings is never gated and is
where the state came from.

Features are declared **here**; the database holds one :class:`core.models.FeatureSwitch` row
per key carrying its state and message. That is exactly the shape ``core/scheduled_jobs.py``
and ``ScheduledJobState`` already use for Automations, and it is why adding the eighth feature
is one entry in :data:`FEATURES` and no migration at all.

This module deliberately imports ``core.models`` only inside functions. The model reads
:class:`FeatureState` and :data:`FEATURES` at class-definition time, so a module-level import
the other way would be a cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any

from django.db import models
from django.http import Http404, HttpRequest, HttpResponse

if TYPE_CHECKING:
    from django.urls import URLPattern

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
    gate, template and spec refers to. ``off_description`` is the "what turning this off does"
    line the admin card shows, kept next to the key instead of scattered across ``help_text``.
    """

    key: str
    name: str
    off_description: str


FEATURES: list[Feature] = [
    Feature(
        key="teach",
        name="Host a Workshop",
        off_description=(
            "The Host a Workshop invitation disappears for members who cannot teach yet, and its "
            "marketing and apply pages answer 404. Instructors keep their Teaching entry and the "
            "whole teaching portal either way."
        ),
    ),
    Feature(
        key="meetings",
        name="Meetings",
        off_description=(
            "The Meetings entry goes, and every meeting workspace, agenda item, action and "
            "proposal page answers 404. Guild pages lose their Meetings tab."
        ),
    ),
    Feature(
        key="directory",
        name="Member Directory",
        off_description=(
            "The Member Directory entry goes and /members/ answers 404, for signed-in members and "
            "signed-out visitors alike. Whether visitors can see it while it IS on is the separate "
            "Public member directory switch."
        ),
    ),
    Feature(
        key="spaces",
        name="Spaces",
        off_description=(
            "The Spaces entry goes, and the floor plan, every studio and shared-area listing and "
            "every space request answers 404."
        ),
    ),
    Feature(
        key="equipment",
        name="Equipment",
        off_description=("The Equipment entry goes, and every equipment page, booking and manage surface answers 404."),
    ),
    Feature(
        key="voting",
        name="Voting",
        off_description=(
            "Guild Voting and the admin voting screens answer 404, snapshots and results "
            "included. Admins lose their own voting screens too — Site Settings brings them back."
        ),
    ),
    Feature(
        key="wiki",
        name="Wiki",
        off_description=("The Wiki entry goes, and every wiki page, write and QR sticker link answers 404."),
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

    Every gate in the app asks this one question. With no state row the answer is ON, which is
    what makes the deploy a no-op: the migration seeds rows, but a database that somehow has
    none still behaves exactly like today.
    """
    from core.models import FeatureSwitch

    return FeatureSwitch.objects.state_of(key) == FeatureState.ON


def feature_required(key: str) -> Callable[[Any], Any]:
    """404 every route in ``key``'s family while the feature is not On.

    The generalisation of ``wiki_feature_required`` and ``equipment_feature_required``, which
    now both delegate here rather than each carrying their own copy of this three-line check.
    404 rather than a redirect, so a crafted request learns nothing about a half-built feature —
    and Coming soon answers exactly as Hidden does, because the difference between them is a nav
    affordance, not an access level.
    """
    if key not in FEATURES_BY_KEY:
        raise KeyError(f"No such feature: {key!r}")

    def decorator(view_func: Any) -> Any:
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not is_on(key):
                raise Http404(f"The {FEATURES_BY_KEY[key].name} feature is turned off.")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def gate_named(patterns: Sequence[URLPattern], key: str, prefixes: Sequence[str]) -> list[str]:
    """Gate every pattern in ``patterns`` whose URL name starts with one of ``prefixes``.

    The five families that had no gate at all live in ``hub/urls.py`` as contiguous blocks, but
    wrapping each block in place would re-indent ~100 untouched ``path()`` lines and bury the
    change. Selecting by URL name instead keeps the diff additive, declares each family's fence
    in one readable place, and — the reason that matters — catches a route somebody adds to the
    family later, which an explicit list would silently miss.

    A prefix that is the whole name matches exactly that one route, which is how the teaching
    portal stays live while three recruiting routes beside it do not. Returns the names it
    gated, in pattern order, so a spec can assert the fence caught exactly the intended set —
    the prefixes are only as safe as the test that pins them.
    """
    gate = feature_required(key)
    gated_names: list[str] = []
    for pattern in patterns:
        name = getattr(pattern, "name", None)
        if name is not None and name.startswith(tuple(prefixes)):
            pattern.callback = gate(pattern.callback)
            gated_names.append(name)
    return gated_names


def drop_unavailable(keys_by_item: Iterable[tuple[Any, str]]) -> list[Any]:
    """Keep only the items whose paired feature key is On.

    Shared by the two places that generate links to features rather than rendering them
    in a template: the lobby kiosk deck (``membership/signage.py``) and the release-email
    screenshot registry (``core/release_email.py``). Both would otherwise happily point at a
    route this switch has just turned into a 404.
    """
    return [item for item, key in keys_by_item if is_on(key)]
