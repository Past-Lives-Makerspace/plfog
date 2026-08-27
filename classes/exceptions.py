"""Domain exceptions for the classes app."""

from __future__ import annotations


class RegistrationStateError(Exception):
    """Raised when a roster action is attempted against a registration in the wrong state.

    Views catch this and surface the message as an error toast (with a fresh row
    partial so stale rows self-heal) — never a 500. The message is member-staff
    facing plain language.
    """
