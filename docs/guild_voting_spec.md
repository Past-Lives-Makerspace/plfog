
# Feature Brief: Guild Voting

## Goal
Allow members of Past Lives Makerspace to direct guild funding through ranked voting.

Members allocate voting points to guilds they support. The proportion of total voting points determines how the monthly funding pool is distributed among guilds.

---

# System Scope

The guild voting system is a calculation and reporting system only.

It does not transfer money, process payments, or execute financial transactions.

Its purpose is to:

• calculate the monthly funding pool  
• calculate the proportional funding allocation for each guild  
• record historical voting and funding results  

The output of the system is a funding report that tells administrators how much money should be allocated to each guild.

Actual movement of money occurs outside the system through the organization's normal financial processes.

---

# Who Can Vote

All active members may vote.

Each voter distributes **10 voting points** across three guilds.

1st choice = 5 points  
2nd choice = 3 points  
3rd choice = 2 points  

Total points per voter = **10 points**.

Points represent voting weight, not money.

---

# Funding Pool

Only **active paying members** contribute money to the guild funding pool.

A paying member becomes part of the funding pool when they log in and submit a vote.

Once a paying member has submitted a vote, their status as a **persistent funding contributor** remains active between funding cycles as long as they remain in the **Active Paying Members list**.

After that point:

• their **$10 contribution** is included in each funding cycle  
• their vote preferences continue to allocate their points  
• both remain active until the member changes their vote or is removed from the Active Paying Members list

---

# Persistent Voting

Voting preferences persist month to month until changed.

Members may update their vote at any time.

Once a member has voted, their vote remains active for future cycles unless they modify it.

Members do not need to vote every month.

---

# Voting Cycle

Voting cutoff occurs on the **15th of each month at 9:00 AM Pacific Time**.

At this moment the system records a **snapshot of all active votes**.

That snapshot determines:

• the total voting points for each guild  
• the set of persistent funding contributors  
• the funding pool for that cycle  

Immediately after the snapshot is taken, a **new voting cycle begins**.

Any vote changes made after the snapshot apply only to the **next funding cycle**.

Funds from the recorded cycle are distributed on the **15th of the following month**.

---

# Funding Pool Determination

Funding pool =  
(number of contributing paying members) × $10

---

# Funding Calculation

Guild funding =  
(guild points / total points) × funding pool

---

# Funding Invariant

The sum of all guild funding allocations must always equal the funding pool.

sum(all guild funding) = funding pool

---

# Acceptance Criteria

• Members can log in and submit a ranked vote for three guilds.  
• Votes assign **5, 3, and 2 points**.  
• Voting preferences persist across cycles until changed.  
• Paying members who vote become **persistent funding contributors**.  
• Each persistent contributor adds **$10 per cycle** while paying.  
• The system records a snapshot at the cutoff.  
• Guild funding allocations follow the defined formula.  
• Historical records of snapshots and allocations are preserved.

---

# Negative Acceptance Criteria

The system must not:

• Allow more than **10 total vote points**.  
• Allow fewer or more than **three guild selections**.  
• Allow duplicate guild selections in one vote.  
• Allow votes from inactive members.  
• Allow a contributor to add more or less than **$10** per cycle.  
• Modify past snapshot results.  
• Lose historical vote or allocation records.

---

# Constraints

• Each voter assigns exactly **5, 3, and 2 points**.  
• Funding is always proportional to total vote points.  
• The funding pool comes only from contributing paying members.  
• Contributors must exist in the **Active Paying Members list**.  
• Removal from that list removes future contributions.  
• Guild funding totals must equal the funding pool.  
• Snapshot results remain historically fixed.  
• The system must **never initiate financial transfers**.

---

# Data Model

## Member
Represents a Past Lives member.

Stores:
• member identifier  
• name  
• email  
• active status  

---

## Membership Status
Represents the current membership/payment condition.

Stores:
• member  
• active/inactive status  
• paying/non‑paying status  

---

## Guild
Represents a guild eligible for voting.

Stores:
• guild identifier  
• guild name  
• active status  

---

## Vote Preference
Represents a member’s current persistent vote.

Stores:
• member  
• first choice guild  
• second choice guild  
• third choice guild  
• last updated timestamp  

Only one vote preference exists per member.

---

## Voting Snapshot
Represents the official state of the system at the cutoff.

Stores:
• snapshot timestamp  
• cycle label  
• contributing paying member count  
• funding pool amount  

---

## Snapshot Vote Record
Represents the vote counted for a member during a specific snapshot.

Stores:
• voting snapshot  
• member  
• ranked guild choices  
• assigned points  

---

## Snapshot Contributor Record
Represents a member whose $10 contribution counted in a cycle.

Stores:
• voting snapshot  
• member  
• contribution amount  

---

## Guild Funding Allocation
Represents the calculated funding for a guild during a snapshot.

Stores:
• voting snapshot  
• guild  
• total points received  
• percent of total points  
• funding allocation amount
