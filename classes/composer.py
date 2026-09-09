"""The class composer's step map: one source of truth for which field lands on which step.

Three consumers must agree on it: the template that renders the tabs and the step
bodies, the view that decides which step to land on after a failed save, and the guard
spec that proves no form field went missing when the form was cut into five steps. They
all read :data:`COMPOSER_STEPS`; nothing restates the mapping anywhere else.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComposerStep:
    """One step of the composer.

    ``fields`` are the form field names rendered on the step; ``anchors`` are the extra DOM
    ids on the step that a readiness hint can jump to (``id_<field>`` ids are implied);
    ``readiness_labels`` are the :class:`~classes.models.ReadinessItem` labels the step owns,
    which drive the tab's completion mark.
    """

    number: int
    key: str
    tab_label: str
    heading: str
    fields: tuple[str, ...]
    anchors: tuple[str, ...] = ()
    readiness_labels: tuple[str, ...] = ()


COMPOSER_STEPS: tuple[ComposerStep, ...] = (
    ComposerStep(
        number=1,
        key="basics",
        tab_label="1. Basics",
        heading="The Basics",
        fields=("title", "category", "instructor", "description"),
        readiness_labels=("Description",),
    ),
    ComposerStep(
        number=2,
        key="photos",
        tab_label="2. Photos",
        heading="Photos And Video",
        fields=("image", "hero_crop", "card_focus", "video_url"),
        anchors=("hero-preview", "gallery-manager"),
        readiness_labels=("Hero photo", "Gallery photo"),
    ),
    ComposerStep(
        number=3,
        key="dates",
        tab_label="3. Dates & Price",
        heading="Dates, Seats And Price",
        fields=(
            "scheduling_model",
            "scheduling_type",
            "flexible_note",
            "capacity",
            "is_free",
            "price_cents",
            "member_discount_pct",
            "is_private",
            "private_for_name",
        ),
        anchors=("class-dates",),
        readiness_labels=("Dates", "Capacity"),
    ),
    ComposerStep(
        number=4,
        key="details",
        tab_label="4. Details",
        heading="What Students Need To Know",
        fields=(
            "prerequisites",
            "materials_included",
            "materials_to_bring",
            "safety_requirements",
            "age_minimum",
            "age_guardian_note",
        ),
    ),
    ComposerStep(number=5, key="review", tab_label="5. Review", heading="Review And Submit", fields=()),
)

STEP_COUNT = len(COMPOSER_STEPS)

# Formsets ride the same page; each one belongs to exactly one step.
FORMSET_STEPS: dict[str, int] = {"gallery": 2, "sessions": 3, "faq": 4}
FORMSET_LABELS: dict[str, str] = {"gallery": "Gallery", "sessions": "Dates", "faq": "FAQ"}
# Non field errors have no step of their own; the first step is where the user starts.
NON_FIELD_STEP = 1


def step_for_field(name: str) -> int:
    """The step number a form field renders on.

    Raises:
        KeyError: When no step names the field. A field that reaches this is a field the
            composer would silently drop, which is exactly what the guard spec exists to catch.
    """
    for step in COMPOSER_STEPS:
        if name in step.fields:
            return step.number
    raise KeyError(f"{name!r} is on no composer step")


def clamp_step(raw: str | None) -> int:
    """Read a ``?step=`` value as a step number, falling back to 1 for anything off the map."""
    try:
        number = int(raw or "")
    except ValueError:
        return 1
    return min(max(number, 1), STEP_COUNT)


def anchor_steps() -> dict[str, int]:
    """DOM id to step number, for the step 5 readiness hints that jump to a field.

    Built from the step map, so the ``id_<field>`` anchors and the named section anchors can
    never point at a step the field is not on.
    """
    mapping: dict[str, int] = {}
    for step in COMPOSER_STEPS:
        for field in step.fields:
            mapping[f"id_{field}"] = step.number
        for anchor in step.anchors:
            mapping[anchor] = step.number
    return mapping


def error_summary(form: Any, formsets: Mapping[str, Any]) -> list[tuple[ComposerStep, list[str]]]:
    """Per step, the labels of what failed, in step order. Steps with nothing wrong are absent.

    Form field errors map through :func:`step_for_field`; each formset maps through
    :data:`FORMSET_STEPS`; non field errors land on :data:`NON_FIELD_STEP`.
    """
    labels: dict[int, list[str]] = {}
    if not form.is_bound:
        return []
    for name in form.errors:
        if name == "__all__":
            labels.setdefault(NON_FIELD_STEP, [])
            continue
        labels.setdefault(step_for_field(name), []).append(str(form[name].label))
    for prefix, formset in formsets.items():
        if formset is None or not formset.is_bound or formset.is_valid():
            continue
        labels.setdefault(FORMSET_STEPS[prefix], []).append(FORMSET_LABELS[prefix])
    return [(step, labels[step.number]) for step in COMPOSER_STEPS if step.number in labels]


def error_steps(form: Any, formsets: Mapping[str, Any]) -> list[int]:
    """The step numbers carrying at least one error, ascending. Empty when nothing failed."""
    return [step.number for step, _labels in error_summary(form, formsets)]


def step_marks(readiness: Iterable[Any]) -> dict[int, bool]:
    """Step number to "every readiness item this step owns is ok", for the tab completion marks.

    Steps that own no readiness item (Details, Review) are absent: nothing on them blocks
    submission, and a checkmark there would train people to fill in optional fields.
    """
    ok_by_label = {item.label: item.ok for item in readiness}
    marks: dict[int, bool] = {}
    for step in COMPOSER_STEPS:
        if step.readiness_labels:
            marks[step.number] = all(ok_by_label[label] for label in step.readiness_labels)
    return marks
