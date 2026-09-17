"""BDD specs for ClassReviewDecisionForm: the reviewer's choice set and its notes rule.

The interesting one is ``allow_hold``. "Approve, hold for the room check" is a FORM value and
not a reviewer verdict: ``ClassApproval.Decision`` never gained a fourth member for it, so the
only place the hold exists is this choice set and the view that maps it.
"""

from __future__ import annotations

from classes.forms import ClassReviewDecisionForm


def _values(form: ClassReviewDecisionForm) -> list[str]:
    return [value for value, _label in form.fields["decision"].choices]


def describe_ClassReviewDecisionForm():
    def describe_the_choice_set():
        def it_offers_three_verdicts_by_default():
            assert _values(ClassReviewDecisionForm()) == ["approved", "changes_requested", "denied"]

        def it_adds_the_hold_when_the_lane_can_be_held():
            form = ClassReviewDecisionForm(allow_hold=True)
            assert _values(form) == ["approved", "approved_hold", "changes_requested", "denied"]

        def it_says_what_plain_approve_does_once_the_hold_sits_beside_it():
            """With both approvals on screen, "Approve" alone would not say which one it is."""
            labels = dict(ClassReviewDecisionForm(allow_hold=True).fields["decision"].choices)
            assert labels["approved"] == "Approve and publish"
            assert labels["approved_hold"] == "Approve, hold for the room check"
            assert dict(ClassReviewDecisionForm().fields["decision"].choices)["approved"] == "Approve"

        def it_keeps_the_hold_help_line_verbatim():
            """Human-approved copy. Changing a word of it is a decision, not a tidy-up."""
            assert ClassReviewDecisionForm.HOLD_HELP_TEXT == (
                "The class stays unpublished until the guild lead confirms the room is free."
            )

        def it_names_the_hold_value_without_borrowing_a_decision():
            from classes.models import ClassApproval

            assert ClassReviewDecisionForm.HOLD == "approved_hold"
            assert ClassReviewDecisionForm.HOLD not in ClassApproval.Decision.values

    def describe_validation():
        def it_accepts_the_hold_with_no_notes():
            form = ClassReviewDecisionForm({"decision": "approved_hold", "notes": ""}, allow_hold=True)
            assert form.is_valid()
            assert form.cleaned_data["decision"] == "approved_hold"

        def it_rejects_the_hold_on_a_lane_that_was_not_offered_it():
            form = ClassReviewDecisionForm({"decision": "approved_hold", "notes": ""})
            assert not form.is_valid()
            assert "decision" in form.errors

        def it_still_requires_notes_when_asking_for_changes():
            form = ClassReviewDecisionForm({"decision": "changes_requested", "notes": "  "}, allow_hold=True)
            assert not form.is_valid()
            assert form.errors["notes"] == ["Please leave a note so the instructor knows what to change."]

        def it_still_requires_notes_when_declining():
            form = ClassReviewDecisionForm({"decision": "denied", "notes": ""})
            assert not form.is_valid()
            assert "notes" in form.errors

        def it_leaves_notes_optional_on_a_plain_approval():
            form = ClassReviewDecisionForm({"decision": "approved", "notes": ""})
            assert form.is_valid()
