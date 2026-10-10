"""The settings matrix: which channels it surfaces, which section and permission group a
row lands in, which channels each scope lets you flip, and how the six sibling row groups
render and save as one setting each."""

import pytest
from django.contrib.auth.models import User

from core.events import settings_matrix
from core.events.registry import Channel, Recipients, all_events, get_event
from core.events.settings_matrix import ADMIN_SECTION, PermissionGroup
from core.models import NotificationPreference
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _sections(user, **kwargs):
    return [section.title for section in settings_matrix.build_matrix(user, **kwargs)]


def _rows(matrix):
    return [row for section in matrix for block in section.blocks for row in block.rows]


def _placement(user, event_key):
    """``(section title, block heading)`` for the row, or None if not shown."""
    for section in settings_matrix.build_matrix(user):
        for block in section.blocks:
            if any(row.event_key == event_key for row in block.rows):
                return section.title, block.heading
    return None


def _section_of(user, event_key):
    """Return the section a given event row renders under, or None if not shown."""
    placement = _placement(user, event_key)
    return None if placement is None else placement[0]


def _rows_in(user, title):
    section = next(section for section in settings_matrix.build_matrix(user) if section.title == title)
    return [row for block in section.blocks for row in block.rows]


def _admin_headings(user):
    for section in settings_matrix.build_matrix(user):
        if section.is_admin:
            return [block.heading for block in section.blocks]
    return []


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


def _grant(user, *capabilities):
    for capability in capabilities:
        Member.objects.get(user=user).admin_capabilities.create(capability=capability)


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
        assert matrix  # sections exist
        for row in _rows(matrix):
            assert len(row.cells) == expected

    def it_returns_sections_of_blocks_of_rows():
        user = User.objects.create_user(username="vc5", email="vc5@example.com")
        for section in settings_matrix.build_matrix(user):
            assert isinstance(section, settings_matrix.MatrixSection)
            assert section.blocks
            for block in section.blocks:
                assert isinstance(block, settings_matrix.MatrixBlock)
                assert block.rows

    def it_slugs_each_section_for_its_jump_chip():
        user = _member_user("slug1", fog_role=Member.FogRole.ADMIN)
        slugs = {section.title: section.slug for section in settings_matrix.build_matrix(user)}
        assert slugs[ADMIN_SECTION] == "admin-permissions"
        assert slugs["Spaces & Equipment"] == "spaces-equipment"


def describe_channel_labels():
    def it_labels_push_plainly():
        # Renamed from "Push (Browser)" now that native push is the primary carrier.
        assert settings_matrix.CHANNEL_LABELS[Channel.PUSH] == "Push"


def describe_push_defaults():
    def it_offers_push_on_every_row_with_a_mix_of_defaults():
        user = User.objects.create_user(username="pd", email="pd@example.com")
        push_cells = [
            cell
            for row in _rows(settings_matrix.build_matrix(user))
            for cell in row.cells
            if cell.channel is Channel.PUSH and cell.present
        ]
        assert push_cells  # every in-app row offers a push toggle
        # important events default on, routine ones default off — both appear for a member
        assert any(cell.enabled for cell in push_cells)
        assert any(not cell.enabled for cell in push_cells)


