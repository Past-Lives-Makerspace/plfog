"""Test helper for the three-state feature switches (``core.features``).

Before #405 a spec turned a feature off by flipping a boolean on the ``SiteConfiguration``
singleton. There is no such boolean now — state lives in one ``FeatureSwitch`` row per feature —
so this is the one-liner every spec uses instead. Keeping it here rather than in each spec means
a future change to the storage shape touches one file, not thirty.
"""

from __future__ import annotations

from core.features import FeatureState
from core.models import FeatureSwitch


def set_feature(key: str, state: str) -> FeatureSwitch:
    """Set ``key``'s feature state, creating the row if the spec never seeded one."""
    row, _ = FeatureSwitch.objects.update_or_create(feature_key=key, defaults={"state": state})
    return row


def turn_on(key: str) -> FeatureSwitch:
    """Put a feature fully On — today's behaviour, nav entry and routes alike."""
    return set_feature(key, FeatureState.ON)


def hide(key: str) -> FeatureSwitch:
    """Hide a feature: no nav entry anywhere, every route in the family 404s."""
    return set_feature(key, FeatureState.HIDDEN)


def coming_soon(key: str, message: str = "") -> FeatureSwitch:
    """Mark a feature Coming soon. A blank ``message`` exercises the default-text fallback."""
    row, _ = FeatureSwitch.objects.update_or_create(
        feature_key=key, defaults={"state": FeatureState.SOON, "message": message}
    )
    return row
