"""BDD specs: every email shell's header link comes from MEMBER_BASE_URL, never a literal host.

A hardcoded ``https://members.pastlives.space`` sent every staging email's masthead to
production. The ten shells now use ``{% member_base_url %}``; this pins that, at the source and
through one rendered example.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.template.loader import render_to_string

_TEMPLATES = [
    "membership/emails/_base.html",
    "account/email/account_already_exists_message.html",
    "account/email/login_code_message.html",
    "account/email/unknown_account_message.html",
    "billing/email/charge_failed_admin.html",
    "billing/email/receipt.html",
    "classes/emails/confirmation.html",
    "classes/emails/instructor_new_registration.html",
    "classes/emails/reminder.html",
    "classes/emails/welcome.html",
]
_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates"


def describe_email_shell_headers():
    @pytest.mark.parametrize("name", _TEMPLATES)
    def it_links_the_masthead_through_the_member_base_url_tag(name):
        source = (_TEMPLATE_ROOT / name).read_text()
        assert "members.pastlives.space" not in source
        assert 'href="{% member_base_url %}"' in source
        assert "site_urls" in source.split("\n", 1)[0]

    def it_renders_the_configured_host(settings, db):
        settings.MEMBER_BASE_URL = "https://staging.pastlives.space"
        html = render_to_string("account/email/login_code_message.html", {"code": "123456"})
        assert 'href="https://staging.pastlives.space"' in html
        assert "members.pastlives.space" not in html