def describe_admin_section():
    # A pure-capability approval event (routes to SPACE_APPROVERS): visible ONLY to a
    # holder of the Space capability, and grouped under Admin / Permissions, not "Spaces".
    CAP_EVENT = "space.lease_requested"
    # An admin-only alert routed by role (FOG_ADMINS), not by a capability.
    ADMIN_ALERT_EVENT = "new_member_joined"
    # A composite event (GUILD_LEADERSHIP_OR_CLASS_APPROVERS): visible to guild leadership
    # OR a Class-capability holder.
    COMPOSITE_EVENT = "class_review_requested"

    def describe_a_plain_member():
        def it_never_sees_the_admin_section(db):
            user = _member_user("plain1")  # default fog_role=member, no capabilities/leadership
            assert ADMIN_SECTION not in _sections(user)

        def it_sees_none_of_the_staff_events(db):
            user = _member_user("plain2")
            assert _section_of(user, CAP_EVENT) is None
            assert _section_of(user, ADMIN_ALERT_EVENT) is None

        def it_still_sees_its_member_categories(db):
            user = _member_user("plain3")
            assert _sections(user)  # member-facing categories remain

    def describe_a_user_with_no_member():
        def it_never_sees_the_admin_section(db):
            user = User.objects.create_user(username="nomember", email="nomember@example.com")
            Member.objects.filter(user=user).delete()
            assert not Member.objects.filter(user=user).exists()
            assert ADMIN_SECTION not in _sections(user)

    def describe_a_fog_admin_without_capabilities():
        def it_sees_the_section_first_with_the_admin_alerts_under_admin(db):
            user = _member_user("admin1", fog_role=Member.FogRole.ADMIN)
            assert _sections(user)[0] == ADMIN_SECTION
            assert _placement(user, ADMIN_ALERT_EVENT) == (ADMIN_SECTION, "Admin")

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("admin2", fog_role=Member.FogRole.ADMIN)
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_capability_holder():
        def it_sees_the_capabilitys_event_under_the_capability(db):
            user = _member_user("cap1")  # plain member granted one duty
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _placement(user, CAP_EVENT) == (ADMIN_SECTION, "Space & Cubby Administrator")

        def it_does_not_see_a_capability_it_does_not_hold(db):
            user = _member_user("cap2")
            _grant(user, AdminCapability.Capability.SPACE_APPROVER)
            assert _section_of(user, "class_validation_requested") is None

    def describe_an_equipment_manager():
        # equipment.reservation_made routes to EQUIPMENT_MANAGERS: a per-equipment staff
        # row and guild leadership each see the row, and so does an EQUIPMENT holder,
        # because equipment nobody runs falls back to them (#746). A plain member never
        # does. Page == delivery.
        EQUIPMENT_EVENT = "equipment.reservation_made"

        def it_shows_the_row_to_a_per_equipment_staff_row_holder(db):
            from tests.membership.factories import EquipmentStaffMembershipFactory

            user = _member_user("equipmgr1")
            EquipmentStaffMembershipFactory(member=Member.objects.get(user=user))
            assert _placement(user, EQUIPMENT_EVENT) == (ADMIN_SECTION, "Equipment manager")

        def it_shows_the_row_to_an_equipment_capability_holder(db):
            user = _member_user("equipmgr2")
            _grant(user, AdminCapability.Capability.EQUIPMENT)
            assert _placement(user, EQUIPMENT_EVENT) == (ADMIN_SECTION, "Equipment Administrator")

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
            assert _placement(staffed, "orientation_requested") == (ADMIN_SECTION, "Equipment manager")
            holder = _member_user("equiporient2")
            _grant(holder, AdminCapability.Capability.EQUIPMENT)
            assert _placement(holder, "orientation_requested") == (ADMIN_SECTION, "Equipment Administrator")
            plain = _member_user("equiporient3")
            assert _section_of(plain, "orientation_requested") is None

    def describe_a_guild_lead():
        def it_sees_composite_leadership_events_but_not_unheld_capabilities(db):
            user = _member_user("lead1")
            GuildFactory(guild_lead=Member.objects.get(user=user))
            assert _placement(user, COMPOSITE_EVENT) == (ADMIN_SECTION, "Guild leadership")
            assert _section_of(user, CAP_EVENT) is None

    def describe_a_guild_officer():
        # voting.officers_closing_soon routes to ALL_GUILD_LEADS, whose resolver filters to
        # active members — so the row must track Member.status, not just the role.
        ALL_LEADS_EVENT = "voting.officers_closing_soon"

        def it_shows_all_guild_leads_rows_to_an_active_officer(db):
            user = _member_user("officer1", fog_role=Member.FogRole.GUILD_OFFICER)
            assert _placement(user, ALL_LEADS_EVENT) == (ADMIN_SECTION, "Guild leadership")

        def it_hides_all_guild_leads_rows_from_a_former_officer(db):
            user = _member_user("officer2", fog_role=Member.FogRole.GUILD_OFFICER, status=Member.Status.FORMER)
            assert _section_of(user, ALL_LEADS_EVENT) is None

    def describe_which_events_land_there():
        def it_takes_every_staff_routed_event_and_no_member_event(db):
            for event in all_events():
                in_admin = settings_matrix._section_for(event) == ADMIN_SECTION
                assert in_admin == (event.recipient in settings_matrix.STAFF_RECIPIENTS), event.key

        def it_renders_staff_rows_only_there_for_a_viewer_who_holds_everything(db):
            user = _everything_user("everything1")
            for section in settings_matrix.build_matrix(user):
                for row in (row for block in section.blocks for row in block.rows):
                    recipient = _event_for_row(row.event_key).recipient
                    assert (recipient in settings_matrix.STAFF_RECIPIENTS) == section.is_admin, row.event_key

        def it_registers_no_category_named_after_the_section(db):
            # ADMIN_SECTION is a section name the code assigns, never a category an event
            # may declare. One that did would render member rows under the admin heading
            # and the admin-only note.
            assert [event.key for event in all_events() if event.category == ADMIN_SECTION] == []


