"""Notifications settings tab saves NotificationPreference rows."""

import re

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.events import settings_matrix
from core.models import NotificationPreference
from membership.models import Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _make_admin(client, username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    client.login(username=username, password="pw12345!")
    return user, member


def _make_officer(client, username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.GUILD_OFFICER
    member.save()
    client.login(username=username, password="pw12345!")
    return user, member


def _preview_as(client, role):
    session = client.session
    session["view_as_role"] = role
    session.save()


def _matrix_sections(response):
    return [section for section, _rows in response.context["notif_matrix"]]


def describe_notifications_tab():
    def it_saves_push_and_email_toggles(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        client.post(
            reverse("hub_user_settings"),
            {
                "form_id": "notifications",
                "pref__class_reminder__push": "on",
                "pref__tab_charged__email": "on",
            },
        )
        user = User.objects.get(username="m")
        assert NotificationPreference.objects.get(user=user, event_key="class_reminder", channel="push").enabled is True
        assert NotificationPreference.objects.get(user=user, event_key="tab_charged", channel="email").enabled is True

    def it_clears_unchecked_toggles(client):
        user = User.objects.create_user(username="m2", email="m2@example.com", password="pw12345!")
        NotificationPreference.objects.create(user=user, event_key="class_reminder", channel="push", enabled=True)
        client.login(username="m2", password="pw12345!")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # nothing checked
        assert (
            NotificationPreference.objects.get(user=user, event_key="class_reminder", channel="push").enabled is False
        )

    def it_renders_the_push_label_with_a_platform_tooltip(client):
        User.objects.create_user(username="m3", email="m3@example.com", password="pw12345!")
        client.login(username="m3", password="pw12345!")
        content = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
        assert "Push (Browser)" not in content  # renamed to plain "Push"
        # uses the canonical .pl-help hover bubble, not a browser title= tooltip
        assert "pl-help__bubble" in content
        assert "Android only for now. iOS coming soon." in content


def describe_build_matrix_staff_flag():
    def it_omits_the_staff_section_when_false():
        user, _member = _make_admin_matrix_user("flagadmin")
        with_staff = [s for s, _r in settings_matrix.build_matrix(user, include_staff_section=True)]
        without_staff = [s for s, _r in settings_matrix.build_matrix(user, include_staff_section=False)]
        assert settings_matrix.STAFF_SECTION in with_staff
        assert settings_matrix.STAFF_SECTION not in without_staff

    def it_never_renders_a_staff_only_channel_column_in_a_member_preview():
        # visible_channels must forward the flag: a channel only staff events offer must not
        # survive as a dead column when the staff section is hidden.
        user, _member = _make_admin_matrix_user("flagadmin2")
        member_view = settings_matrix.visible_channels(user, include_staff_section=False)
        # Every channel shown in the member-view preview is offered by some non-staff event.
        events = settings_matrix._visible_events(user, include_staff_section=False)
        for channel in member_view:
            assert any(event.channel(channel) is not None for event in events)


def _make_admin_matrix_user(username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    return user, member


def describe_view_as_staff_section():
    def it_shows_the_staff_section_to_an_admin_viewing_as_self(client):
        _make_admin(client, "vaself")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_admin_previews_as_member(client):
        _make_admin(client, "vamember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_admin_previews_as_guest(client):
        _make_admin(client, "vaguest")
        _preview_as(client, "guest")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_shows_the_staff_section_when_an_admin_previews_as_officer(client):
        _make_admin(client, "vaofficer")
        _preview_as(client, "guild_officer")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_hides_the_staff_section_when_an_officer_previews_as_member(client):
        _make_officer(client, "offviewmember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION not in _matrix_sections(response)

    def it_keeps_the_staff_section_for_a_guild_lead_whose_role_is_member(client):
        # A lead's fog_role is member, so include_staff stays True (the flag flips only when a
        # higher-role holder previews down) — they keep the staff rows their led_guilds grant.
        user = User.objects.create_user(username="leadmember", email="lead@example.com", password="pw12345!")
        member = Member.objects.get(user=user)
        GuildFactory(guild_lead=member)
        client.login(username="leadmember", password="pw12345!")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.STAFF_SECTION in _matrix_sections(response)

    def it_does_not_wipe_staff_prefs_when_saving_while_previewing_as_member(client):
        # The §5.2 wipe trap: the GET hid the staff section, so the POST omits its checkboxes.
        # save_matrix must receive include_staff_section=False and skip staff events, or every
        # staff pref would be written enabled=False.
        user, _member = _make_admin(client, "wipeadmin")
        NotificationPreference.objects.create(user=user, event_key="new_member_joined", channel="email", enabled=True)
        _preview_as(client, "member")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # no staff checkbox present
        pref = NotificationPreference.objects.get(user=user, event_key="new_member_joined", channel="email")
        assert pref.enabled is True  # untouched — not silently wiped

    def it_still_saves_staff_prefs_for_an_admin_viewing_as_self(client):
        # Control: viewing as self, the staff checkbox is present, so an unchecked POST clears it.
        user, _member = _make_admin(client, "selfsave")
        NotificationPreference.objects.create(user=user, event_key="new_member_joined", channel="email", enabled=True)
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # staff box unchecked
        pref = NotificationPreference.objects.get(user=user, event_key="new_member_joined", channel="email")
        assert pref.enabled is False


# The collapsed forced-email block: it renders the same way wherever the matrix renders,
# so these specs drive all three hosts of the partial.
ALWAYS_EMAILED_TITLE = '<span class="pl-disclosure__title">Always emailed</span>'
ALWAYS_EMAILED_HINT = (
    '<span class="pl-disclosure__hint">These always go out by email. '
    "Push and Discord can still be changed on the notices that offer them.</span>"
)


def _always_emailed_block(content):
    """The rendered markup of the Always-emailed section, summary through closing tag."""
    start = content.index('<div class="pl-notif-section" id="notif-always-emailed">')
    return content[start : content.index("</details>", start) + len("</details>")]


_CHECKBOX = re.compile(r'<input type="checkbox"[^>]*>', re.S)


def _browser_post_data(content):
    """The notification fields a real browser would submit from the rendered page.

    A checkbox contributes its name only when it is rendered, checked and not
    disabled. Building the POST this way instead of naming fields by hand is what
    lets a save-wipe spec detect a wipe: stop rendering the block's rows and the
    field stops being submitted, exactly as it would in a browser, while
    ``save_matrix`` still iterates every visible event and reads the absence as off.
    """
    data = {"form_id": "notifications"}
    for tag in _CHECKBOX.findall(content):
        if "disabled" in tag or "checked" not in tag:
            continue
        data[re.search(r'name="([^"]+)"', tag).group(1)] = "on"
    return data


def _summary_of(content):
    """Just the <summary> of the Always-emailed disclosure — its title, hint and chevron."""
    block = _always_emailed_block(content)
    return block[block.index("<summary") : block.index("</summary>")]


def describe_always_emailed_disclosure():
    def describe_the_heading():
        def it_reads_the_same_on_a_members_own_page(client):
            User.objects.create_user(username="ae_own", email="ae_own@example.com", password="pw12345!")
            client.login(username="ae_own", password="pw12345!")
            content = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            assert ALWAYS_EMAILED_TITLE in content
            assert ALWAYS_EMAILED_HINT in content

        def it_reads_the_same_on_the_no_login_token_page(client):
            from core.email_prefs import make_prefs_token

            user = User.objects.create_user(username="ae_token", email="ae_token@example.com")
            token = make_prefs_token(user)
            content = client.get(f"{reverse('hub_user_settings')}?tab=notifications&t={token}").content.decode()
            assert ALWAYS_EMAILED_TITLE in content
            assert ALWAYS_EMAILED_HINT in content

        def it_reads_the_same_when_an_admin_edits_someone_else(client):
            _make_admin(client, "ae_admin")
            target = User.objects.create_user(username="ae_target", email="ae_target@example.com").member
            content = client.get(reverse("hub_admin_member_edit", args=[target.pk])).content.decode()
            assert ALWAYS_EMAILED_TITLE in content
            assert ALWAYS_EMAILED_HINT in content

        def it_renders_one_identical_summary_on_every_surface(client):
            # The whole point of a pronoun-free heading: no matrix_self branch, so the
            # summary is byte-identical whether it is your page, an emailed link, or an
            # admin editing someone else. Compare them rather than trusting three
            # separate substring checks.
            from core.email_prefs import make_prefs_token

            own = User.objects.create_user(username="ae_same", email="ae_same@example.com", password="pw12345!")
            client.login(username="ae_same", password="pw12345!")
            own_page = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            target = User.objects.create_user(username="ae_same_t", email="ae_same_t@example.com").member
            _make_admin(client, "ae_same_admin")
            admin_page = client.get(reverse("hub_admin_member_edit", args=[target.pk])).content.decode()
            client.logout()
            token_page = client.get(
                f"{reverse('hub_user_settings')}?tab=notifications&t={make_prefs_token(own)}"
            ).content.decode()

            summaries = {_summary_of(page) for page in (own_page, admin_page, token_page)}
            assert len(summaries) == 1
            summary = summaries.pop()
            for pronoun in ("this member", "Always sent to you"):
                assert pronoun not in summary
            # Word-boundary, not a bare substring: "your" also lives inside "yourself"
            # and any number of future class names, and a spec that fails on those
            # reads as unrelated to the pronoun it is actually policing.
            assert not re.search(r"\byours?\b", summary, re.IGNORECASE)

    def describe_the_markup():
        def it_uses_the_documented_disclosure_component(client):
            User.objects.create_user(username="ae_markup", email="ae_markup@example.com", password="pw12345!")
            client.login(username="ae_markup", password="pw12345!")
            block = _always_emailed_block(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            assert '<details class="pl-disclosure">' in block
            assert '<summary class="pl-disclosure__summary">' in block
            assert '<span class="pl-disclosure__text">' in block
            # The chevron is not decoration: a summary with the native marker hidden and
            # no chevron reads as plain text and nobody clicks it (FRONTEND.md).
            assert '<span class="pl-disclosure__chevron" aria-hidden="true"></span>' in block
            assert '<div class="pl-disclosure__body">' in block

        def it_renders_collapsed(client):
            User.objects.create_user(username="ae_closed", email="ae_closed@example.com", password="pw12345!")
            client.login(username="ae_closed", password="pw12345!")
            block = _always_emailed_block(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            assert '<details class="pl-disclosure" open' not in block
            assert "<details open" not in block

        def it_keeps_every_button_out_of_the_summary(client):
            # A <button> inside a <summary> bubbles its click and toggles the disclosure,
            # so "All off" would also close the section. The bulk control lives in the
            # body, above the grid.
            User.objects.create_user(username="ae_btn", email="ae_btn@example.com", password="pw12345!")
            client.login(username="ae_btn", password="pw12345!")
            block = _always_emailed_block(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            summary = block[block.index("<summary") : block.index("</summary>")]
            assert "<button" not in summary
            body = block[block.index('<div class="pl-disclosure__body">') :]
            assert "pl-notif-bulk__btn" in body

        def it_leaves_the_section_reachable_by_the_bulk_control(client):
            # plNotifBulk walks up to .pl-notif-section; the <details> must sit INSIDE it,
            # or the section's own All on/off would flip the whole form.
            User.objects.create_user(username="ae_scope", email="ae_scope@example.com", password="pw12345!")
            client.login(username="ae_scope", password="pw12345!")
            content = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            block = _always_emailed_block(content)
            assert block.startswith('<div class="pl-notif-section" id="notif-always-emailed">')
            assert block.count('<div class="pl-notif-section"') == 1

    def describe_saving_while_collapsed():
        # A closed <details> still submits its inputs, so the writable cells inside the
        # block behave exactly as they did when they sat in their categories.
        def it_still_renders_the_writable_cells_as_live_checkboxes(client):
            User.objects.create_user(username="ae_live", email="ae_live@example.com", password="pw12345!")
            client.login(username="ae_live", password="pw12345!")
            block = _always_emailed_block(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            body = block[block.index('<div class="pl-disclosure__body">') :]
            assert 'name="pref__class_cancelled__push"' in body
            push_input = body[body.index('name="pref__class_cancelled__push"') :]
            push_input = push_input[: push_input.index(">")]
            assert "disabled" not in push_input

        def it_saves_a_writable_cell_from_inside_the_block(client):
            user = User.objects.create_user(username="ae_save", email="ae_save@example.com", password="pw12345!")
            client.login(username="ae_save", password="pw12345!")
            client.post(
                reverse("hub_user_settings"),
                {"form_id": "notifications", "pref__class_cancelled__push": "on"},
            )
            pref = NotificationPreference.objects.get(user=user, event_key="class_cancelled", channel="push")
            assert pref.enabled is True

        def it_saves_a_discord_cell_from_inside_the_block(client):
            # The block's hint promises Discord can still be changed on the notices that
            # offer it. Only a member who has linked Discord sees a live cell — for anyone
            # else it renders disabled and save_matrix skips it — so the promise is only
            # honest if this path works.
            user = User.objects.create_user(username="ae_disc", email="ae_disc@example.com", password="pw12345!")
            member = Member.objects.get(user=user)
            member.discord_user_id = "ae-disc-1"
            member.save(update_fields=["discord_user_id"])
            client.login(username="ae_disc", password="pw12345!")
            block = _always_emailed_block(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            cell = block[block.index('name="pref__class_cancelled__discord_dm"') :]
            assert "disabled" not in cell[: cell.index(">")]
            client.post(
                reverse("hub_user_settings"),
                {"form_id": "notifications", "pref__class_cancelled__discord_dm": "on"},
            )
            pref = NotificationPreference.objects.get(user=user, event_key="class_cancelled", channel="discord_dm")
            assert pref.enabled is True

        def it_does_not_wipe_a_writable_cell_the_member_left_checked(client):
            # Submit what the page actually rendered, not a hand-named field: naming it
            # by hand supplies the very input whose absence is the hazard, so the spec
            # could never fail for the reason it is named after. Hide the block instead
            # of collapsing it and this POST arrives without the cell, and the member's
            # checked push preference is silently zeroed.
            user = User.objects.create_user(username="ae_keep", email="ae_keep@example.com", password="pw12345!")
            NotificationPreference.objects.create(user=user, event_key="lease_expiring", channel="push", enabled=True)
            client.login(username="ae_keep", password="pw12345!")
            posted = _browser_post_data(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            client.post(reverse("hub_user_settings"), posted)
            pref = NotificationPreference.objects.get(user=user, event_key="lease_expiring", channel="push")
            assert pref.enabled is True

        def it_writes_no_row_for_a_forced_email_or_the_bell(client):
            # The locked cells render disabled, the browser omits them, and save_matrix
            # skips them anyway — exactly as it did before the block existed.
            user = User.objects.create_user(username="ae_forced", email="ae_forced@example.com", password="pw12345!")
            client.login(username="ae_forced", password="pw12345!")
            client.post(reverse("hub_user_settings"), {"form_id": "notifications"})
            locked = NotificationPreference.objects.filter(
                user=user,
                event_key__in=["class_cancelled", "lease_expiring", "member.invited"],
                channel__in=["email", "in_app"],
            )
            assert not locked.exists()
