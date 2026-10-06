"""Specs for core.phone: reading a phone number out of what a member typed."""

from __future__ import annotations

import pytest

from core.phone import phone_tel_number, tel_number


def describe_tel_number():
    def it_keeps_the_digits_and_a_leading_plus():
        assert tel_number("+1 (503) 555 0199") == "+15035550199"


def describe_phone_tel_number():
    """A contact is a phone number only when it holds nothing else."""

    @pytest.mark.parametrize(
        ("typed", "href"),
        [
            ("(503) 555 0199", "5035550199"),
            ("+1 503.555.0199", "+15035550199"),
            ("503-555-0199 x12", "5035550199;ext=12"),
            ("503 555 0199 / 503 555 0200", "5035550199"),
            ("555 0199", "5550199"),
            ("971-271-7020", "9712717020"),
            ("(503) 555-1212 x12", "5035551212;ext=12"),
            ("+1 503 555 1212", "+15035551212"),
            ("1 503 555 1212", "15035551212"),
            ("+44 20 7946 0958", "+442079460958"),
            ("+123 4567", "+1234567"),
            ("+123456789012345", "+123456789012345"),
        ],
    )
    def it_reads_a_value_that_is_only_a_phone_number(typed: str, href: str):
        assert phone_tel_number(typed) == href

    @pytest.mark.parametrize(
        "typed",
        [
            "Open Tue 5pm",
            "Call 503 555 0199",
            "studio 4B",
            "555 019",
            "2024",
            "10/12/2024",
            "1,000,000",
            "2024-2025",
            "123456789012345678",
            "+123456",
            "+1234567890123456",
            "@threadfox",
            "",
        ],
    )
    def it_gives_nothing_for_free_text_or_too_few_digits(typed: str):
        assert phone_tel_number(typed) == ""
