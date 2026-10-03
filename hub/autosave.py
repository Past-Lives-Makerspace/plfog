"""The JSON half of a form that saves itself as it is edited (#575).

A page whose forms autosave posts each form whole, with an ``X-Autosave: 1`` header, from
``static/js/guild_autosave.js``. The view keeps its plain path (redirect or re-render) for a
browser without the script and answers the header with JSON instead:

- a valid save is ``{"saved": true, "rows": {"<prefix>": [pk | null, ...]}}``, one pk per
  formset row in posted order so the script can stamp a new row's hidden ``id`` and never
  post it as new again;
- a refused save is a 422 ``{"errors": {"<html field name>": [...]}}`` plus the error toast
  the leadership editor's ``_refused`` sets in ``HX-Trigger``, keyed the way the inputs are
  named (``faq-2-question``), with a row's own errors under ``<prefix>-<i>-__all__``, a plain
  form's under ``__all__`` and a formset's under ``<prefix>-__all__``.
"""

from __future__ import annotations

from django.forms import BaseForm, BaseFormSet
from django.http import HttpRequest, JsonResponse

from hub.toast import trigger_toast


def wants_autosave(request: HttpRequest) -> bool:
    """True when the autosave script posted this request and expects JSON back."""
    return request.headers.get("X-Autosave") == "1"


def autosave_saved(rows: dict[str, list[int | None]] | None = None) -> JsonResponse:
    """The 200 for a save that landed, with each formset's row pks in posted order."""
    return JsonResponse({"saved": True, "rows": rows or {}})


def _is_deleted(formset: BaseFormSet, form: BaseForm) -> bool:
    """True for a row the post flagged for deletion (``deleted_forms`` is empty on an invalid formset)."""
    return bool(formset.can_delete and form.cleaned_data.get("DELETE"))


def formset_rows(formset: BaseFormSet) -> list[int | None]:
    """The pk of every form in a saved formset, in order; None for a deleted or skipped row."""
    rows: list[int | None] = []
    for form in formset.forms:
        if _is_deleted(formset, form):
            rows.append(None)
        else:
            rows.append(form.instance.pk)
    return rows


def _collect(form: BaseForm, errors: dict[str, list[str]]) -> None:
    """Add a form's errors keyed by html field name (``__all__`` becomes ``<prefix>-__all__`` on a row)."""
    for name, messages in form.errors.items():
        errors[form.add_prefix(name)] = [str(message) for message in messages]


def autosave_refused(*sources: BaseForm | BaseFormSet) -> JsonResponse:
    """422 with every error keyed by html field name, and the first one as an error toast.

    Raises:
        ValueError: when nothing passed in carries an error, which means the view called
            this on a valid form and the caller is wrong, not the member.
    """
    errors: dict[str, list[str]] = {}
    for source in sources:
        if isinstance(source, BaseFormSet):
            for form in source.forms:
                if not _is_deleted(source, form):
                    _collect(form, errors)
            non_form = [str(message) for message in source.non_form_errors()]
            if non_form:
                errors[f"{source.prefix}-__all__"] = non_form
        else:
            _collect(source, errors)
    if not errors:
        raise ValueError("autosave_refused needs at least one error to report.")
    first = next(iter(errors.values()))[0]
    response = JsonResponse({"errors": errors}, status=422)
    trigger_toast(response, f"Couldn't save that. {first}", "error")
    return response