def _event_for_row(event_key):
    """The event a row stands for: itself, or the first member of its row group."""
    group = next((g for g in settings_matrix.ROW_GROUPS if g.group_id == event_key), None)
    return get_event(event_key if group is None else group.event_keys[0])


def _everything_user(username):
    """An Admin who holds every capability, leads a guild and staffs a tool."""
    from tests.membership.factories import EquipmentStaffMembershipFactory

    user = _member_user(username, fog_role=Member.FogRole.ADMIN)
    _grant(user, *AdminCapability.Capability.values)
    member = Member.objects.get(user=user)
    GuildFactory(guild_lead=member)
    EquipmentStaffMembershipFactory(member=member)
    return user


def describe_permission_groups():
    def it_orders_the_groups_and_heads_them_with_the_permissions_tab_labels():
        headings = settings_matrix._group_headings()
        assert [headings[group] for group in PermissionGroup] == [
            "Admin",
            "CMS Administrator",
            "Billing Administrator",
            "Refunds",
            "Calendar Administrator",
            "Space & Cubby Administrator",
            "Discount Code Administrator",
            "Equipment Administrator",
            "Webmaster",
            "Guild leadership",
            "Equipment manager",
        ]

    def it_renders_the_groups_a_viewer_holds_in_that_order(db):
        # Refunds and Equipment manager are absent on purpose: every row that could file
        # under them files under an earlier group this viewer holds (Admin, Equipment
        # Administrator), and a group with no rows does not render.
        assert _admin_headings(_everything_user("order1")) == [
            "Admin",
            "CMS Administrator",
            "Billing Administrator",
            "Calendar Administrator",
            "Space & Cubby Administrator",
            "Discount Code Administrator",
            "Equipment Administrator",
            "Webmaster",
            "Guild leadership",
        ]

    def it_shows_refunds_to_a_refunds_holder_who_is_not_an_admin(db):
        user = _member_user("refunds1")
        _grant(user, AdminCapability.Capability.REFUNDS)
        assert _admin_headings(user) == ["Refunds"]
        assert _placement(user, "class_cancelled_admin_notice") == (ADMIN_SECTION, "Refunds")

    def it_files_a_row_once_under_the_first_group_the_viewer_holds(db):
        # A guild lead who is also a CMS Administrator gets class review requests both
        # ways; the row shows once, under the earlier group.
        user = _member_user("dedupe1")
        GuildFactory(guild_lead=Member.objects.get(user=user))
        _grant(user, AdminCapability.Capability.CLASS_APPROVER)
        keys = [row.event_key for row in _rows(settings_matrix.build_matrix(user))]
        assert keys.count("class_review_requested") == 1
        assert _placement(user, "class_review_requested") == (ADMIN_SECTION, "CMS Administrator")

    def it_files_the_automation_alert_under_webmaster_for_a_holder_only(db):
        holder = _member_user("webmaster1")
        _grant(holder, AdminCapability.Capability.WEBMASTER)
        assert _admin_headings(holder) == ["Webmaster"]
        assert _placement(holder, "automation.failed") == (ADMIN_SECTION, "Webmaster")
        # A plain admin without the grant receives nothing, so the page shows them nothing.
        assert _placement(_member_user("webmaster2", fog_role=Member.FogRole.ADMIN), "automation.failed") is None

    def it_renders_no_empty_group(db):
        user = _member_user("empty1")
        _grant(user, AdminCapability.Capability.SPACE_APPROVER)
        assert _admin_headings(user) == ["Space & Cubby Administrator"]

    def it_leaves_every_member_topic_one_untitled_block(db):
        for section in settings_matrix.build_matrix(_everything_user("untitled1")):
            if not section.is_admin:
                assert [block.heading for block in section.blocks] == [""], section.title

    def describe_the_group_table():
        def it_gives_every_staff_recipient_candidate_groups():
            assert set(settings_matrix._GROUPS_BY_RECIPIENT) == settings_matrix.STAFF_RECIPIENTS
            assert all(settings_matrix._GROUPS_BY_RECIPIENT.values())

        def it_places_every_viewer_eligible_for_a_row_in_a_group_they_hold():
            # Walks every staff recipient against a viewer holding each single standing and
            # each capability: whoever _eligible_for admits must land in a held group, or
            # the row would be a notice they receive but cannot find.
            for recipient in settings_matrix.STAFF_RECIPIENTS:
                for profile in _single_standing_profiles():
                    if not settings_matrix._eligible_for(recipient, profile):
                        continue
                    group = settings_matrix._group_for(recipient, profile)
                    assert profile.holds(group), (recipient, profile)

        def it_raises_when_no_group_places_a_row():
            with pytest.raises(settings_matrix.NoPermissionGroupError):
                settings_matrix._group_for(Recipients.FOG_ADMINS, _profile())


