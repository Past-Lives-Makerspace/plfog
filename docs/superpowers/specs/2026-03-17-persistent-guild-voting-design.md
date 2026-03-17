# Persistent Guild Voting — Design Spec

## Goal
Replace the session-based guild voting system with persistent voting per the guild_voting_spec.md. Members vote anytime, preferences persist, snapshots capture state for funding calculations.

## Data Model

### New Models (in membership/models.py)

**VotePreference** — one row per member, their current persistent vote
- `member` — OneToOneField → Member (CASCADE), related_name="vote_preference"
- `guild_1st` — ForeignKey → Guild (CASCADE), related_name="first_choice_votes"
- `guild_2nd` — ForeignKey → Guild (CASCADE), related_name="second_choice_votes"
- `guild_3rd` — ForeignKey → Guild (CASCADE), related_name="third_choice_votes"
- `updated_at` — DateTimeField(auto_now=True)
- Constraint: all three guilds must be different (enforced at form level, not DB)

**FundingSnapshot** — immutable historical record
- `cycle_label` — CharField(100), e.g. "March 2026"
- `snapshot_at` — DateTimeField
- `contributor_count` — PositiveIntegerField
- `funding_pool` — DecimalField(max_digits=10, decimal_places=2)
- `results` — JSONField (output of vote_calculator.calculate_results)
- Ordering: ["-snapshot_at"]

### Remove
- `VotingSession` model
- `GuildVote` model

### Keep
- `vote_calculator.py` — reused for snapshot math

### Migration
- New migration: drop VotingSession + GuildVote, create VotePreference + FundingSnapshot

## Views (hub/views.py)

### guild_voting(request) — GET and POST at /guilds/voting/
**GET:**
- Get member's existing VotePreference (or None)
- Get latest FundingSnapshot (or None)
- Get all active guilds for form choices
- Render template with: form (pre-filled if preference exists), current preferences, latest results

**POST:**
- Validate VotePreferenceForm
- Create or update VotePreference for member
- Redirect back with success message
- If member is None or not active, show info message

### take_snapshot(request) — POST-only at /guilds/voting/snapshot/
- Staff-only (@login_required + @staff_member_required equivalent)
- Gather all VotePreferences with select_related
- Filter paying members (membership_plan__monthly_price__gt=0)
- Build votes list for vote_calculator
- Call calculate_results(votes, paying_voter_count)
- Save FundingSnapshot with cycle_label = current month/year
- Redirect back with success message

## Forms (hub/forms.py)

### VotePreferenceForm
- `guild_1st`, `guild_2nd`, `guild_3rd` — ModelChoiceField(queryset=Guild.objects.filter(is_active=True))
- clean() validates all three are different
- Labels: "1st Choice (5 points)", "2nd Choice (3 points)", "3rd Choice (2 points)"

## URLs (hub/urls.py)
- `guilds/voting/` → guild_voting (existing, gets real functionality)
- `guilds/voting/snapshot/` → take_snapshot (new, POST-only)

## Template (templates/hub/guild_voting.html)

Three sections:

1. **Vote Form** — 3 dropdowns, point weights shown, submit button. If member has voted, pre-filled with current choices and shows "Update Vote" instead of "Submit Vote". If no member linked, show message.

2. **Current Preferences** — if member has voted, show their 3 choices with point values and last-updated timestamp

3. **Funding Results** — latest snapshot results table: guild name, points, percentage, funding amount. Shows funding pool total and snapshot date. "No results yet" if none exist.

4. **Admin Section** (staff only) — "Take Snapshot" button with confirmation. List of past snapshots with date and pool amount.

## Files to Delete
- membership/vote_views.py
- membership/vote_forms.py
- membership/vote_urls.py
- membership/vote_emails.py
- membership/airtable_sync.py
- templates/membership/voting/ (entire directory, 10 files)
- tests/membership/vote_views_spec.py
- tests/membership/vote_forms_spec.py
- tests/membership/vote_emails_spec.py
- tests/membership/airtable_sync_spec.py
- tests/membership/voting_session_spec.py

## Files to Keep
- membership/vote_calculator.py
- tests/membership/vote_calculator_spec.py

## Testing
- BDD/spec style with pytest-describe, files named *_spec.py, functions named it_*
- Test VotePreference model creation and constraints
- Test VotePreferenceForm validation (valid, duplicates, missing)
- Test guild_voting view GET (no vote, has vote, no member)
- Test guild_voting view POST (create, update, invalid)
- Test take_snapshot view (staff only, calculation correctness)
- Test FundingSnapshot creation and ordering
