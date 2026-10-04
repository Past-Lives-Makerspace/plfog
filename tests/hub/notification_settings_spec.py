"""Notifications settings tab saves NotificationPreference rows."""

import html
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
    return [section.title for section in response.context["notif_matrix"]]


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
    def it_omits_the_admin_section_when_false():
        user, _member = _make_admin_matrix_user("flagadmin")
        with_staff = [s.title for s in settings_matrix.build_matrix(user, include_staff_section=True)]
        without_staff = [s.title for s in settings_matrix.build_matrix(user, include_staff_section=False)]
        assert settings_matrix.ADMIN_SECTION in with_staff
        assert settings_matrix.ADMIN_SECTION not in without_staff

    def it_never_renders_a_staff_only_channel_column_in_a_member_preview():
        # visible_channels must forward the flag: a channel only staff events offer must not
        # survive as a dead column when the staff section is hidden.
        user, _member = _make_admin_matrix_user("flagadmin2")
        member_view = settings_matrix.visible_channels(user, include_staff_section=False)
        # Every channel shown in the member-view preview is offered by some non-staff event.
        events = settings_matrix._visible_events(settings_matrix._staff_profile(user), include_staff_section=False)
        for channel in member_view:
            assert any(event.channel(channel) is not None for event in events)


def _make_admin_matrix_user(username):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save()
    return user, member