def _profile(**overrides):
    fields = {
        "is_admin": False,
        "is_active": True,
        "is_officer": False,
        "leads_guild": False,
        "staffs_guild": False,
        "is_orienter": False,
        "manages_equipment": False,
        "capabilities": frozenset(),
    }
    fields.update(overrides)
    return settings_matrix._StaffProfile(**fields)


def _single_standing_profiles():
    profiles = [
        _profile(is_admin=True),
        _profile(is_officer=True),
        _profile(leads_guild=True),
        _profile(staffs_guild=True),
        _profile(staffs_guild=True, is_orienter=True),
        _profile(manages_equipment=True),
        _profile(staffs_guild=True, is_kiln_crew=True),
    ]
    profiles += [_profile(capabilities=frozenset({value})) for value in AdminCapability.Capability.values]
    return profiles


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
        "group.feedback_request": (
            "Updates to your requests",
            "A feature request or bug report you sent is planned, being built, live, or not planned.",
            "Your requests",
        ),
        "group.space_request_decision": (
            "Updates to your space request",
            "Your studio or cubby request was approved or declined.",
            "Spaces & Equipment",
        ),
        "group.instructor_application_decision": (
            "Updates to your request to host a class",
            "Your request to host a class was approved or declined.",
            "Classes",
        ),
        "group.waitlist_promotion": (
            "Added from the waitlist",
            "Staff moved you off the waitlist into a class. If the class is paid, this carries your payment link.",
            "Classes",
        ),
    }

    def _rows_by_key(user):
        return {
            row.event_key: (section.title, row)
            for section in settings_matrix.build_matrix(user)
            for block in section.blocks
            for row in block.rows
        }

    def it_renders_each_group_as_exactly_one_row_with_its_copy_in_its_section(db):
        user = User.objects.create_user(username="grp1", email="grp1@example.com")
        keys = [row.event_key for row in _rows(settings_matrix.build_matrix(user))]
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
        classes = _rows_in(user, "Classes")
        assert [row.event_key for row in classes] == [
            "class_reminder",
            "registration_confirmed",
            # A forced email no longer moves a row: the two padlocked Classes rows sit in
            # catalogue order with the rest.
            "class_cancelled",
            "waitlist_spot_available",
            "waitlist_confirmed",
            "refund_issued",
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
            assert _section_of(user, "event.submitted") == ADMIN_SECTION

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
            # split. Two staff recipients both land in ADMIN_SECTION, so it passes them —
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
            for row in _rows(settings_matrix.build_matrix(user)):
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


def describe_padlocked_rows():
    # The 13 rows the retired Always emailed block held, with the topic each returns to
    # (#524). Pinned as a literal: which forced notice a member finds where is the point.
    FORMER_ALWAYS_EMAILED = {
        "class_cancelled": "Classes",
        "refund_issued": "Classes",
        "tab_entry_added": "Billing",
        "tab_approaching_limit": "Billing",
        "billing.late_fee_paid": "Billing",
        "billing.late_fee_waived": "Billing",
        "lease_expiring": "Spaces & Equipment",
        "equipment.reservation_confirmed": "Spaces & Equipment",
        "equipment.reservation_cancelled_by_manager": "Spaces & Equipment",
        "equipment.reservation_cancelled": "Spaces & Equipment",
        "member.invited": "Membership",
        "member.login_invite": "Membership",
        "discord_guilds_imported": "Guilds",
    }
    # Forced member emails added since (#748): a request's receipt and its decline.
    FORCED_SINCE = {
        "equipment.reservation_requested": "Spaces & Equipment",
        "equipment.reservation_declined": "Spaces & Equipment",
    }
    # Forced emails routed to staff: padlocked too, but in Admin / Permissions.
    STAFF_FORCED = {
        "refund_failed",
        "classes.duplicate_payment_alert",
        "classes.orphaned_payment_alert",
        "billing.late_fee_orphan_payment",
        "membership.orientation_orphan_payment",
    }

    def _email_cell(user, event_key):
        for row in _rows(settings_matrix.build_matrix(user)):
            if row.event_key == event_key:
                return next(cell for cell in row.cells if cell.channel is Channel.EMAIL)
        raise AssertionError(f"{event_key} row not rendered")

    def it_holds_exactly_todays_forced_email_events(db):
        # Adding or removing a forced email is a deliberate edit to this list.
        forced = {event.key for event in all_events() if (spec := event.channel(Channel.EMAIL)) and spec.is_forced}
        assert forced == set(FORMER_ALWAYS_EMAILED) | set(FORCED_SINCE) | STAFF_FORCED

    def it_returns_each_former_always_emailed_row_to_its_topic(db):
        user = _member_user("pad1")
        for key, section in {**FORMER_ALWAYS_EMAILED, **FORCED_SINCE}.items():
            assert _section_of(user, key) == section, key

    def it_locks_their_email_cell_on_with_the_padlock(db):
        user = _member_user("pad2")
        for key in FORMER_ALWAYS_EMAILED:
            cell = _email_cell(user, key)
            assert (cell.present, cell.forced, cell.enabled, cell.is_editable) == (True, True, True, False), key

    def it_keeps_a_forced_event_in_its_category_like_any_other(db):
        # class_reminder is the control: same category, same recipient shape, email
        # default OFF rather than FORCED.
        assert settings_matrix._section_for(get_event("class_cancelled")) == "Classes"
        assert settings_matrix._section_for(get_event("class_reminder")) == "Classes"

    def describe_the_staff_routed_ones():
        def it_keeps_refund_failed_in_admin_permissions_padlocked(db):
            user = _member_user("pad3")
            _grant(user, AdminCapability.Capability.BILLING_APPROVER)
            assert _placement(user, "refund_failed") == (ADMIN_SECTION, "Billing Administrator")
            assert _email_cell(user, "refund_failed").forced is True

        def it_never_shows_them_to_a_plain_member(db):
            user = _member_user("pad4")
            for key in STAFF_FORCED:
                assert _section_of(user, key) is None, key

    def describe_saving_the_page():
        def it_leaves_push_and_discord_choices_on_padlocked_rows_unchanged(db):
            # The save-wipe trap: save_matrix reads an absent box as off, so a padlocked
            # row that stopped rendering its writable Push and Discord cells would zero
            # them on every save. Post exactly what a browser would from the rendered
            # cells (checked and not disabled), then read every choice back.
            user = _member_user("pad5")
            member = Member.objects.get(user=user)
            member.discord_user_id = "pad5-discord"
            member.save(update_fields=["discord_user_id"])
            chosen = {}
            for index, key in enumerate(sorted(FORMER_ALWAYS_EMAILED)):
                event = get_event(key)
                for channel in (Channel.PUSH, Channel.DISCORD_DM):
                    if event.has_channel(channel):
                        enabled = (index + len(chosen)) % 2 == 0
                        NotificationPreference.objects.create(
                            user=user, event_key=key, channel=channel.value, enabled=enabled
                        )
                        chosen[(key, channel.value)] = enabled
            assert True in chosen.values() and False in chosen.values()
            posted = {
                cell.name: "on"
                for row in _rows(settings_matrix.build_matrix(user))
                for cell in row.cells
                if cell.is_editable and cell.enabled
            }

            settings_matrix.save_matrix(user, posted)

            saved = {
                (pref.event_key, pref.channel): pref.enabled
                for pref in NotificationPreference.objects.filter(user=user, event_key__in=FORMER_ALWAYS_EMAILED)
            }
            assert saved == chosen  # every choice kept, and no row written for a padlocked email


def describe_editable_channels():
    def _cell(channel, *, forced=False, available=True, present=True):
        return settings_matrix.Cell(
            name=f"pref__x__{channel.value}",
            channel=channel,
            enabled=True,
            forced=forced,
            present=present,
            available=available,
        )

    def _row(*cells):
        return settings_matrix.Row(event_key="x", label="X", description="", cells=list(cells))

    def describe_a_block():
        def it_offers_only_channels_a_row_lets_you_flip(db):
            block = settings_matrix.MatrixBlock.of(
                "Heading",
                [
                    _row(
                        _cell(Channel.IN_APP, forced=True),  # the bell: always locked
                        _cell(Channel.EMAIL, forced=True),  # padlocked
                        _cell(Channel.PUSH),
                        _cell(Channel.DISCORD_DM, available=False),  # Discord not linked
                    ),
                    _row(_cell(Channel.EMAIL, present=False), _cell(Channel.PUSH)),
                ],
            )
            assert block.editable_channels == [Channel.PUSH]

        def it_offers_email_once_any_row_has_a_switchable_email(db):
            block = settings_matrix.MatrixBlock.of(
                "",
                [
                    _row(_cell(Channel.PUSH), _cell(Channel.EMAIL, forced=True)),
                    _row(_cell(Channel.EMAIL), _cell(Channel.DISCORD_DM)),
                ],
            )
            # Column order, not row order.
            assert block.editable_channels == [Channel.EMAIL, Channel.PUSH, Channel.DISCORD_DM]

    def it_unions_blocks_into_sections_and_sections_into_the_page(db):
        email = settings_matrix.MatrixBlock.of("A", [_row(_cell(Channel.EMAIL))])
        push = settings_matrix.MatrixBlock.of("B", [_row(_cell(Channel.PUSH))])
        section = settings_matrix.MatrixSection.of(ADMIN_SECTION, [push, email], is_admin=True)
        assert section.editable_channels == [Channel.EMAIL, Channel.PUSH]
        other = settings_matrix.MatrixSection.of("Guilds", [settings_matrix.MatrixBlock.of("", [])], is_admin=False)
        assert other.editable_channels == []
        assert settings_matrix.page_editable_channels([other, section]) == [Channel.EMAIL, Channel.PUSH]

    def it_matches_the_cells_on_every_scope_of_a_real_page(db):
        for section in settings_matrix.build_matrix(_everything_user("editable1")):
            for block in section.blocks:
                editable = {cell.channel for row in block.rows for cell in row.cells if cell.is_editable}
                assert set(block.editable_channels) == editable, (section.title, block.heading)

    def it_never_offers_a_bell_control(db):
        matrix = settings_matrix.build_matrix(_everything_user("editable2"))
        assert Channel.IN_APP not in settings_matrix.page_editable_channels(matrix)

    def it_offers_no_discord_control_to_an_unlinked_member(db):
        matrix = settings_matrix.build_matrix(_member_user("editable3"))
        for section in matrix:
            assert Channel.DISCORD_DM not in section.editable_channels, section.title
        assert Channel.DISCORD_DM not in settings_matrix.page_editable_channels(matrix)

    def it_offers_discord_once_the_member_links_it(db):
        user = _member_user("editable4")
        member = Member.objects.get(user=user)
        member.discord_user_id = "editable4-discord"
        member.save(update_fields=["discord_user_id"])
        matrix = settings_matrix.build_matrix(user)
        assert Channel.DISCORD_DM in settings_matrix.page_editable_channels(matrix)

    def it_offers_no_email_control_where_every_email_is_padlocked(db):
        # A scope made of the real padlocked rows (a forced email each) has an Email column
        # full of padlocks and so no Email control, while their Push cells stay live.
        user = _member_user("editable5")
        padlocked = {"member.invited", "class_cancelled", "refund_issued"}
        rows = [row for row in _rows(settings_matrix.build_matrix(user)) if row.event_key in padlocked]
        assert {row.event_key for row in rows} == padlocked
        block = settings_matrix.MatrixBlock.of("", rows)
        assert Channel.EMAIL not in block.editable_channels
        assert Channel.PUSH in block.editable_channels
