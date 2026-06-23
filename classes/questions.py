"""Shared helpers for CMS registration questions.

A single place that knows how to turn a :class:`~classes.models.RegistrationQuestion`
into a form field, harvest the answers back out, and resolve a person's remembered
answers for pre-fill. Used by both the public registration form and the standalone
onboarding "a few more questions" step so the two never drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import forms

from classes.models import RegistrationAnswer, RegistrationQuestion

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser
    from django.db.models import QuerySet


def active_questions() -> "QuerySet[RegistrationQuestion]":
    """The live CMS questions, in display order. Empty when none are configured."""
    return RegistrationQuestion.objects.filter(is_active=True)


def build_field(question: RegistrationQuestion) -> forms.Field:
    """Map a question's type to the matching form field (required flag follows the question)."""
    if question.question_type == RegistrationQuestion.QuestionType.LONG_TEXT:
        return forms.CharField(
            required=question.is_required,
            label=question.prompt,
            widget=forms.Textarea(attrs={"rows": 3}),
        )
    if question.question_type == RegistrationQuestion.QuestionType.YES_NO:
        return forms.TypedChoiceField(
            required=question.is_required,
            label=question.prompt,
            choices=[("", "Choose…"), ("yes", "Yes"), ("no", "No")],
        )
    if question.question_type == RegistrationQuestion.QuestionType.SINGLE_CHOICE:
        options = [(c, c) for c in (question.choices_json or [])]
        return forms.ChoiceField(
            required=question.is_required,
            label=question.prompt,
            choices=[("", "Choose…"), *options],
        )
    # SHORT_TEXT and any future fallthrough.
    return forms.CharField(
        required=question.is_required,
        label=question.prompt,
        max_length=500,
    )


def inject_fields(
    form: forms.BaseForm,
    questions: list[RegistrationQuestion],
    initial: dict[int, str] | None = None,
) -> None:
    """Add one ``custom_q_<pk>`` field per question to ``form``, seeding initial values."""
    initial = initial or {}
    for question in questions:
        field = build_field(question)
        if question.pk in initial:
            field.initial = initial[question.pk]
        form.fields[f"custom_q_{question.pk}"] = field


def collect_answers(form: forms.BaseForm, questions: list[RegistrationQuestion]) -> dict[int, str]:
    """Pull non-empty answers out of a cleaned form, keyed by question id."""
    answers: dict[int, str] = {}
    for question in questions:
        raw = form.cleaned_data.get(f"custom_q_{question.pk}")
        if raw in (None, ""):
            continue
        answers[question.pk] = str(raw)
    return answers


def _latest_answers(answer_qs: "QuerySet[RegistrationAnswer]") -> dict[int, str]:
    """Most-recent answer text per question id from a RegistrationAnswer queryset."""
    latest: dict[int, str] = {}
    for row in answer_qs.order_by("-registration__registered_at", "-id").values("question_id", "answer_text"):
        latest.setdefault(row["question_id"], row["answer_text"])
    return latest


def prefill_answers(
    user: "AbstractBaseUser | AnonymousUser | None",
    email: str = "",
) -> tuple[dict[int, str], bool]:
    """Resolve remembered answers for the active questions, plus whether any were found.

    Logged-in users draw from their stored profile answers, falling back per
    unanswered question to the latest answer across their own past registrations.
    Anonymous people are matched by the email they typed — so a returning guest
    sees their previous answers (which is why the caller surfaces a visible note,
    rather than filling them in silently).

    Args:
        user: The acting user, or an anonymous/None user for guests.
        email: The registrant email to match guests against.

    Returns:
        A ``({question_id: answer_text}, found_any)`` tuple, scoped to active questions.
    """
    active_ids = set(active_questions().values_list("id", flat=True))
    if not active_ids:
        return {}, False

    answers: dict[int, str] = {}
    if user is not None and user.is_authenticated:
        from core.models import UserProfile

        profile = UserProfile.objects.filter(user=user).first()
        if profile is not None:
            for key, value in profile.custom_question_answers.items():
                question_id = int(key)
                if question_id in active_ids and value:
                    answers[question_id] = value
        missing = active_ids - set(answers)
        if missing:
            from classes.account.selectors import _registrations_for

            history = _latest_answers(
                RegistrationAnswer.objects.filter(registration__in=_registrations_for(user), question_id__in=missing)
            )
            answers.update(history)
    elif email:
        history = _latest_answers(RegistrationAnswer.objects.filter(registration__email__iexact=email))
        answers.update({qid: text for qid, text in history.items() if qid in active_ids})

    return answers, bool(answers)