def describe_view_as_admin_section():
    def it_shows_the_admin_section_to_an_admin_viewing_as_self(client):
        _make_admin(client, "vaself")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION in _matrix_sections(response)

    def it_hides_the_admin_section_when_an_admin_previews_as_member(client):
        _make_admin(client, "vamember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION not in _matrix_sections(response)

    def it_hides_the_admin_section_when_an_admin_previews_as_guest(client):
        _make_admin(client, "vaguest")
        _preview_as(client, "guest")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION not in _matrix_sections(response)

    def it_shows_the_admin_section_when_an_admin_previews_as_officer(client):
        _make_admin(client, "vaofficer")
        _preview_as(client, "guild_officer")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION in _matrix_sections(response)

    def it_hides_the_admin_section_when_an_officer_previews_as_member(client):
        _make_officer(client, "offviewmember")
        _preview_as(client, "member")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION not in _matrix_sections(response)

    def it_keeps_the_admin_section_for_a_guild_lead_whose_role_is_member(client):
        # A lead's fog_role is member, so include_staff stays True (the flag flips only when a
        # higher-role holder previews down) — they keep the staff rows their led_guilds grant.
        user = User.objects.create_user(username="leadmember", email="lead@example.com", password="pw12345!")
        member = Member.objects.get(user=user)
        GuildFactory(guild_lead=member)
        client.login(username="leadmember", password="pw12345!")
        response = client.get(reverse("hub_user_settings") + "?tab=notifications")
        assert settings_matrix.ADMIN_SECTION in _matrix_sections(response)

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


_CHECKBOX = re.compile(r'<input type="checkbox"[^>]*>', re.S)


def _browser_post_data(content):
    """The notification fields a real browser would submit from the rendered page.

    A checkbox contributes its name only when it is rendered, checked and not
    disabled. Building the POST this way instead of naming fields by hand is what
    lets a save-wipe spec detect a wipe: stop rendering a row and its field stops
    being submitted, exactly as it would in a browser, while ``save_matrix`` still
    iterates every visible event and reads the absence as off.
    """
    data = {"form_id": "notifications"}
    for tag in _CHECKBOX.findall(content):
        name = re.search(r'name="(pref__[^"]+)"', tag)
        if name is None or "disabled" in tag or "checked" not in tag:
            continue  # another form's checkbox, or one the browser would not submit
        data[name.group(1)] = "on"
    return data


_SECTION_START = re.compile(r'<(?:section|div) class="pl-notif-section[^"]*" id="notif-([a-z0-9-]+)"')


def _section_ids(content):
    """The rendered sections' anchors, in page order."""
    return [match.group(1) for match in _SECTION_START.finditer(content)]


def _section_markup(content, slug):
    """The rendered markup of one section, from its opening tag to the next section's."""
    starts = list(_SECTION_START.finditer(content))
    for index, match in enumerate(starts):
        if match.group(1) == slug:
            end = (
                starts[index + 1].start()
                if index + 1 < len(starts)
                else content.index("pl-notif-actions", match.start())
            )
            return content[match.start() : end]
    raise AssertionError(f"no section notif-{slug} on the page")


def _notifications_card(content):
    """The notifications card only: intro to the end of the form, so the changelog every
    page renders (STANDARDS.md §8) can never satisfy or break an assertion."""
    start = content.index('<h2 class="pl-notif-title">')
    return content[start : content.index('<div class="pl-notif-actions">', start)]


def _input_tag(markup, name):
    start = markup.index(f'name="{name}"')
    return markup[markup.rindex("<input", 0, start) : markup.index(">", start)]


def _own_page(client, username, *, admin=False):
    if admin:
        _make_admin(client, username)
    else:
        User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
        client.login(username=username, password="pw12345!")
    return client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()


def _token_page(client, user):
    from core.email_prefs import make_prefs_token

    return client.get(f"{reverse('hub_user_settings')}?tab=notifications&t={make_prefs_token(user)}").content.decode()


def _admin_edit_page(client, target_member, admin_username):
    _make_admin(client, admin_username)
    return client.get(reverse("hub_admin_member_edit", args=[target_member.pk])).content.decode()


ADMIN_SLUG = "admin-permissions"
RETIRED_BLOCK_ANCHOR = 'id="notif-always-emailed"'


def describe_admin_permissions_on_every_surface():
    def it_renders_first_with_its_jump_chip_on_an_admins_own_page(client):
        content = _own_page(client, "ap_own", admin=True)
        assert _section_ids(content)[0] == ADMIN_SLUG
        assert '<section class="pl-notif-section pl-notif-section--admin" id="notif-admin-permissions"' in content
        assert 'class="pl-notif-jump__chip pl-notif-jump__chip--admin" href="#notif-admin-permissions"' in content

    def it_heads_each_permission_group_inside_it(client):
        section = _section_markup(_own_page(client, "ap_heads", admin=True), ADMIN_SLUG)
        assert '<div class="pl-notif-group pl-notif-block" id="notif-admin-admin">' in section
        assert '<h4 class="pl-notif-group__title">Admin</h4>' in section

    def it_carries_the_note_and_the_manage_link_at_its_top(client):
        section = _section_markup(_own_page(client, "ap_note", admin=True), ADMIN_SLUG)
        note = section.index("pl-notif-section-note")
        assert "Manage your admin duties" in section
        assert note < section.index("pl-notif-group__title")

    def it_names_the_failed_automation_alert_among_the_capability_alerts(client):
        section = _section_markup(_own_page(client, "ap_note_text", admin=True), ADMIN_SLUG)
        assert (
            "Class, space, discount, calendar, billing, and failed automation alerts are controlled by your "
            "admin capabilities."
        ) in section

    def it_counts_no_capability_alerts_when_an_admin_edits_a_member(client):
        # A count went stale once (it said five); the member-edit note names no number now.
        target = User.objects.create_user(username="ap_note_target", email="ap_note_target@example.com").member
        target.fog_role = Member.FogRole.ADMIN
        target.save()
        section = _section_markup(_admin_edit_page(client, target, "ap_note_editor"), ADMIN_SLUG)
        assert "The capability alerts follow their admin capabilities on the Permissions tab." in section

    def it_renders_when_an_admin_edits_another_admin(client):
        target = User.objects.create_user(username="ap_target", email="ap_target@example.com").member
        target.fog_role = Member.FogRole.ADMIN
        target.save()
        assert ADMIN_SLUG in _section_ids(_admin_edit_page(client, target, "ap_editor"))

    def it_renders_on_the_no_login_token_page_for_an_admin(client):
        user, _member = _make_admin_matrix_user("ap_token")
        assert ADMIN_SLUG in _section_ids(_token_page(client, user))

    def describe_for_a_plain_member():
        def it_renders_no_section_and_no_chip_on_their_own_page(client):
            content = _own_page(client, "ap_plain")
            assert ADMIN_SLUG not in _section_ids(content)
            assert 'href="#notif-admin-permissions"' not in content

        def it_renders_no_section_on_the_token_page(client):
            user = User.objects.create_user(username="ap_plain_token", email="ap_plain_token@example.com")
            assert ADMIN_SLUG not in _section_ids(_token_page(client, user))

        def it_renders_no_section_when_an_admin_edits_them(client):
            target = User.objects.create_user(username="ap_plain_t", email="ap_plain_t@example.com").member
            assert ADMIN_SLUG not in _section_ids(_admin_edit_page(client, target, "ap_plain_editor"))

        def it_renders_a_chip_for_every_real_section_and_nothing_else(client):
            content = _own_page(client, "ap_chips")
            chips = re.findall(r'class="pl-notif-jump__chip[^"]*" href="#notif-([a-z0-9-]+)"', content)
            assert chips == _section_ids(content)


def describe_padlocked_rows_on_every_surface():
    # The retired Always emailed block: its rows are back in their topics with a padlocked
    # Email cell, on all three hosts of the partial.
    def it_renders_no_always_emailed_block_anywhere(client):
        target = User.objects.create_user(username="pl_target", email="pl_target@example.com")
        own = _own_page(client, "pl_own")
        admin_page = _admin_edit_page(client, target.member, "pl_admin")
        client.logout()
        token = _token_page(client, target)
        for page in (own, admin_page, token):
            assert RETIRED_BLOCK_ANCHOR not in page
            assert '<details class="pl-disclosure">' not in page
        # The copy is gone from the notifications card too (the card only: the changelog
        # on every page may quote the old name).
        for page in (own, token):
            assert "Always emailed" not in _notifications_card(page)

    def it_explains_the_padlock_in_the_intro(client):
        intro = " ".join(_notifications_card(_own_page(client, "pl_intro")).split())
        assert "The bell always shows everything. A padlock means that notice is always emailed" in intro

    def it_renders_class_cancelled_in_classes_with_a_padlocked_email(client):
        classes = _section_markup(_own_page(client, "pl_classes"), "classes")
        email = _input_tag(classes, "pref__class_cancelled__email")
        assert "disabled" in email
        assert "checked" in email
        assert "pl-toggle__lock" in classes[classes.index('name="pref__class_cancelled__email"') :]

    def it_keeps_the_padlocked_rows_push_cell_live(client):
        classes = _section_markup(_own_page(client, "pl_push"), "classes")
        assert "disabled" not in _input_tag(classes, "pref__class_cancelled__push")

    def describe_saving():
        def it_saves_a_writable_cell_on_a_padlocked_row(client):
            user = User.objects.create_user(username="pl_save", email="pl_save@example.com", password="pw12345!")
            client.login(username="pl_save", password="pw12345!")
            client.post(
                reverse("hub_user_settings"),
                {"form_id": "notifications", "pref__class_cancelled__push": "on"},
            )
            pref = NotificationPreference.objects.get(user=user, event_key="class_cancelled", channel="push")
            assert pref.enabled is True

        def it_saves_a_discord_cell_on_a_padlocked_row(client):
            # Only a member who has linked Discord sees a live cell — for anyone else it
            # renders disabled and save_matrix skips it.
            user = User.objects.create_user(username="pl_disc", email="pl_disc@example.com", password="pw12345!")
            member = Member.objects.get(user=user)
            member.discord_user_id = "pl-disc-1"
            member.save(update_fields=["discord_user_id"])
            client.login(username="pl_disc", password="pw12345!")
            classes = _section_markup(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode(), "classes"
            )
            assert "disabled" not in _input_tag(classes, "pref__class_cancelled__discord_dm")
            client.post(
                reverse("hub_user_settings"),
                {"form_id": "notifications", "pref__class_cancelled__discord_dm": "on"},
            )
            pref = NotificationPreference.objects.get(user=user, event_key="class_cancelled", channel="discord_dm")
            assert pref.enabled is True

        def it_does_not_wipe_a_writable_cell_the_member_left_checked(client):
            # Submit what the page actually rendered, not a hand-named field: naming it
            # by hand supplies the very input whose absence is the hazard, so the spec
            # could never fail for the reason it is named after.
            user = User.objects.create_user(username="pl_keep", email="pl_keep@example.com", password="pw12345!")
            NotificationPreference.objects.create(user=user, event_key="lease_expiring", channel="push", enabled=True)
            client.login(username="pl_keep", password="pw12345!")
            posted = _browser_post_data(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            client.post(reverse("hub_user_settings"), posted)
            pref = NotificationPreference.objects.get(user=user, event_key="lease_expiring", channel="push")
            assert pref.enabled is True

        def it_does_not_wipe_an_admins_rows_saved_from_their_own_page(client):
            # The same trap for Admin / Permissions: its rows render and post like any other.
            user, _member = _make_admin(client, "pl_admin_keep")
            NotificationPreference.objects.create(
                user=user, event_key="new_member_joined", channel="email", enabled=True
            )
            posted = _browser_post_data(
                client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            )
            client.post(reverse("hub_user_settings"), posted)
            pref = NotificationPreference.objects.get(user=user, event_key="new_member_joined", channel="email")
            assert pref.enabled is True

        def it_writes_no_row_for_a_forced_email_or_the_bell(client):
            # The locked cells render disabled, the browser omits them, and save_matrix
            # skips them anyway.
            user = User.objects.create_user(username="pl_forced", email="pl_forced@example.com", password="pw12345!")
            client.login(username="pl_forced", password="pw12345!")
            client.post(reverse("hub_user_settings"), {"form_id": "notifications"})
            locked = NotificationPreference.objects.filter(
                user=user,
                event_key__in=["class_cancelled", "lease_expiring", "member.invited"],
                channel__in=["email", "in_app"],
            )
            assert not locked.exists()


def _aria_labels(markup):
    """The bulk buttons' accessible names, unescaped ("Spaces & Equipment" renders &amp;)."""
    return {html.unescape(label) for label in re.findall(r'aria-label="(Turn (?:on|off) [^"]+)"', markup)}


def describe_bulk_controls():
    def describe_the_everything_bar():
        def it_offers_all_and_each_channel_a_plain_member_can_flip(client):
            labels = _aria_labels(_notifications_card(_own_page(client, "bk_page")))
            for action in ("on", "off"):
                assert f"Turn {action} all channels for everything" in labels
                assert f"Turn {action} Email for everything" in labels
                assert f"Turn {action} Push for everything" in labels
            # Discord is not linked, so no Discord cell can change and no control shows.
            assert "Turn off Discord for everything" not in labels

        def it_offers_discord_once_the_member_links_it(client):
            user = User.objects.create_user(username="bk_disc", email="bk_disc@example.com", password="pw12345!")
            member = Member.objects.get(user=user)
            member.discord_user_id = "bk-disc-1"
            member.save(update_fields=["discord_user_id"])
            client.login(username="bk_disc", password="pw12345!")
            content = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
            assert "Turn off Discord for everything" in _aria_labels(_notifications_card(content))

        def it_says_nothing_changes_until_save(client):
            card = _notifications_card(_own_page(client, "bk_hint"))
            assert "Every section on this page. Nothing changes until you press Save." in card

    def describe_every_grid():
        def it_offers_exactly_the_channels_its_rows_let_you_flip(client):
            # Walk the real page: for every grid, each channel has an On and Off button if
            # and only if the grid's editable channels include it, and never the bell.
            User.objects.create_user(username="bk_grid", email="bk_grid@example.com", password="pw12345!")
            client.login(username="bk_grid", password="pw12345!")
            response = client.get(reverse("hub_user_settings") + "?tab=notifications")
            content = response.content.decode()
            labels = dict(response.context["notif_channels"])
            for section in response.context["notif_matrix"]:
                markup = _aria_labels(_section_markup(content, section.slug))
                for block in section.blocks:
                    scope = block.heading or section.title
                    for channel, label in labels.items():
                        offered = f"Turn off {label} for {scope}" in markup
                        assert offered == (channel in block.editable_channels), (scope, label)
                    assert (f"Turn on all channels for {scope}" in markup) == bool(block.editable_channels), scope

        def it_renders_no_bulk_row_for_a_grid_with_nothing_to_flip(client):
            User.objects.create_user(username="bk_none", email="bk_none@example.com", password="pw12345!")
            client.login(username="bk_none", password="pw12345!")
            response = client.get(reverse("hub_user_settings") + "?tab=notifications")
            content = response.content.decode()
            locked = [section for section in response.context["notif_matrix"] if not section.editable_channels]
            assert locked, "expected a topic whose every cell is padlocked (Membership)"
            for section in locked:
                markup = _section_markup(content, section.slug)
                assert "pl-notif-matrix__bulk" not in markup
                assert "pl-notif-onoff" not in markup

        def it_names_billing_in_its_controls(client):
            markup = _aria_labels(_section_markup(_own_page(client, "bk_bill"), "billing"))
            assert "Turn off Email for Billing" in markup
            assert "Turn on Push for Billing" in markup

        def it_never_offers_a_bell_control(client):
            assert not [label for label in _aria_labels(_own_page(client, "bk_bell", admin=True)) if "Bell" in label]

    def describe_admin_permissions():
        def it_has_a_whole_section_bar_and_one_set_per_group(client):
            section = _aria_labels(_section_markup(_own_page(client, "bk_admin", admin=True), ADMIN_SLUG))
            assert "Turn off Email for Admin / Permissions" in section
            assert "Turn off all channels for Admin / Permissions" in section
            assert "Turn off Email for Admin" in section

    def it_tags_every_checkbox_with_its_channel(client):
        card = _notifications_card(_own_page(client, "bk_tags", admin=True))
        boxes = re.findall(r'<input type="checkbox"\s+name="pref__[^"]+__([a-z_]+)"\s+data-channel="([a-z_]+)"', card)
        assert boxes
        assert all(suffix == channel for suffix, channel in boxes)
        assert len(boxes) == card.count('<input type="checkbox"')

    def it_keeps_the_dirty_guard_on_the_members_own_page(client):
        card = _notifications_card(_own_page(client, "bk_dirty"))
        assert 'data-dirty-key="notifications"' in card
        assert '@change="dirty.notifications = true"' in card

    def it_renders_the_controls_on_the_token_page_and_the_admin_tab_too(client):
        target = User.objects.create_user(username="bk_t", email="bk_t@example.com")
        admin_page = _admin_edit_page(client, target.member, "bk_admin_editor")
        client.logout()
        token = _token_page(client, target)
        for page in (admin_page, token):
            assert "Turn off Email for everything" in _aria_labels(page)
            assert "Turn off Email for Billing" in _aria_labels(page)
