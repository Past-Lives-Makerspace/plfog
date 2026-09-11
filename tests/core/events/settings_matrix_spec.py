"""The settings matrix: which channels it surfaces, which section a row lands in, and
how the six sibling row groups render and save as one setting each."""

import pytest
from django.contrib.auth.models import User

from core.events import settings_matrix
from core.events.registry import Channel, all_events, get_event
from core.models import NotificationPreference
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _sections(user):
    return [section for section, _rows in settings_matrix.build_matrix(user)]


def _section_of(user, event_key):
    """Return the section a given event row renders under, or None if not shown."""
    for section, rows in settings_matrix.build_matrix(user):
        if any(row.event_key == event_key for row in rows):
            return section
    return None


def describe_visible_channels():
    def it_includes_channels_events_offer():
        user = User.objects.create_user(username="vc", email="vc@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.IN_APP in channels
        assert Channel.EMAIL in channels
        assert Channel.PUSH in channels

    def it_drops_channels_no_event_offers():
        # SCHEDULED_EMAIL and DIGEST are declared shells no event uses yet — they
        # must not render as dead, all-"—" columns on the settings page.
        user = User.objects.create_user(username="vc2", email="vc2@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.SCHEDULED_EMAIL not in channels
        assert Channel.DIGEST not in channels

    def it_keeps_user_channel_display_order():
        user = User.objects.create_user(username="vc3", email="vc3@example.com")
        channels = settings_matrix.visible_channels(user)
        assert channels == [c for c in settings_matrix.USER_CHANNELS if c in channels]


def describe_build_matrix():
    def it_emits_one_cell_per_visible_channel():
        user = User.objects.create_user(username="vc4", email="vc4@example.com")
        expected = len(settings_matrix.visible_channels(user))
        matrix = settings_matrix.build_matrix(user)
        assert matrix  # categories exist
        for _category, rows in matrix:
            for row in rows:
                assert len(row.cells) == expected


def describe_channel_labels():
    def it_labels_push_plainly():
        # Renamed from "Push (Browser)" now that native push is the primary carrier.
        assert settings_matrix.CHANNEL_LABELS[Channel.PUSH] == "Push"


def describe_push_defaults():
    def it_offers_push_on_every_row_with_a_mix_of_defaults():
        user = User.objects.create_user(username="pd", email="pd@example.com")
        push_cells = [
            cell
            for _section, rows in settings_matrix.build_matrix(user)
            for row in rows
            for cell in row.cells
            if cell.channel is Channel.PUSH and cell.present
        ]
        assert push_cells  # every in-app row offers a push toggle
        # important events default on, routine ones default off — both appear for a member
        assert any(cell.enabled for cell in push_cells)
        assert any(not cell.enabled for cell in push_cells)


def describe_staff_section():
    # A pure-capability approval event (routes to SPACE_APPROVERS): visible ONLY to a
    # holder of the Space capability, and grouped under Staff & leadership, not "Spaces".
    CAP_EVENT = "space.lease_requested"
    # An admin-only alert routed by role (FOG_ADMINS), not by a capability.
    ADMIN_ALERT_EVENT = "new_member_joined"
    # A composite event (GUILD_LEADERSHIP_OR_CLASS_APPROVERS): visible to guild leadership
    # OR a Class-capability holder.
    COMPOSITE_EVENT = "class_review_requested"

    def _member_user(username, **member_kwargs):
        # create_user auto-provisions a plain Member (fog_role=member); fetch and
        # mutate it rather than creating a second member for the same user.
        user = User.objects.create_user(username=username, email=f"{username}@example.com")
        member = Member.objects.get(user=user)
        if member_kwargs:
            for field, value in member_kwargs.items():
                setattr(member, field, value)
            member.save()
        return user

    def _grant(user, capability):
        Member.objects.get(user=user).admin_capabilities.create(capability=capability)

    def describe_a_plain_member():
        def it_never_sees_the_staff_section(db):
            user = _member_user("plain1")  # default fog_role=member, no capabilities/leadership
            assert settings_matrix.STAFF_SECTION not in _sections(user)

        def it_sees_none_of_the_staff_events(db):
            user = _member_user("plain2")
            assert _section_of(user, CAP_EVENT) is None
            assert _section_of(user, ADMIN_ALERT_EVENT) is None

        def it_still_sees_its_member_categories(db):
            user = _member_user("plain3")
            assert _sections(user)  # member-facing categories remain

    def describe_a_user_with_no_member():
        def it_never_sees_the_staff_section(db):
            user = User.objects.create_user(username="nomember", email="nomember@example.com")
            Member.objects.filter(user=user).delete()
            assert not Member.objects.filter(user=user).exists()
            assert settings_matrix.STAFF_SECTION not in _sections(user)

    def describe_a_fog_admin_without_capabilities():
        def it_sees_the_section_for_admin_alerts_rendered_last(db):
            user = _member_user("admin1", fog_role=Member.FogRole.ADMIN)
            sections = _sections(user)
            assert sections[-1] == settings_matrix.STAFF_SECTION
            assert _section_of(user, ADMIN_ALERT_EVENT) == settings_matrix.STAFF_SECTION

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("admin2", fog_role=Member.FogRole.ADMIN)
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_capability_holder():
        def it_sees_and_groups_the_capabilitys_event(db):
            user = _member_user("cap1")  # plain member granted one duty
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _section_of(user, CAP_EVENT) == settings_matrix.STAFF_SECTION

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("cap2")
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _section_of(user, "class_validation_requested") is None

    def describe_an_equipment_manager():
        # equipment.reservation_made routes to EQUIPMENT_MANAGERS — the three manage
        # tiers (per-equipment staff row / guild leadership / EQUIPMENT capability)
        # each see the row; a plain member never does. Page == delivery.
        EQUIPMENT_EVENT = "equipment.reservation_made"

        def it_shows_the_row_to_a_per_equipment_staff_row_holder(db):
            from tests.membership.factories import EquipmentStaffMembershipFactory

            user = _member_user("equipmgr1")
            EquipmentStaffMembershipFactory(member=Member.objects.get(user=user))
            assert _section_of(user, EQUIPMENT_EVENT) == settings_matrix.STAFF_SECTION

        def it_shows_the_row_to_an_equipment_capability_holder(db):
            user = _member_user("equipmgr2")
            _grant(user, AdminCapability.Capability.EQUIPMENT)
            assert _section_of(user, EQUIPMENT_EVENT) == settings_matrix.STAFF_SECTION

        def it_hides_the_row_from_a_plain_member(db):
            user = _member_user("equipmgr3")
            assert _section_of(user, EQUIPMENT_EVENT) is None

        def it_shows_orientation_requested_to_equipment_managers_too(db):
            # The composed GUILD_ORIENTERS_OR_EQUIPMENT_MANAGERS recipient: an
            # equipment staff-row holder and an EQUIPMENT capability holder each see
            # the row; a plain member never does. Page == delivery.
            from tests.membership.factories import EquipmentStaffMembershipFactory

            staffed = _member_user("equiporient1")
            EquipmentStaffMembershipFactory(member=Member.objects.get(user=staffed))
            assert _section_of(staffed, "orientation_requested") == settings_matrix.STAFF_SECTION
            holder = _member_user("equiporient2")
            _grant(holder, AdminCapability.Capability.EQUIPMENT)
            assert _section_of(holder, "orientation_requested") == settings_matrix.STAFF_SECTION
            plain = _member_user("equiporient3")
            assert _section_of(plain, "orientation_requested") is None

    def describe_a_guild_lead():
        def it_sees_composite_leadership_events_but_not_unheld_capabilities(db):
            user = _member_user("lead1")
            GuildFactory(guild_lead=Member.objects.get(user=user))
            assert _section_of(user, COMPOSITE_EVENT) == settings_matrix.STAFF_SECTION
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_guild_officer():
        # voting.officers_closing_soon routes to ALL_GUILD_LEADS, whose resolver filters to
        # active members — so the row must track Member.status, not just the role.
        ALL_LEADS_EVENT = "voting.officers_closing_soon"

        def it_shows_all_guild_leads_rows_to_an_active_officer(db):
            user = _member_user("officer1", fog_role=Member.FogRole.GUILD_OFFICER)
            assert _section_of(user, ALL_LEADS_EVENT) == settings_matrix.STAFF_SECTION

        def it_hides_all_guild_leads_rows_from_a_former_officer(db):
            user = _member_user("officer2", fog_role=Member.FogRole.GUILD_OFFICER, status=Member.Status.FORMER)
            assert _section_of(user, ALL_LEADS_EVENT) is None


def describe_row_groups():
    # The shipping copy, pinned here rather than read back off ROW_GROUPS: a spec that
    # asserts row.label == group.label only proves the plumbing. These strings are what a
    # member reads, so a reword has to come past this file.
    EXPECTED_GROUPS = {
        "group.event_decision": (
            "Updates to your event",
            "Your proposed event was approved, sent back for changes, or declined.",
            "Events",
        ),
        "group.class_decision": (
            "Updates to your class",
            "A reviewer approved your class or asked you for changes.",
            "Teaching",
        ),
        "group.announcement_decision": (
            "Updates to your announcement",
            "Your proposed announcement was approved, sent back for changes, or declined.",
            "Guilds",
        ),
        "group.space_request_decision": (
            "Updates to your space request",
            "Your studio or cubby request was approved or declined.",
            "Spaces & Equipment",
        ),
        "group.instructor_application_decision": (
            "Updates to your request to host a workshop",
            "Your request to host a workshop was approved or declined.",
            "Classes",
        ),
        "group.waitlist_promotion": (
            "Added from the waitlist",
            "Staff moved you off the waitlist into a class. If the class is paid, this carries your payment link.",
            "Classes",
        ),
    }

    def _rows_by_key(user):
        return {row.event_key: (section, row) for section, rows in settings_matrix.build_matrix(user) for row in rows}

    def it_renders_each_group_as_exactly_one_row_with_its_copy_in_its_section(db):
        user = User.objects.create_user(username="grp1", email="grp1@example.com")
        matrix = settings_matrix.build_matrix(user)
        keys = [row.event_key for _section, rows in matrix for row in rows]
        rows_by_key = _rows_by_key(user)
        for group_id, (label, description, section) in EXPECTED_GROUPS.items():
            assert keys.count(group_id) == 1, f"{group_id} should render exactly once"
            got_section, row = rows_by_key[group_id]
            assert (row.label, row.description, got_section) == (label, description, section)

    def it_renders_no_row_for_a_grouped_member_key(db):
        # The siblings are folded in, not rendered alongside their group.
        user = User.objects.create_user(username="grp2", email="grp2@example.com")
        rows_by_key = _rows_by_key(user)
        for group in settings_matrix.ROW_GROUPS:
            for key in group.event_keys:
                assert key not in rows_by_key

    def it_covers_every_declared_group(db):
        # Guards the pinned table above against a seventh group arriving unpinned.
        assert {group.group_id for group in settings_matrix.ROW_GROUPS} == set(EXPECTED_GROUPS)

    def it_keeps_each_group_row_where_its_first_member_sat(db):
        # Registry order is the page's row order; a group takes the slot of its first
        # member and its later siblings vanish. Pinned as a LITERAL, not rebuilt from
        # _visible_events/_section_for/_post_key_for: an expectation computed from the
        # same helpers build_matrix calls moves with every bug in them and can only ever
        # catch a re-sort. Classes is the section worth pinning — two groups, one mid-list
        # and one last, with ungrouped siblings on both sides of the first.
        user = User.objects.create_user(username="grp3", email="grp3@example.com")
        classes = dict(settings_matrix.build_matrix(user))["Classes"]
        assert [row.event_key for row in classes] == [
            "class_reminder",
            "registration_confirmed",
            "waitlist_spot_available",
            "waitlist_confirmed",
            "class_announcement",
            "group.waitlist_promotion",
            "registration_removed",
            "registration_moved",
            "group.instructor_application_decision",
        ]

    def describe_group_membership():
        def it_never_swallows_a_sibling_that_shares_the_prefix(db):
            # The prefix a group's keys share is NOT its scope. Scoped by prefix, "event."
            # would drag in event.submitted, event.reminder, event.happening_now and
            # event.community_published; "space." would drag in the lease events;
            # event.submitted and guild_announcement.submitted are approval REQUESTS
            # routed to staff through a different emit helper. Every one of those has to
            # keep posting under its own key.
            #
            # Asserted through _post_key_for, never against _GROUP_BY_EVENT_KEY: that dict
            # is a comprehension over the very keys the group declares, so reading it back
            # can only restate its own construction. _post_key_for is where a prefix match
            # would actually be written, so this is the call that fails when one is.
            checked = 0
            for group in settings_matrix.ROW_GROUPS:
                assert isinstance(group.event_keys, tuple)
                assert len(group.event_keys) >= 2
                namespaces = {key.rsplit(".", 1)[0] + "." for key in group.event_keys if "." in key}
                for namespace in namespaces:
                    for event in all_events():
                        if not event.key.startswith(namespace) or event.key in group.event_keys:
                            continue
                        checked += 1
                        assert settings_matrix._post_key_for(event) == event.key
            assert checked >= 4, "expected several same-namespace keys a prefix match would have swallowed"
            for key in ("event.submitted", "guild_announcement.submitted"):
                assert settings_matrix._post_key_for(get_event(key)) == key

        def it_still_renders_a_submitted_sibling_as_its_own_staff_row(db):
            user = User.objects.create_user(username="grp4", email="grp4@example.com")
            member = Member.objects.get(user=user)
            member.fog_role = Member.FogRole.ADMIN
            member.save()
            member.admin_capabilities.create(capability=AdminCapability.Capability.EVENTS_APPROVER)
            assert _section_of(user, "event.submitted") == settings_matrix.STAFF_SECTION

        def it_registers_no_event_key_in_the_group_namespace(db):
            # The `group.` prefix is reserved for row groups; an event that claimed one
            # would collide with a group's checkbox names.
            assert [event.key for event in all_events() if event.key.startswith("group.")] == []

        def it_gives_every_member_of_a_group_one_channel_signature(db):
            # A group renders ONE set of cells, read off its first member. If a sibling
            # ever declared a different channel or default, the row would silently lie
            # about it — so the suite fails here instead.
            for group in settings_matrix.ROW_GROUPS:
                signatures = {
                    frozenset((spec.channel, spec.default) for spec in get_event(key).channels)
                    for key in group.event_keys
                }
                assert len(signatures) == 1, f"{group.group_id} members disagree on channels"

        def it_puts_every_member_of_a_group_in_one_section(db):
            for group in settings_matrix.ROW_GROUPS:
                sections = {settings_matrix._section_for(get_event(key)) for key in group.event_keys}
                assert len(sections) == 1, f"{group.group_id} members disagree on section"

        def it_puts_every_member_of_a_group_on_one_recipient(db):
            # The section check above only catches a group that straddles the member/staff
            # split. Two staff recipients both land in STAFF_SECTION, so it passes them —
            # and then build_matrix reads the row's state from EVERY key in event_keys
            # while save_matrix writes only the ones _visible_events returns for this
            # viewer. A holder of one duty but not the other would see a row stuck on the
            # hidden sibling's value: check the box, save, watch it come back unchecked.
            for group in settings_matrix.ROW_GROUPS:
                recipients = {get_event(key).recipient for key in group.event_keys}
                assert len(recipients) == 1, f"{group.group_id} members disagree on recipient"

    def describe_cell_state():
        GROUP = "group.event_decision"
        MEMBER_KEYS = ("event.approved", "event.changes_requested", "event.declined")

        def _email_cell(user):
            for _section, rows in settings_matrix.build_matrix(user):
                for row in rows:
                    if row.event_key == GROUP:
                        return next(cell for cell in row.cells if cell.channel is Channel.EMAIL)
            raise AssertionError(f"{GROUP} row not rendered")

        def it_renders_on_when_every_member_key_is_on(db):
            user = User.objects.create_user(username="cell1", email="cell1@example.com")
            for key in MEMBER_KEYS:
                NotificationPreference.objects.create(user=user, event_key=key, channel="email", enabled=True)
            assert _email_cell(user).enabled is True

        def it_renders_off_when_any_one_member_key_is_off(db):
            # Any opt-out wins: the page never promises mail a sibling would not send.
            user = User.objects.create_user(username="cell2", email="cell2@example.com")
            for key in MEMBER_KEYS:
                NotificationPreference.objects.create(user=user, event_key=key, channel="email", enabled=True)
            NotificationPreference.objects.filter(user=user, event_key="event.declined").update(enabled=False)
            assert _email_cell(user).enabled is False

    def describe_save_matrix():
        def it_writes_every_member_key_when_a_group_cell_is_on(db):
            user = User.objects.create_user(username="save1", email="save1@example.com")
            settings_matrix.save_matrix(user, {"pref__group.event_decision__email": "on"})
            written = dict(
                NotificationPreference.objects.filter(
                    user=user, event_key__in=["event.approved", "event.changes_requested", "event.declined"]
                )
                .filter(channel="email")
                .values_list("event_key", "enabled")
            )
            assert written == {"event.approved": True, "event.changes_requested": True, "event.declined": True}

        def it_writes_every_member_key_when_a_group_cell_is_off(db):
            user = User.objects.create_user(username="save2", email="save2@example.com")
            for key in ("event.approved", "event.changes_requested", "event.declined"):
                NotificationPreference.objects.create(user=user, event_key=key, channel="email", enabled=True)
            settings_matrix.save_matrix(user, {})  # nothing checked
            written = dict(
                NotificationPreference.objects.filter(
                    user=user,
                    event_key__in=["event.approved", "event.changes_requested", "event.declined"],
                    channel="email",
                ).values_list("event_key", "enabled")
            )
            assert written == {"event.approved": False, "event.changes_requested": False, "event.declined": False}

        def it_never_writes_a_group_id_as_an_event_key(db):
            # Save every group row at once and read the keys back: grouping lives between
            # the form and the table, and NotificationPreference keeps event keys only.
            user = User.objects.create_user(username="save3", email="save3@example.com")
            posted = {
                settings_matrix.field_name(group.group_id, channel): "on"
                for group in settings_matrix.ROW_GROUPS
                for channel in settings_matrix.USER_CHANNELS
            }
            settings_matrix.save_matrix(user, posted)
            keys = set(NotificationPreference.objects.filter(user=user).values_list("event_key", flat=True))
            assert keys, "the save wrote nothing"
            assert not [key for key in keys if key.startswith("group.")]
            # And the member keys really did get their rows.
            assert {key for group in settings_matrix.ROW_GROUPS for key in group.event_keys} <= keys

        def it_keeps_one_preference_row_per_member_key_and_channel(db):
            # Grouping changes no row count in the table: saving twice upserts, and each
            # member key still owns its own row per channel.
            user = User.objects.create_user(username="save4", email="save4@example.com")
            posted = {"pref__group.waitlist_promotion__email": "on"}
            settings_matrix.save_matrix(user, posted)
            settings_matrix.save_matrix(user, posted)
            rows = NotificationPreference.objects.filter(
                user=user, event_key__in=["waitlist_promoted", "waitlist_promoted_pay"], channel="email"
            )
            assert rows.count() == 2
            assert all(row.enabled for row in rows)


def describe_always_emailed_section():
    # The inventory, pinned exactly (the pattern registry_spec uses for
    # _PUSH_ON_BY_DEFAULT): the rule below says what belongs in the block, and this says
    # what is in it today, so adding or removing a forced email is a deliberate edit.
    ALWAYS_EMAILED_KEYS = {
        "member.invited",
        "member.login_invite",
        "discord_guilds_imported",
        "class_cancelled",
        "refund_issued",
        "tab_entry_added",
        "tab_approaching_limit",
        "lease_expiring",
        "equipment.reservation_confirmed",
        "equipment.reservation_cancelled_by_manager",
    }

    def describe_the_rule():
        def it_collects_an_event_whose_email_is_forced(db):
            # class_cancelled is a Classes event; a forced email moves it out of Classes.
            event = get_event("class_cancelled")
            assert event.channel(Channel.EMAIL).is_forced
            assert settings_matrix._section_for(event) == settings_matrix.ALWAYS_EMAILED_SECTION

        def it_leaves_an_event_without_a_forced_email_in_its_category(db):
            # class_reminder is the control: same category, same recipient shape, email
            # default OFF rather than FORCED.
            event = get_event("class_reminder")
            assert not event.channel(Channel.EMAIL).is_forced
            assert settings_matrix._section_for(event) == "Classes"

        def it_registers_no_category_named_after_the_block(db):
            # Symmetric with the `group.` namespace guard: ALWAYS_EMAILED_SECTION is a
            # section name the code assigns, never a category an event may declare. One
            # that did would be swept into the tail ordering with the forced emails and
            # then rendered by the template's literal `category == 'Always emailed'`
            # branch, putting ordinary opt-in rows inside the collapsed disclosure under
            # a heading that says they cannot be turned off.
            collisions = [
                event.key for event in all_events() if event.category == settings_matrix.ALWAYS_EMAILED_SECTION
            ]
            assert collisions == []

        def it_ignores_an_event_that_declares_no_email_at_all(db):
            eventless = [event for event in all_events() if not event.has_channel(Channel.EMAIL)]
            assert eventless, "expected at least one event with no email channel"
            for event in eventless:
                assert settings_matrix._section_for(event) != settings_matrix.ALWAYS_EMAILED_SECTION

    def describe_the_inventory():
        def it_holds_exactly_todays_forced_email_events(db):
            derived = {event.key for event in all_events() if settings_matrix._is_always_sent(event)}
            # refund_failed also forces email but is staff-routed; the staff check wins.
            assert derived == ALWAYS_EMAILED_KEYS | {"refund_failed"}

        def it_renders_those_ten_rows_to_a_plain_member(db):
            user = User.objects.create_user(username="ae1", email="ae1@example.com")
            rows = dict(settings_matrix.build_matrix(user))[settings_matrix.ALWAYS_EMAILED_SECTION]
            assert {row.event_key for row in rows} == ALWAYS_EMAILED_KEYS

    def describe_refund_failed():
        def it_stays_in_the_staff_section(db):
            # It declares a forced email AND routes to a refund authority. Staff wins:
            # the block a plain member sees must not carry a staff duty.
            event = get_event("refund_failed")
            assert settings_matrix._is_always_sent(event)
            assert settings_matrix._section_for(event) == settings_matrix.STAFF_SECTION

        def it_renders_under_staff_for_a_billing_approver(db):
            user = User.objects.create_user(username="ae2", email="ae2@example.com")
            Member.objects.get(user=user).admin_capabilities.create(
                capability=AdminCapability.Capability.BILLING_APPROVER
            )
            assert _section_of(user, "refund_failed") == settings_matrix.STAFF_SECTION

        def it_never_reaches_the_always_emailed_block_a_plain_member_sees(db):
            user = User.objects.create_user(username="ae5", email="ae5@example.com")
            rows = dict(settings_matrix.build_matrix(user))[settings_matrix.ALWAYS_EMAILED_SECTION]
            assert "refund_failed" not in {row.event_key for row in rows}

    def describe_ordering():
        def it_renders_after_every_member_facing_category_for_a_plain_member(db):
            user = User.objects.create_user(username="ae3", email="ae3@example.com")
            sections = _sections(user)
            assert sections[-1] == settings_matrix.ALWAYS_EMAILED_SECTION
            assert settings_matrix.STAFF_SECTION not in sections

        def it_renders_between_the_last_category_and_staff_for_an_admin(db):
            user = User.objects.create_user(username="ae4", email="ae4@example.com")
            member = Member.objects.get(user=user)
            member.fog_role = Member.FogRole.ADMIN
            member.save()
            sections = _sections(user)
            assert sections[-2:] == [settings_matrix.ALWAYS_EMAILED_SECTION, settings_matrix.STAFF_SECTION]

        def it_orders_the_two_tail_sections_structurally_not_alphabetically(db):
            # Both tail sections are outside CATEGORY_ORDER, so today they would also land
            # correctly by alphabet — "Always emailed" < "Staff & leadership" < nothing
            # else. That is an accident of two strings. A brand-new unlisted category
            # sorting after both must still render BEFORE them.
            ordered = settings_matrix._ordered_categories(
                {
                    "Guilds",
                    "Zebra keeping",
                    settings_matrix.ALWAYS_EMAILED_SECTION,
                    settings_matrix.STAFF_SECTION,
                }
            )
            assert ordered == [
                "Guilds",
                "Zebra keeping",
                settings_matrix.ALWAYS_EMAILED_SECTION,
                settings_matrix.STAFF_SECTION,
            ]

        def it_omits_a_tail_section_nobody_is_shown(db):
            ordered = settings_matrix._ordered_categories({"Guilds", settings_matrix.ALWAYS_EMAILED_SECTION})
            assert ordered == ["Guilds", settings_matrix.ALWAYS_EMAILED_SECTION]
