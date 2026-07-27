"""App version and changelog."""

from __future__ import annotations

VERSION = "0.23.40"

CHANGELOG: list[dict[str, str | list[str]]] = [
    {
        "version": "0.23.40",
        "date": "2026-07-27",
        "title": "Cleaner Discord posts and event calendar",
        "changes": [
            "The weekly class and calendar digests no longer repeat several times on Monday mornings.",
            "The community calendar Discord channel now shows events only, so classes and open studio hours no longer clutter it.",
            "Fixed a class that was showing the wrong instructor's name.",
        ],
    },
    {
        "version": "0.23.38",
        "date": "2026-07-24",
        "title": "Explore the makerspace on an interactive map",
        "changes": [
            (
                "There's a new Spaces page in the sidebar with a real map of the building. Pick a "
                "floor and every studio and shop is drawn to shape and colour-coded at a "
                "glance — green for open, grey for taken, amber for under maintenance — with its "
                "size and monthly price written right in the room. Drag to pan, pinch or scroll "
                "to zoom, and tap any space for the full details and who's in it."
            ),
            (
                "A space that belongs to a guild now links straight to that guild's page: open a "
                "shop or studio on the map, and its guild's name in the details is a tap away from "
                "everything that guild is about."
            ),
            (
                "Prefer not to use the map? The Listings tab has every space in a plain table you "
                "can read and use with a keyboard. It follows the floor you picked, puts the "
                "available spaces first, and you can search it or filter it down to just what's "
                "open."
            ),
            (
                "Everything else that used to share that page now has its own Help page, further "
                "down the sidebar: parking and arrival, who to contact, the FAQ, the code of "
                "conduct, and the handbook links. Old links to the map still work."
            ),
            (
                "The new Help page opens with plain-language guides to how the makerspace works: "
                "guilds, orientations, guild voting, taking and teaching classes, the community "
                "calendar, connecting Discord, and your notification settings. Each guide has its "
                "own link so you can jump straight to the one you need. There's also a new Wiki "
                "link in the sidebar that opens the makerspace's full knowledge base in a new tab."
            ),
            (
                "See a space you want? Ask for it right there. Your request goes to the makerspace "
                "admins, and you'll get an email and a notification when they answer. Nothing is "
                "charged automatically; a human sorts the paperwork out with you."
            ),
            (
                "Changed your mind? Your open requests show on the same page with a Withdraw "
                "button, and you can ask again later."
            ),
        ],
    },
    {
        "version": "0.23.25",
        "date": "2026-07-22",
        "title": "Class photos now load faster",
        "changes": [
            (
                "Class photos are now served from our own storage instead of being fetched "
                "from the old class website every time a page opened. Browsing the class "
                "catalog should feel noticeably quicker, and the pictures keep working once "
                "the old site is switched off."
            ),
        ],
    },
    {
        "version": "0.23.21",
        "date": "2026-07-22",
        "title": "Important notices now reach you by email, not just the bell",
        "changes": [
            (
                "A handful of things that really matter were only ever showing up as a "
                "notification in the app, so they were easy to miss. From now on they email you "
                "too: a class you booked being cancelled, a refund going out, a charge an admin "
                "adds to your tab, your tab getting close to its limit, and your studio lease "
                "coming up for renewal. Each one now spells out the details — which class, how "
                "much, which space, what date — instead of a one-line nudge."
            ),
            (
                "Booked a class as a guest, without a Past Lives account? Cancellation and refund "
                "emails now reach you too. Before, they only went to people with accounts, so "
                "guests heard nothing at all."
            ),
            (
                "Fixed: if you'd invited more than one person to Past Lives, only the very first "
                "acceptance ever notified you. Every invite you send now tells you when it's "
                "accepted."
            ),
        ],
    },
    {
        "version": "0.23.20",
        "date": "2026-07-22",
        "title": "Guild and account links from the class site now show a proper 'page not found'",
        "changes": [
            (
                "Opening a guild page, member directory, settings, or billing link while you were "
                "on the class-booking site showed an error page instead of telling you the page "
                "isn't available there. Those links now land on a friendly 'we couldn't find that "
                "page' message with a way back to browsing classes — and they still work as normal "
                "when you're signed in on the member hub."
            ),
        ],
    },
    {
        "version": "0.23.20",
        "date": "2026-07-22",
        "title": "Picked a guild on Discord but not linked yet? The bot now gives you a heads-up",
        "changes": [
            (
                "If you react in #choose-your-guild to join a guild but your Discord isn't "
                "connected to your Past Lives account yet, the bot now sends you a one-time DM "
                "letting you know you're only halfway done — with the link to finish connecting. "
                "Once you're linked, your existing reaction counts automatically, no re-reacting "
                "needed."
            ),
        ],
    },
    {
        "version": "0.23.17",
        "date": "2026-07-21",
        "title": "Guild-funding voting comes to Discord: /voting and /vote",
        "changes": [
            (
                "Type /voting in the Past Lives Discord to see this month's live guild-funding "
                "standings as a bar graph — every guild (even the ones still at zero), medals for "
                "the top three, and when the cycle closes. The standings reply is visible to "
                "everyone in the channel, so anyone can check the race at a glance."
            ),
            (
                "With /vote you can cast or change your ballot without leaving Discord — pick "
                "your 1st, 2nd, and 3rd choice guilds from the dropdowns and it saves instantly, "
                "exactly like voting on the page. Your own picks stay private: only you see the "
                "/vote reply."
            ),
        ],
    },
    {
        "version": "0.23.16",
        "date": "2026-07-21",
        "title": "Browse the member directory right in Discord with /members",
        "changes": [
            (
                "Type /members in the Past Lives Discord to flip through member profile cards — "
                "photos, skills, guilds, and the contact info each member has chosen to share — "
                "with Prev/Next buttons to page through and optional guild and search filters."
            ),
            (
                "It follows the exact same privacy rules as the app's directory: only listed "
                "members appear, hidden fields stay hidden, and the reply is visible only to you. "
                "Editing your card still happens in the app, under Settings → Directory."
            ),
        ],
    },
    {
        "version": "0.23.15",
        "date": "2026-07-21",
        "title": "Classes can now go on sale",
        "changes": [
            (
                "Instructors can put a class on sale with a percent or dollar discount — look for "
                "the bright sale banner on the class page and the original price crossed out next "
                "to the new one. You'll always see the sale price before you pay."
            ),
            (
                "Instructors: there's a new Sale section on your class edit page — flip the toggle, "
                "pick the discount, and optionally write your own banner headline."
            ),
        ],
    },
    {
        "version": "0.23.12",
        "date": "2026-07-21",
        "title": "The member directory is now members-only by default",
        "changes": [
            (
                "The member directory now asks visitors to sign in before it shows anything — so your "
                "card, contact info, and photo are visible only to fellow members unless the makerspace "
                "deliberately opens it up. A new site setting lets admins make the directory fully "
                "public again if that's ever wanted."
            ),
            (
                "The directory listing toggle in your profile settings now reads Hidden / Public, "
                "to make clearer what switching it on means."
            ),
        ],
    },
    {
        "version": "0.23.11",
        "date": "2026-07-21",
        "title": "Class updates now land in Discord's #classes channel",
        "changes": [
            (
                "Every Monday morning, the #classes channel on Discord gets a digest of the week's "
                "classes — day by day, with times, instructors, and links to sign up — plus a section "
                "of flexible-scheduling classes you can book anytime."
            ),
            (
                "And whenever a new class is published, a short post announces it in the channel right "
                "away with the date, price, and a sign-up link."
            ),
        ],
    },
    {
        "version": "0.23.10",
        "date": "2026-07-21",
        "title": "The #important-info channel now stays current automatically",
        "changes": [
            (
                "The pinned links post in Discord's #important-info channel — classes, the Community "
                "Calendar, the Code of Conduct, and the Past Lives app — is now managed from the app, "
                "so when a link changes the post updates right away instead of going stale."
            ),
            (
                "The bot-commands guide in that same post now builds itself from the bot's actual "
                "slash commands, so any new command shows up there automatically."
            ),
        ],
    },
    {
        "version": "0.23.9",
        "date": "2026-07-21",
        "title": "The #public-calendar channel is back in business",
        "changes": [
            (
                "Every Monday morning, the #public-calendar channel on Discord now gets a digest of "
                "everything happening at the makerspace in the week ahead — events, guild meetings, and "
                "classes — grouped by day, with links to each one."
            ),
            (
                "And whenever a new event or class lands on the Community Calendar, a short post announces "
                "it in the channel right away, so you never miss something new."
            ),
            ("Want the whole picture? The full calendar is always at https://members.pastlives.space/calendar/."),
        ],
    },
    {
        "version": "0.23.8",
        "date": "2026-07-21",
        "title": "calendar.pastlives.space — an easy address for the Community Calendar",
        "changes": [
            (
                "There's now a short, shareable address for the Community Calendar: calendar.pastlives.space "
                "takes you straight there. Handy for flyers, Discord, and telling a friend."
            ),
        ],
    },
    {
        "version": "0.23.7",
        "date": "2026-07-21",
        "title": "The Events tab now lists everything on the calendar",
        "changes": [
            (
                "The Community Calendar's Events tab used to show only community events, so guild meetings, "
                "classes, and subscribed-calendar events were missing. It now lists every event on the calendar, "
                "in order, with easy paging through the full list."
            ),
            (
                'Each event\'s title is now a link straight to its page — tap it (or the new "More Info" link) '
                "to see the details and register."
            ),
        ],
    },
    {
        "version": "0.23.6",
        "date": "2026-07-21",
        "title": "See every event on a busy calendar day",
        "changes": [
            (
                "On the Community Calendar, a packed day used to hide extra events behind a small "
                '"+3" tag. Now you can click that tag to expand the day and see everything on it — '
                'then click "Show less" to tuck it back. Works in both the week and month views.'
            ),
        ],
    },
    {
        "version": "0.23.5",
        "date": "2026-07-21",
        "title": "Class pages: your own Q&A and a photo gallery by the signup button",
        "changes": [
            (
                'Teachers can now edit the "Questions" section on their class page — reword the standard '
                "questions, add your own, or remove ones that don't fit. Classes that haven't customized "
                "anything keep the familiar defaults."
            ),
            (
                "A class's extra photos now show up in a gallery right under the registration card, so they're "
                "easy to browse while you're deciding to sign up."
            ),
        ],
    },
    {
        "version": "0.23.4",
        "date": "2026-07-21",
        "title": "Community Calendar events are back — and coming to Discord",
        "changes": [
            (
                "The Member and Public calendars on the Community Calendar stopped updating for a while and "
                "quietly ran dry — they're syncing again every morning, each in its own color."
            ),
            (
                "Events from those calendars now also show up as Discord scheduled events, so you can RSVP "
                "and get reminders right in the server."
            ),
        ],
    },
    {
        "version": "0.23.4",
        "date": "2026-07-21",
        "title": "Classes stay filed under their guilds",
        "changes": [
            (
                "Classes in the catalog are being organized under the guild that runs them — Metalworking, "
                "Ceramics, Glass, and the rest — so browsing by guild actually works. Until now the nightly "
                "sync from the old class site quietly reset every class back to a generic bucket; that's fixed, "
                "so the groupings stick."
            ),
        ],
    },
    {
        "version": "0.23.2",
        "date": "2026-07-18",
        "title": "Pick a guild from a dropdown for /schedule-orientation",
        "changes": [
            (
                "The /schedule-orientation command in Discord now gives you a dropdown of guilds to choose from "
                "— the same as /join-guild and /info — instead of typing the name."
            ),
        ],
    },
    {
        "version": "0.23.1",
        "date": "2026-07-18",
        "title": "Pick a guild from a dropdown for /info",
        "changes": [
            (
                "The /info command in Discord now gives you a dropdown of guilds to choose from — the same as "
                "/join-guild — instead of typing the name. Leave it blank to use the current channel's guild."
            ),
        ],
    },
    {
        "version": "0.23.0",
        "date": "2026-07-17",
        "title": "Join a guild — and get around — right from Discord",
        "changes": [
            (
                "New /join-guild command: type it in the Past Lives Discord, pick your guild, and you're "
                "in — you get your guild's Discord role and a welcome from the guild, no trip to the app. "
                "(You can still join with the emoji reactions in #choose-your-guild too.)"
            ),
            (
                "New /guide command lists every Past Lives Discord command and what it does, so it's easy "
                "to see everything you can do without leaving Discord."
            ),
        ],
    },
    {
        "version": "0.23.0",
        "date": "2026-07-17",
        "title": "Guild leads: your mailing list, your call on who gets each email",
        "changes": [
            (
                "Your guild's edit page is now clearly your Guild Settings page, and its Announcements tab "
                "has a new Your Mailing List section: see exactly who your announcements reach, add custom "
                "email addresses beyond your members, and import a list all at once."
            ),
            (
                "When you post an announcement you can now choose who gets the email — everyone's included "
                "by default, just uncheck anyone you want to skip. Your members still see it in the app and "
                "it still posts to your Discord channel."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Events in Discord",
        "changes": [
            (
                "Community events now show up right in the Past Lives Discord server's Events tab, so "
                "you can mark yourself interested and get reminders without leaving Discord. Each one "
                "links back to its full event page."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Studio Hours on guild pages",
        "changes": [
            (
                "Guild leads can now set their weekly studio hours and meetings right in the app, and "
                "every guild page shows a Studio Hours card so you know when to drop by and chat with "
                "the lead. Meetings can now repeat weekly, not just monthly. Your guild's public Google "
                "Calendar stays in sync automatically."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Slash commands in Discord",
        "changes": [
            (
                "Type /link in the Past Lives Discord to connect your Discord to your member account in "
                "one tap — no digging through settings. Once connected, guild syncing and the rest of the "
                "Discord goodies just work."
            ),
            (
                "Four more commands to use right in Discord: /schedule-orientation to request a guild "
                "orientation, /whats-on for the next 7 days of events and classes, /balance to check "
                "your tab, and /info for any guild's rules, meetings, FAQ, and links."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "A warm welcome when you finish orientation",
        "changes": [
            (
                "When a member completes their orientation with a guild, that guild now gets an automatic "
                "hello — an in-app notification and a post in the guild's Discord channel — so newcomers "
                "are welcomed the moment they're through the door."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Know your reach before you post",
        "changes": [
            (
                "The guild Announcements tab now shows guild leads how many members will receive their "
                "emailed announcement, with the full recipient list a click away — so you know your "
                "audience before you compose."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Jump straight to a guild's classes",
        "changes": [
            (
                "Every guild page now has a “[Guild name] Classes” link that opens the class catalog "
                "filtered to just that guild's classes — even before the guild has scheduled any. The "
                "catalog shows a clear “Classes in [Guild]” heading so you always know the filter is on."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Class reminder emails read cleanly again",
        "changes": [
            (
                "Fixed the class reminder email so the instructor's welcome note shows with its proper "
                "formatting instead of stray code — the way it already appears in your confirmation email."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "A cleaner profile, with your own contact links",
        "changes": [
            (
                "Your profile settings now split into a Member tab and an Instructor tab, so your teaching "
                "info stays separate from your member-directory profile."
            ),
            (
                "Instructors get a dedicated “About me as an instructor” bio for your public class "
                "page — kept separate from the short bio on your member-directory card."
            ),
            (
                "Add your own labeled contact links — a website, Instagram, a booking email — and "
                "choose where each one shows: on your directory card, your instructor page, or both."
            ),
        ],
    },
    {
        "version": "0.22.9",
        "date": "2026-07-16",
        "title": "Easier-to-read class confirmation screens",
        "changes": [
            (
                "The class registration confirmation and cancellation screens had gold text that was hard to "
                "read in light mode. The wording now shows in a clearly legible color in both light and dark themes."
            ),
        ],
    },
    {
        "version": "0.21.19",
        "date": "2026-07-13",
        "title": "Account page loads reliably again",
        "changes": [
            (
                "Fixed an error that could stop your account page from loading when one of your classes was "
                "taught by an instructor without a public profile page. Your upcoming and past classes now "
                "show up reliably, and the instructor's name is shown either way."
            ),
        ],
    },
    {
        "version": "0.21.19",
        "date": "2026-07-13",
        "title": "Recurring events show up on the calendar",
        "screenshot": "community-calendar",
        "changes": [
            (
                "Repeating events from a linked Google calendar — like weekly Open Studio Hours — now show up "
                "on the Community Calendar for every upcoming date, not just the very first one."
            ),
        ],
    },
    {
        "version": "0.21.19",
        "date": "2026-07-13",
        "title": "Classes grouped by guild on the calendar",
        "screenshot": "community-calendar",
        "changes": [
            (
                "Class events on the Community Calendar now sit under their guild's own filter and legend, so you "
                "can show or hide any one guild's classes with a tap — and each guild's classes take on its "
                "calendar color. Set a color on your guild page to make yours stand out."
            ),
        ],
    },
    {
        "version": "0.21.19",
        "date": "2026-07-13",
        "title": "Pick your timeframe on the class catalog",
        "changes": [
            (
                "The class catalog now lets you choose how far ahead to look — the next 30, 90, or 180 days, or "
                "everything upcoming — and the count at the top finally matches exactly what's shown."
            ),
        ],
    },
    {
        "version": "0.21.19",
        "date": "2026-07-13",
        "title": "Clearer guild staff titles",
        "changes": [
            (
                "Guild staff titles read better now: you can name someone a “Guild Lead” (not only “Co-Guild "
                "Lead”), and the “Orienter” role is now “Orientator.”"
            ),
        ],
    },
    {
        "version": "0.21.14",
        "date": "2026-07-12",
        "title": "Your Guilds, wherever you are",
        "screenshot": "my-guilds",
        "changes": [
            (
                "There's a simple on/off switch for every Guild in Settings — flip one to officially join (and get "
                "its announcements) or flip it off to leave."
            ),
            (
                "And now you can pick your Guilds right from the #choose-your-guild channel on Discord — it syncs "
                "both ways with your guilds here!"
            ),
        ],
    },
    {
        "version": "0.21.13",
        "date": "2026-07-12",
        "title": "A better way to run announcements",
        "changes": [
            (
                "Anyone can now suggest an announcement for their guild — a lead or admin reviews it before it posts, "
                "and you can track or tweak your suggestion."
            ),
            (
                "And the tool itself got an upgrade: ping @everyone on Discord, preview the email live, save drafts, "
                "and choose exactly which channel it lands in."
            ),
        ],
    },
    {
        "version": "0.21.11",
        "date": "2026-07-12",
        "title": "Community events, leveled up",
        "screenshot": "community-calendar",
        "changes": [
            (
                "Anyone can now propose an event for the Community Calendar — a guild lead or admin gives it a quick "
                "thumbs-up and it's live for everyone!"
            ),
            (
                'Every event gets its own shareable page with all the details and an "Add to calendar" button — '
                "easy to share and save!"
            ),
            (
                "Pick when an event's announcement goes out, and switch on reminders 1, 3, or 7 days ahead plus a "
                '"happening now" heads-up!'
            ),
        ],
    },
    {
        "version": "0.21.10",
        "date": "2026-07-12",
        "title": "Your notifications, cleaned up",
        "screenshot": "notifications",
        "changes": [
            (
                "The bell up top now opens a full Notifications page instead of a cramped little dropdown — way "
                "easier to read and scroll on your phone!"
            ),
            (
                'And we retired the buggy "new sign-in from a new device" email that cried wolf over normal network '
                "changes — your sign-ins are still logged, just no more noise!"
            ),
        ],
    },
    {
        "version": "0.21.9",
        "date": "2026-07-12",
        "title": "QR codes, QR codes, QR codes!",
        "screenshot": "qr-codes",
        "changes": [
            (
                "Guilds, Classes, and Events all generate their own QR code now — download a ready-to-print share "
                "card and put it on a flyer or a door sign!"
            ),
            (
                "Scan it and you land right on the page, and the link keeps working even if the address changes — "
                "so your printouts never go stale!"
            ),
        ],
    },
    {
        "version": "0.20.5",
        "date": "2026-07-04",
        "title": "A home base when you sign in",
        "screenshot": "home",
        "changes": [
            (
                "Signing in now lands you on a real home page — what's coming up for you, the latest from your "
                "guilds, and quick links to everywhere you go."
            ),
            (
                'New here? A friendly "Get started" checklist walks you through your profile, joining guilds, and '
                "setting a voting preference — dismiss it anytime."
            ),
        ],
    },
    {
        "version": "0.20.1",
        "date": "2026-07-03",
        "title": "One place for how our space works",
        "screenshot": "org-info",
        "changes": [
            (
                "A new Space & Org Info page in the sidebar: a map of the space (guild spots, restrooms, exits), "
                "parking, who to contact, and the code of conduct."
            ),
            (
                "The Member Guide and Code of Conduct now live right here, instead of scattered across separate "
                "Google Docs."
            ),
        ],
    },
    {
        "version": "0.20.1",
        "date": "2026-07-03",
        "title": "Your profile & the member directory",
        "screenshot": "member-directory",
        "changes": [
            (
                "Editing your profile is safer — if a photo's too big, we keep the rest of your changes instead of "
                "losing them while you swap in a smaller one."
            ),
            (
                "New members show in the directory by default (you can still hide yourself or any detail), and "
                "connecting Discord fills in your handle for you."
            ),
        ],
    },
    {
        "version": "0.20.1",
        "date": "2026-07-03",
        "title": "Guild pages got a glow-up",
        "screenshot": "guild-pages",
        "changes": [
            (
                "Orientations, Meeting Notes, and Events are now tabs right inside the guild editor, so you set "
                "everything up in one place."
            ),
            (
                "Post a guild announcement to email plus your pick of Discord channel, staff show once with all "
                "their titles, and FAQs can be renamed and take links."
            ),
        ],
    },
    {
        "version": "0.20.1",
        "date": "2026-07-03",
        "title": "A few smaller touches",
        "changes": [
            (
                "A cleaner sign-in — we removed a confusing old staff-only login page, so it's always just your "
                "email code now."
            ),
            (
                "Pages tell you what they're for up top, already-started classes drop off the Community Calendar, "
                "and your light or dark theme follows you around."
            ),
            'And our "what\'s new" update emails got a fresh, easy-to-skim look — like this one!',
        ],
    },
    {
        "version": "0.19.17",
        "date": "2026-06-28",
        "title": "Guild pages get a leadership team and more to share",
        "changes": [
            (
                "Guild leads can now build a staff team — co-guild-leads, secretaries, treasurers, and orienters — "
                "who get the same access and are copied on the same requests; your whole leadership team shows on the "
                "guild page."
            ),
            (
                "Give a staff member a title of your own, too: type something like 'Studio Technician' or 'Glaze "
                "Technician' right beside the preset roles, and it shows on the guild page just like the rest. A "
                "person can hold a preset role and a custom title at the same time."
            ),
            (
                "Post meeting notes and agendas (each with a write-up and downloadable files or links) on a new "
                "Meeting Notes tab, and make your FAQ answers richer with an embedded video or an attached document."
            ),
            (
                "Editing a guild page is easier: each section saves on its own, photos get their own Gallery tab, and "
                "you can edit an announcement after posting instead of starting over."
            ),
        ],
    },
    {
        "version": "0.19.16",
        "date": "2026-06-27",
        "title": "Your notifications and emails, all in one place",
        "changes": [
            (
                "Your notification settings are now a single, simple grid: for every kind of update — class "
                "reminders, approvals, waitlist spots, billing receipts, announcements, and voting — you pick how it "
                "reaches you: the in-app bell, email, or a browser push. Quick 'All on / All off' buttons let you "
                "adjust a whole section at once."
            ),
            (
                "Class reminders now send on their own before each class — a single, nicely formatted reminder, and "
                "never a duplicate."
            ),
            (
                "Every email from Past Lives now arrives in one branded layout, with an easy way to manage your "
                "preferences or unsubscribe right in the footer. The 'new login' security alert also stops crying "
                "wolf — it recognizes devices you've used and only emails you about a genuinely new browser."
            ),
            (
                "New things you can opt into: makerspace-wide and guild announcements, a heads-up before monthly "
                "voting closes, and the voting results once they're in."
            ),
            (
                "When you write an email or announcement — a class welcome note, a guild's thank-you, a makerspace-wide "
                "post — you now get a simple formatting toolbar: bold, italic, headings, and bulleted or numbered "
                "lists, so what you send arrives looking polished."
            ),
        ],
    },
    {
        "version": "0.19.8",
        "date": "2026-06-26",
        "title": "Show off your skills — and open up for commissions",
        "changes": [
            (
                "Your member directory profile can now list the things you make and do — woodworking, welding, music "
                "production, web design, and lots more — with optional years of experience next to each one."
            ),
            (
                "Flip on 'Open for commissions!' and add a short note if you're happy to take on custom work, "
                "contract jobs, or consulting, so other members know they can reach out."
            ),
            (
                "The member directory is now searchable and filterable by skill, and you can show just the members "
                "who are open for commissions — handy when you're looking for the right person for a project."
            ),
        ],
    },
    {
        "version": "0.19.7",
        "date": "2026-06-26",
        "title": "The monthly guild-funding vote, leveled up",
        "changes": [
            (
                "The tools for running the monthly guild-funding vote now live in one tabbed place — an at-a-glance "
                "overview, the full Funding History, and Snapshots for locking in each month's tally — and admins can "
                "open the same voting page members see without leaving the admin area."
            ),
            (
                "The vote keeps members in the loop by email: a friendly 'polls closing soon' note showing what "
                "you're currently voting for, a nudge if you haven't voted yet, and the final funding breakdown once "
                "the results are in."
            ),
            (
                "A new Voting → Settings page sets how far ahead reminders go and the funding-pool floor, lets "
                "organizers reword any voting email, and holds the results email until an organizer reviews the "
                "numbers and sends it."
            ),
        ],
    },
    {
        "version": "0.19.5",
        "date": "2026-06-26",
        "title": "Community events and meetings on the calendar",
        "changes": [
            (
                "Guild leads can post their guild's meetings and events, and admins can post community events and the "
                "Guild Lead Meeting — all on the Community Calendar, with a Discord heads-up when they go up."
            ),
            (
                "Events can repeat on the schedule you choose — twice a month, monthly, every few months, or yearly — "
                "landing on the same weekday-of-month (like the 2nd Saturday), and they show on the guild's own "
                "calendar and in calendar exports and subscriptions too."
            ),
            (
                "A guild's published classes now appear on its Guild Calendar alongside orientations and events, and "
                "the calendars are bigger and easier to read at a glance."
            ),
        ],
    },
    {
        "version": "0.19.4",
        "date": "2026-06-26",
        "title": "Easier member management and invites",
        "changes": [
            (
                "Every member now has a login account ready to go — so if you've never signed in, an organizer can "
                "send a one-tap email invite to set it up."
            ),
            (
                "Inviting people is a proper workflow now: see who's been invited and whether they've joined, resend "
                "an invite that's been sitting unanswered, or cancel one that went out by mistake."
            ),
            (
                "The member admin is tidier — each person shows their email and whether they've signed in, people who "
                "took a class without joining are marked as non-members, and managing someone's email addresses lives "
                "in one Emails tab."
            ),
        ],
    },
    {
        "version": "0.19.3",
        "date": "2026-06-26",
        "title": "Discord, tidied — one channel, guild channels, and a personal DM line",
        "changes": [
            (
                "Every announcement the app posts — releases, makerspace-wide notes, voting reminders, and newly "
                "published classes — now lands in one tidy Discord channel, so nothing gets missed."
            ),
            (
                "Guild leads can connect their guild's own Discord channel, so a guild announcement shows up both "
                "makerspace-wide and in the guild's own channel."
            ),
            "You can now link your own Discord account to get your Past Lives notifications as a direct message.",
        ],
    },
    {
        "version": "0.19.2",
        "date": "2026-06-26",
        "title": "Book a class, get an account",
        "changes": [
            (
                "Booking a class can now set you up with a free Past Lives account in the same step — no extra sign- "
                "up and no password — so you can come back and manage your bookings."
            ),
            (
                "When you opt into the newsletter while registering, the answers you give (like your experience "
                "level) help us sign you up for the mailing lists that actually fit, instead of one generic list."
            ),
        ],
    },
    {
        "version": "0.19.1",
        "date": "2026-06-26",
        "title": "A few catalog touches",
        "changes": [
            (
                "The Classes & Workshops catalog now shows how many upcoming sessions you can actually book, not just "
                "how many class types we offer."
            ),
            (
                "Class groupings are now called 'Guilds' everywhere, matching how the makerspace already talks about "
                "its guilds."
            ),
        ],
    },
    {
        "version": "0.18.11",
        "date": "2026-06-23",
        "title": "Faster first-time sign-in on the booking site",
        "changes": [
            "Signing in to the booking site for the first time no longer asks a set of intake questions — "
            "you go straight to your account. Newsletter sign-up still happens the usual way, with the "
            "opt-in checkbox when you register for a class.",
        ],
    },
    {
        "version": "0.18.10",
        "date": "2026-06-23",
        "title": "Orienters, plus email management is back on the member page",
        "changes": [
            "Guild leads and admins can now name trusted members as 'orienters' for their guild — find the new "
            "Orienters section on the guild's Orientations settings page. Orienters can run that guild's "
            "orientations (confirm bookings, sign members off, and manage availability) without being given the "
            "keys to the rest of the guild's settings.",
            "Managing a member's email addresses is back on the member edit page: admins can add, remove, set the "
            "primary, and mark addresses verified right there, instead of digging through the old admin screens.",
        ],
    },
    {
        "version": "0.18.9",
        "date": "2026-06-22",
        "title": "Reviewers see a live preview of the class page",
        "changes": [
            "When a guild lead or admin reviews a class before it goes live, the review page now shows a live preview of exactly how the class page will look to a prospective student — right alongside the approve/decline controls — so you can sign off on what people will actually see.",
        ],
    },
    {
        "version": "0.18.8",
        "date": "2026-06-22",
        "title": "A clearer teaching dashboard and a simpler welcome-email switch",
        "changes": [
            "The teaching dashboard now matches the polished class-catalog dashboard: clearer section "
            "headings, a 'your classes this week' list, a tidy recent sign-ups table, and an at-a-glance "
            "summary — so it's easier to see what needs your attention.",
            "The class welcome email now has a simple Active on/off switch instead of a wordy checkbox, so "
            "it's obvious whether the note is being sent to new registrants.",
        ],
    },
    {
        "version": "0.18.7",
        "date": "2026-06-22",
        "title": "Admins reliably get class-review and registration emails",
        "changes": [
            "When an instructor submits a class for approval, the studio's admins now automatically get the 'a class needs review' email — it goes to everyone with the Admin role, so it works without any extra setup. Admins also reliably get the 'new registration' notifications the same way.",
        ],
    },
    {
        "version": "0.18.6",
        "date": "2026-06-22",
        "title": "Tidier 'email registrants' panel on the class roster",
        "changes": [
            "On the class roster, 'Email selected registrants' is now a button that opens the message form only when you want it — it stays tucked away by default so the roster is easier to scan. The subject and message boxes also now match the dark theme instead of showing as white boxes.",
        ],
    },
    {
        "version": "0.18.5",
        "date": "2026-06-22",
        "title": "Easier session scheduling in dark mode",
        "changes": [
            "When scheduling class sessions, the date and time boxes now open their picker as soon as you click anywhere on them, and the little calendar and clock icons are now visible in dark mode. The 'Duration' menu options are readable in dark mode too.",
        ],
    },
    {
        "version": "0.18.4",
        "date": "2026-06-22",
        "title": "Readable note box on the orientation request page",
        "changes": [
            "Fixed the 'decline with a note' box on the orientation request page — in dark mode it was showing as a white box. It now matches the dark theme like the rest of the page.",
        ],
    },
    {
        "version": "0.18.3",
        "date": "2026-06-22",
        "title": "Readable date picker on the registration page",
        "changes": [
            "Fixed the 'Choose your dates' menu on the class registration page — it was showing as a hard-to-read white box that clashed with the rest of the page. It now matches the other form fields.",
        ],
    },
    {
        "version": "0.18.2",
        "date": "2026-06-22",
        "title": "Tidier class page header",
        "changes": [
            "Cleaned up the top of each class page: the '← All classes & workshops' link and the page's edit controls now sit in their own row just beneath the header image, instead of crowding the title over the photo. The back link is now a standard gold button so it reads cleanly in light mode.",
        ],
    },
    {
        "version": "0.18.1",
        "date": "2026-06-22",
        "title": "Guild page polish: clickable member count and guild icons in the directory",
        "changes": [
            "The member count on a guild page is now a clickable chip — tap it to jump straight to the member directory showing just that guild's members.",
            "The member directory now shows each guild's icon next to its name on every member's card, so you can see who's in which guild at a glance.",
        ],
    },
    {
        "version": "0.18.0",
        "date": "2026-06-21",
        "title": "Guild orientations: book, run, and track shop orientations",
        "changes": [
            "Guilds can now run orientations right from their guild page. If you haven't been oriented for a guild yet, you'll see a small calendar of open times — pick one and request it in a click.",
            "Booking an orientation sends you a confirmation email with a calendar invite you can add to Google or Outlook. It's clearly marked 'not an official booking yet' until the guild lead confirms your time.",
            "Guild leads set everything up in one place: weekly orientation hours that fill the calendar automatically, one-off time slots, how many people fit per slot, an orientation info page, and a 'closed for orientations' switch with a note (like 'on vacation till Sept 8') for when you need a break.",
            "When someone requests an orientation, the guild lead gets an email and can confirm, decline, or suggest another time — straight from the email with no login needed, or on the hub. Declining can include a friendly note.",
            "Plans change: both members and guild leads can cancel or reschedule an orientation with one tap from their email, and everyone affected is notified automatically.",
            "Leads and admins get an 'Orientations' page listing upcoming orientations across all guilds, plus a searchable, sortable history you can filter (by guild, yours, status, completed, or date range) and export to a spreadsheet.",
            "Orientations mark themselves complete once their time passes, and the member gets a guild-lead-written 'thanks for orienting — here's what's next' email. Leads can also add a member to a slot themselves, or tick a past orientation back to 'not completed' if needed.",
            "Guild pages now have quick contact buttons: email the guild lead, jump to the guild's Discord channel, or visit its website.",
            "Joining a guild can now send a warm welcome email written by the guild lead, and quietly lets the lead know a new member has arrived.",
        ],
    },
    {
        "version": "0.17.0",
        "date": "2026-06-19",
        "title": "Clearer class types: one-off classes vs multi-session series",
        "changes": [
            "Classes that run over several dates — like a 3-week Blacksmithing course — are now clearly marked as a 'multi-session series.' One sign-up enrolls you in every date in the set, and the class and registration pages spell out all the dates so you know exactly what you're booking.",
            "When the same class is offered on more than one set of dates (say, a June run and a July run), the catalog now shows it as a single class with a 'Pick a session set' chooser, instead of looking like a pile of separate single days.",
            "The registration page now has a 'Choose your dates' dropdown listing every available run of that class, so you can switch to a different set of dates without going back to the catalog.",
            "Classes brought over from the old class site are now correctly recognized as a series when they span multiple dates, so multi-week courses no longer show up as a bunch of one-off sessions.",
            "A class that has already begun no longer shows up as open for sign-up — you can't join a multi-week series part-way through — so the catalog only lists classes you can actually still book. A series page now lists every date in the set, with ones that already happened clearly marked.",
            "Setting up or editing a class now starts with a clear, plain-language choice between a 'Single class (one date)' and a 'Multi-session series' — and you can add or remove dates anytime, even after publishing.",
            "Instructors and admins can offer the same class on another set of dates with one click ('Offer on another set of dates'); the new run starts as a draft and automatically stays grouped under the same class on the public page.",
            "We added an automated test that clicks through the site the way a real member does — requesting a sign-in code, entering it, and registering for a free class — so we catch a broken sign-in or booking flow before it ever reaches you. It runs on every change we make.",
            "The 'email me a sign-in code' page now matches the site's light theme instead of falling back to a plain, unstyled page.",
            "Pages now show a slim progress bar at the top while loading or saving, so opening a class or saving a form no longer feels frozen on slower connections.",
            "The admin 'Sync Now' button for the legacy CMS now shows a spinner and a progress bar while it runs — and estimates how long it'll take based on the previous sync.",
            "The extra questions on the registration form (like experience level or allergies) are now only the ones set up in the class admin — a few leftover sample questions that were showing by mistake have been cleared out.",
            "We now remember your answers to those questions. The next time you register for a class while signed in, your previous answers are filled in for you — ready to tweak if anything's changed.",
            "Booked before as a guest, without an account? Enter the same email and your earlier answers come back automatically, with a clear note so it's never a surprise.",
            "The registration page now offers a clear choice up front: log in, create a free account to manage your bookings, or simply continue as a guest.",
            "If you create an account, we ask these questions just once during sign-up, so you're not re-typing the same things every time you book a class.",
            "Guild Leads can now reliably manage their guild's classes and pages. If you lead a guild, you can edit and adjust any class in your guild's categories — and the Edit/Adjust controls only ever appear for that guild's lead or an admin. Previously some guild leads were treated as regular members and couldn't save their changes.",
            "Admins now have a single Registrations page listing everyone signed up across all classes, with search, quick filters by class and status, and a one-click export to a spreadsheet.",
            "Instructors and guild leads get that same Registrations view for just their own classes, so they can see who's coming to what they teach without needing admin access.",
            "From a registration, an admin can now cancel a spot, move someone to a different class, or mark a refund — marking a refund frees the seat and offers it to the next person on the waitlist, with a direct link to finish the actual refund in Stripe.",
            "Instructors can now write a custom 'welcome to my class' email that's sent automatically to everyone who signs up — perfect for what to bring, where to park, or how to prepare. It's separate from the order confirmation, off until you turn it on, and there's a 'send a test to me' button so you can see exactly how it'll look first.",
            "Instructors now see the makerspace-wide discount codes (like the members' discount) right on their Discount Codes page, clearly marked as admin-managed and read-only, alongside their own codes — so it's obvious which codes already exist before creating a new one.",
            "Guild pages now show a 'Next Meeting' card that figures out the date for you: set a cadence like 'monthly, 3rd Thursday at 6pm in Studio B' and the page always shows the upcoming date — or a one-off override, or 'TBA' if nothing's scheduled yet.",
            "Guild leads and admins can now post announcements to their guild page — pinned at the top, above the About section — and set them to automatically disappear after a date you choose.",
            "Guild pages now show quick stats (members, classes offered, next meeting) and an 'Upcoming classes' list that links straight to sign-up, plus a 'Get Involved' panel for joining the guild or teaching a class.",
            "Guild pages are now organized into tabs — Overview, Calendar, and Classes. The Calendar tab shows that guild's events and class sessions in the same week/month view as the main calendar, and the Classes tab lists all the guild's published classes with one-click sign-up.",
            "Guild pages now have a 'Recent Activity' feed showing the latest happenings — new members joining, fresh announcements, and newly published classes.",
            "Guild leads can now spotlight a 'featured class' that appears as a highlighted sign-up card at the top of their guild page.",
            "The member directory now shows which guilds each member belongs to, and you can filter the directory by guild to see everyone in one. Joining a guild is instant, and you can join as many as you like.",
        ],
    },
    {
        "version": "0.16.8",
        "date": "2026-06-18",
        "title": "Big booking-site update: classes, sign-in, approvals, and search",
        "changes": [
            "Fixed a 500 error that prevented new account signups on the booking site.",
            "The 'new sign-in from a new device' security email now uses the same polished template as other Past Lives emails.",
            "The site activity feed now correctly shows who registered for a class instead of 'System'.",
            "The '+N more dates/sessions' text on class listings is now clickable and expands to show all dates inline.",
            "The admin Classes list now groups classes that share the same title and category into one row, showing a date count badge. Changing the category on one grouped class automatically updates the rest.",
            "Class pages now spell out 'Past Lives Members' next to the member price instead of the 'PL' shorthand.",
            "The booking-site sign-in, sign-up, and welcome/onboarding pages now match the light theme used everywhere else, so the text is easy to read in light mode instead of washed out.",
            "When you register for a class while signed in, your pronouns and phone number now carry over to your profile and welcome steps, so you don't have to type them again.",
            "Class image galleries are now capped at 10 photos, with a clear '10/10' indicator when you reach the limit.",
            "Admins and instructors can now export a class's participant list to a spreadsheet (name, email, registration date, payment status) right from the registrations page.",
            "If you've already opted in to class email updates, the newsletter checkbox no longer reappears at checkout. Behind the scenes, 'first-time student' email tagging now correctly skips people we already know as members.",
            "Each class page now generates its own unique title and description for search engines, so the same class offered on different dates no longer looks like duplicate pages to Google.",
            "New classes are now approved in order — first the guild lead, then a Past Lives admin — and guild leads get a 'Needs your attention' panel for classes awaiting their sign-off.",
            "The site activity log now records exactly who confirmed, cancelled, or refunded a class registration, instead of attributing some of those actions to 'System'.",
            "Classes can now be set up as a multi-session 'Series Package' — one sign-up and one payment enrolls you in all the dates — or a 'Single Session', with a badge on listings so you can tell them apart at a glance.",
        ],
    },
    {
        "version": "0.16.7",
        "date": "2026-06-15",
        "title": "Guild logos on class pages and FOG hub topbar",
        "changes": [
            "Class pages that don't have a hero image now show the guild logo for that category instead of a blank header.",
            "The category chip on a class page now shows the guild logo alongside the category name.",
            "The member hub topbar now reads 'Past Lives FOG' and shows the version number in brackets instead of 'BETA'.",
        ],
    },
    {
        "version": "0.16.6",
        "date": "2026-06-11",
        "title": "Scheduled reminders, account page polish, onboarding fixes",
        "changes": [
            "Lease expiry and voting reminders now run automatically — no manual setup needed.",
            "Your account page on the booking site now looks great in light mode.",
            "Fixed the onboarding questions not showing which answer you selected.",
        ],
    },
    {
        "version": "0.16.5",
        "date": "2026-06-10",
        "title": "Classes offered on many dates show once",
        "changes": [
            "When a class runs on several different dates, the catalog now shows it a single time with a 'Pick a date' list, instead of repeating the same class once per date. Each date keeps its own seats, so you can see at a glance which dates still have spots.",
            "A class page now lists the other available dates for that same class, so you can switch dates without going back to the catalog.",
        ],
    },
    {
        "version": "0.16.4",
        "date": "2026-06-10",
        "title": "Class session calendar saves reliably",
        "changes": [
            "When editing a class, the session scheduling calendar now loads correctly every time, so adding or changing session dates and times saves as expected. Previously it could fail to appear when navigating straight to the edit page.",
        ],
    },
    {
        "version": "0.16.3",
        "date": "2026-06-09",
        "title": "Save confirmations show up again",
        "changes": [
            "When you save something (your profile, a guild page, your settings, and so on) the little confirmation pop-up in the corner now reliably appears again, so you get clear feedback that the change went through.",
        ],
    },
    {
        "version": "0.16.2",
        "date": "2026-06-09",
        "title": "Cleaner class titles on the Community Calendar",
        "changes": [
            "Class events on the Community Calendar no longer show the trailing date in their title (e.g. 'Intro to Welding - 6/5/26' now reads 'Intro to Welding'), matching how class names already appear in the catalog.",
        ],
    },
    {
        "version": "0.16.1",
        "date": "2026-06-09",
        "title": "Community Calendar shows each class once",
        "changes": [
            "The Community Calendar no longer lists classes twice. Each upcoming class now appears a single time and links straight to its page here on Past Lives, instead of the old classes.pastlives.space listing.",
            "The classes management Overview now has a 'View more' link under 'Upcoming classes this week' that jumps to the full Community Calendar.",
        ],
    },
    {
        "version": "0.16.0",
        "date": "2026-06-08",
        "title": "Booking-site fixes, a clearer teaching area, and email auditing",
        "changes": [
            "On the public booking site (book.pastlives.space), the 'Manage Classes & Workshops' and 'Manage My Classes' buttons now take you straight to the right place in your member dashboard — before, they led to a dead page.",
            "Hosting classes is open to every member: there's no separate 'instructor' role anymore. Any member can create and manage a class or workshop — it still needs approval from the category's Guild Lead and an admin before it goes live. The teaching pages now live at a clearer /classes/teach address, and old links redirect there automatically.",
            "The top bar on the booking site is tidier for signed-in members — the link back to your FOG dashboard and your 'Member' badge are now properly styled and only appear where they're useful.",
            "Admins have a new site-wide Activity dashboard in the sidebar. One tab shows a chronological feed of what's happening across the site — logins, profile updates, votes, tab charges, class registrations, and more. A second tab logs every automatic email the site sends (receipts, class confirmations, invites) so you can confirm exactly what went out and catch anything that failed.",
            "Guild pages got a big visual refresh. Each guild now has a proper hero banner, an image gallery with click-to-zoom, an optional intro video, a meeting schedule, a frequently-asked-questions section, and a links sidebar — all managed by guild leads and admins from a redesigned full-page editor (the old pop-up is gone). Members can now join or leave a guild in one click, and guilds that opt in can show a roster of their members, respecting each member's directory-privacy settings.",
            "Past Lives now keeps you in the loop with notifications. A bell in the top bar collects everything relevant to you — a new class or workshop going live, your registration or refund clearing, a waitlist spot opening up, your tab being charged, monthly voting reminders, and more. Under Settings → Notifications you choose which of these also reach you by browser push or email; the bell always shows the full list. For your account's safety, you'll always get an email when someone signs in from a new device.",
            "The classes admin area has a brand-new Overview page that greets you when you open it. At a glance you can see which classes are waiting for your approval (with Review and Approve buttons right there), which classes have people on a waitlist, your most recent sign-ups, and a 14-day chart of how registrations are trending.",
            "The management tabs are simpler. Instead of seven tabs across the top, there are now just three — Overview, Classes, and Settings. Categories, discount codes, registration questions, and waiver text all live together under Settings now, so the day-to-day tabs stay uncluttered.",
            "There's a 'View live catalog' link in the management header so you can hop straight to the public booking site to see your classes the way visitors do.",
            "Anyone teaching a class gets the same upgrade on their teaching dashboard — opening it now shows your drafts, anything awaiting review, your latest sign-ups, and your waitlists at a glance, with a one-tap 'Create your first class' if you're just getting started.",
            "Opening a class now puts everything for that class in one place — a tabbed workspace for its registrations, waitlist, and discount codes, with Edit and Preview right at the top.",
            "You get that same per-class workspace for your own classes — open any class you teach to see its sign-ups, waitlist, and discount codes in one place. Your teaching profile now lives in the top-right account menu.",
        ],
    },
    {
        "version": "0.15.0",
        "date": "2026-06-08",
        "title": "Old-CMS classes import and sync automatically every day",
        "changes": [
            "The class catalog and Community Calendar now refresh themselves automatically every morning. New workshops imported from the old class site (classes.pastlives.space) and any updated guild calendars show up on their own — nobody has to open the calendar or press a button to pull them in. The 'Sync Now' buttons in Site Settings are still there whenever you want an instant refresh.",
        ],
    },
    {
        "version": "0.14.2",
        "date": "2026-06-07",
        "title": "Booking site polish: upcoming-only listing, pagination, and cross-site login",
        "changes": [
            "The class list now only shows classes with upcoming sessions — past events no longer clutter the page.",
            "Classes are paginated 25 at a time, sorted by closest upcoming date first. A Previous/Next bar appears at the bottom when there's more than one page.",
            "The sticky category strip has been removed. A small arrow button appears in the bottom-right corner when you scroll down — click it to jump back to the top.",
            "If you're already logged into pastlives.space, visiting your account on book.pastlives.space will automatically sign you in without a second login prompt.",
            "The Past Lives logo in the top-left of the booking site now links to the class list instead of the main website.",
            "Class detail pages no longer show a blank instructor tile when the instructor isn't on record.",
            "Class descriptions now display with proper paragraph breaks instead of running together as a single block of text.",
        ],
    },
    {
        "version": "0.14.1",
        "date": "2026-06-06",
        "title": "Classes from the old CMS now show up on book.pastlives.space",
        "changes": [
            "All classes from classes.pastlives.space now automatically sync into book.pastlives.space every 15 minutes — flip the toggle in Site Settings → Legacy CMS to turn it on. When you're ready to manage classes exclusively here, just turn it off.",
            "Imported classes link to the right instructor automatically when the class title includes 'with [Instructor Name]'. Once linked, the connection is never overwritten by a re-sync so any manual adjustments stick.",
            "Hero images from the old CMS are served through a proxy so class cards look complete right away — run 'Download legacy images' when you're ready to move them into plfog's own storage.",
            "The Members admin table now shows how many classes each instructor is teaching, with a one-click link that filters the Classes table to just their classes.",
            "The Classes admin table now has an instructor dropdown filter so you can quickly see every class taught by a specific person.",
            "Site Settings has a new Legacy CMS tab with the sync toggle, a 'Sync Now' button for on-demand imports, and an instructor match table that shows how many of each instructor's classes were automatically linked by name.",
        ],
    },
    {
        "version": "0.14.0",
        "date": "2026-05-28",
        "title": "Classes CMS round 2: rebrand, waitlist, dual approval, activity feed, and more",
        "changes": [
            "The public Classes site at book.pastlives.space now matches the look of pastlives.space. The old blue Squarespace backdrop is gone, the whole portal uses the Past Lives palette, and there is a slim PAST LIVES header at the top with Home, Guilds, Membership, Classes, and Contact links so it feels like one site to your customers.",
            "Light theme is the default on book.pastlives.space. The dark/light toggle in the top bar still works on the member hub the same way it always has.",
            "Class detail pages are now full width with bigger, easier-to-read text (16px floor everywhere). New sections appear automatically when the class has them: a video block, gallery, schedule, materials and prereqs side by side, an instructor card, a guild card, a short FAQ, related classes, and a sticky booking card on the right that follows you as you scroll.",
            "Instructors can paste a YouTube link onto any class and it embeds right in the description. Works with watch links, youtu.be short links, embed URLs, and shorts.",
            "Each of the 14 guild categories has a small icon next to its name on the public list. Admins can swap any icon for a custom one from the category admin.",
            "All Classes page got a Filter button. Sort by category from a dropdown, then click Filter to pick instructors, set a price range, or show only classes with a member discount, free classes, or classes with upcoming sessions. The URL updates as you filter so you can share a link to exactly what you are looking at.",
            'Discount codes can now be made for a single class. Open Edit on any class and look at the new "Discount codes for this class" section. Codes you make here only work for that class. There is also an Auto-apply toggle, which drops the price for every registrant without them needing to type the code. Global codes still live on the Discount Codes tab and still require an admin to make.',
            "New approval flow for classes. When an instructor submits a class, it now goes to both an admin AND the Guild Lead of that class's guild (when one is set). Each reviewer gets an email with their own link so they can approve, request changes, or decline with notes. The instructor gets an email each step of the way: the class only goes live when every reviewer has approved. Requesting changes or declining bounces the class back to Draft so the instructor can edit and resubmit.",
            'Waitlists for sold-out classes. When a class is full the Register button changes to "Join the waitlist" and the registrant gets confirmation of their spot in line, no charge. When somebody cancels, we email the next person on the waitlist with a 24-hour window to register. Admins see the waitlist on each class\'s admin page.',
            "New Activity tab in the Classes admin area. Reverse chronological feed of everything that happens with classes: who created a class, who submitted it for review, who approved or requested changes, who registered, who got refunded, who joined the waitlist, when a discount code was used. Filter chips for Classes, Registrations, Waitlist, and Discount codes plus a search box for class title, name, or email.",
            "Fixed invisible text on the Register button and category chip on class detail pages in light theme.",
            "Community Calendar now uses the same clean background as the rest of the site — the old Squarespace backdrop image is gone.",
            "Registration form inputs and text are now readable in both light and dark mode. Dropdowns on the form also match the rest of the field styling.",
        ],
    },
    {
        "version": "0.13.2",
        "date": "2026-05-26",
        "title": "Image uploads work on the New Class form",
        "changes": [
            "The 'New Class' form now has the same hero image and gallery image upload experience as the edit form — drag-and-drop zone with instant preview instead of a plain file picker. Gallery images are uploaded when you hit Save.",
        ],
    },
    {
        "version": "0.13.1",
        "date": "2026-05-26",
        "title": "Students on class detail, dollar pricing, admin create form fix",
        "changes": [
            "The class detail page now shows all registered students right on the page. You can see who signed up, their email, payment status, and registration date without leaving the class. Cancelled or refunded registrations appear dimmed for reference.",
            "Admins can email students directly from the class detail page. Check the students you want to reach, click 'Email selected students', type your message, and send. Recipients are BCC'd so no one sees the others' emails, and every send is logged.",
            "Price fields on class forms now accept dollars (e.g. 80.00) instead of cents (e.g. 8000). Same for the fixed discount field on discount codes. Existing prices display correctly; nothing changes behind the scenes.",
            "Fixed the discount code list showing dollar amounts wrong (e.g. '$2000.00' instead of '$20.00').",
            "The 'New Class' form for admins now has the session calendar and gallery image section, matching the edit form.",
            "The 'View Publicly' button on the Classes page now opens book.pastlives.space in a new tab.",
        ],
    },
    {
        "version": "0.13.0",
        "date": "2026-05-25",
        "title": "Visual session calendar, AJAX images, admin polish",
        "changes": [
            "Instructors now schedule class sessions on a visual month-view calendar — click a day, pick a time and duration, done. No more fiddling with raw datetime fields. Sessions show as gold dots on the calendar and are listed below with times.",
            "Gallery images upload instantly without hitting Save. Drag and drop to reorder, inline alt text, one-click delete. Max 10 images, 3 MB each, auto-compressed.",
            "Hero image also uploads instantly via drag-and-drop — no more needing to save the whole form.",
            "Instructors get an email notification when someone registers for their class. Admins can subscribe to registration notifications via CLASS_ADMIN_NOTIFY_EMAILS.",
            "The registration detail page got a full redesign — all info at a glance in clean card sections.",
            "Fixed the admin registrations page showing payment amounts in cents instead of dollars ($7200 → $72.00). Stripe charges were always correct — display-only bug.",
            "Member pricing now says 'for PL members' on public class pages.",
            "Removed the 'Recurring pattern' and 'Requires model release' fields from class forms — they were confusing and unused.",
            "Fixed hero image stretching on the edit page preview.",
            "Fixed gallery hover-zoom distorting images that aren't 16:9.",
            "Registrant names are now clickable links in the admin registrations table.",
        ],
    },
    {
        "version": "0.12.0",
        "date": "2026-05-25",
        "title": "CMS polish: styled emails, admin consolidation, instructor registrations",
        "changes": [
            "Class confirmation and reminder emails now match the polished look of our login emails — dark card with gold accents, clear schedule and location details, and a big 'Manage Registration' button so you can view or cancel right from the email.",
            "Billing receipt emails got the same treatment — itemized charges in a clean card layout with a link to your Stripe receipt.",
            "All email links are now full clickable URLs instead of bare paths, so they work in every email client.",
            "The classes admin area is now one consolidated workspace — Settings and Registration Questions are tabs alongside Classes, Registrations, Instructors, Categories, and Discount Codes. No more hunting across separate pages.",
            "Instructors now see their registrations grouped by class. Each class section expands to show its registrants and has its own email compose tool, so you can quickly see who's signed up and message them without switching tabs.",
            "Registration Questions (the custom questions asked on every class sign-up) can now be managed from the classes admin tab instead of the Django admin.",
            "Fixed paid class registration not redirecting to Stripe checkout when submitting the registration form.",
            "Discount codes created by instructors now require admin approval before they can be used. Admins see an approve/unapprove toggle on each code.",
            "Every admin table (Classes, Registrations, Instructors, Categories, Discount Codes, Questions) now has a search bar, clickable sortable column headers, and pagination.",
        ],
    },
    {
        "version": "0.11.7",
        "date": "2026-05-23",
        "title": "Fix: production deploy blocked by migration conflict",
        "changes": [
            "Fixed a migration conflict that was preventing the app from starting on production. Two independent changes landed on the same migration number and Django refused to run until they were merged — no data was affected, the app just couldn't start.",
        ],
    },
    {
        "version": "0.11.6",
        "date": "2026-05-23",
        "title": "Classes: smarter photos, newsletter tagging, custom questions, and instructor messaging",
        "changes": [
            "Class photos now auto-resize on upload — drop in a 4MB iPhone HEIC and we'll downscale and convert it to a clean web JPEG. The hero image gets a built-in crop tool: pick the 16:9 focal point and the public page crops to your selection.",
            "Newsletter tagging picks up more context. When someone registers for a class, their newsletter contact now gets tagged with the category, the instructor, the guild (if the category is linked to one), and 'first-time-student' if it's their first class with us.",
            "Categories can now be linked to a guild. Admins set the link from the category edit page, and it drives the new guild Mailchimp tag.",
            "When a member finishes onboarding on book.pastlives.space, their account is now synced to Mailchimp with their persona, referral source, and category interests as tags — so newsletter campaigns can target by 'how they found us' or 'what they're interested in', not just who's already registered for a class.",
            "Tour status from Simplybook is now pulled into member profiles. The account overview and instructor roster can show whether a member has completed a tour, refreshed at most once a day per member.",
            "You can now set up global registration questions that get asked on every class registration. Admins manage the list from the Django admin — short text, paragraph, yes/no, or pick-one — and instructors see the answers next to each registrant on their registrations page.",
            "Instructors can now email their students directly from the registrations page. Tick the rows you want to message, type a subject and body, and send. Recipients are BCC'd so no one sees the others' addresses, and every send is logged for the audit trail.",
        ],
    },
    {
        "version": "0.11.5",
        "date": "2026-05-22",
        "title": "Classes: multi-image galleries with hover-zoom + a preview button",
        "changes": [
            "When you create or edit a class, you can now upload extra gallery photos in addition to the hero banner — finished pieces, the studio space, tool close-ups, anything that helps a visitor picture the workshop. Each photo gets an optional alt-text field and a sort number so you can put them in the order you want.",
            "Picking a new image instantly shows a thumbnail in the form so you can tell which file you grabbed before saving.",
            "On the public class page, the hero now leads a real gallery — thumbnails along the bottom, hover anywhere on the main image to magnify a close-up of that spot (great for showing detail on glasswork, ceramics, fiber pieces), and click to open a full-screen lightbox with arrow-key navigation.",
            "A new 'Preview' button on the class form opens the public class page in a new tab — instructors and admins can see exactly how the page will look to visitors before submitting for review or publishing, including drafts.",
            "The hero image field now has a small '?' tooltip showing the recommended dimensions (1600 × 900, 16:9) and max file size so you know what to aim for before uploading.",
        ],
    },
    {
        "version": "0.11.4",
        "date": "2026-05-22",
        "title": "book.pastlives.space: an obvious way to sign up",
        "changes": [
            "The public site at book.pastlives.space now has a 'Sign up' button right in the navbar next to 'Log in' — new visitors don't have to know to click through Log in to find the signup link anymore.",
            "Signing up on book.pastlives.space is always open, regardless of the invite-only setting in Site Settings. The invite-only gate was meant for makerspace membership signups on members.pastlives.space — it shouldn't be blocking someone from making a book account just to track their class registrations.",
        ],
    },
    {
        "version": "0.11.3",
        "date": "2026-05-22",
        "title": "Site Settings: add multiple calendars to the Community Calendar",
        "changes": [
            "Site Settings now has its own Calendar tab with a list of named calendar feeds — General, Workshops, Open Studio, anything you want. Click '+ Add calendar' to drop in another iCal URL, give it a name and a color, and it shows up on the Community Calendar legend with its own colored chips.",
            "The Site Settings page is now organized into two tabs (General and Calendar) so finding registration, MailChimp, and analytics settings stays out of the way when you're just managing calendars.",
            "Existing General Calendar setups were automatically migrated into the new list as a 'General Calendar' feed — nothing to do, no events go missing.",
        ],
    },
    {
        "version": "0.11.2",
        "date": "2026-05-22",
        "title": "Delete classes outright, and a $1 minimum on paid classes",
        "changes": [
            "Admins can now delete any class that has no registrations — not just drafts. The Delete button shows up on the class detail page next to Archive whenever no one has signed up yet. Classes with registrations still have to be archived so we keep the history.",
            "When you set a price on a class, the minimum is now $1.00. Anything cheaper should just be a free class — tick 'This is a free class / workshop' and the price goes to zero. (This only affects new prices you set going forward; classes that were already priced below a dollar still work.)",
        ],
    },
    {
        "version": "0.11.1",
        "date": "2026-05-22",
        "title": "Class registration: friendlier error when a price comes out too low",
        "changes": [
            "Fixed a bug where the 'Continue to payment' button on a class registration page could show an error screen if the total ended up less than $0.50 (for example after applying a big discount code). You'll now see a clear message asking you to remove the code or get in touch with the studio.",
        ],
    },
    {
        "version": "0.11.0",
        "date": "2026-05-22",
        "title": "Classes & workshops are now open to everyone at book.pastlives.space",
        "changes": [
            "We launched a brand new public site at book.pastlives.space — anyone can browse our classes and workshops, sign up, and pay, no Past Lives account required. Send the link to friends, family, or anyone curious about taking a class with us.",
            "Members: nothing changes for you. Classes still live in the sidebar at /classes/ inside your hub, the member discount still applies automatically, and your dashboard looks exactly the same as it did yesterday.",
            "One login, two doors — if you're signed in on members.pastlives.space and you wander over to book.pastlives.space, we still recognize you and your member discount still applies. Handy for grabbing a link to share or showing a class to a non-member friend looking over your shoulder.",
            "Anyone who registers for a class can now create a free account and log in at book.pastlives.space to see their upcoming classes, past classes, receipts, and edit their contact info. No membership required — it's just a lightweight place for class takers to keep track of what they've booked.",
            "A short 3-step welcome runs the first time someone signs in at book.pastlives.space — it asks if they've ever taken a class with us before, collects a preferred name and a day-of contact phone, and lets them check off which kinds of classes they'd like to hear about. Every step is skippable, and we only email them about new classes in the categories they picked.",
            "Booked as a guest and forgot to make an account? There's a new 'Find my booking' page at book.pastlives.space/account/lookup/ — type in your last name and the confirmation order number from your email (the friendly PL-XXXX-YY one we now print at the top of every confirmation) and we'll pull up your booking. No password needed.",
            "Instructors can hop over to book.pastlives.space anytime to see classes they've personally signed up for as a student. A small link inside their account points back to their teaching dashboard on members.pastlives.space — that's still where class management happens.",
            "Registrations from both surfaces land in the same place — admins manage everything from one Classes admin in the members hub, so there's nothing new to learn and nothing to keep in sync.",
        ],
    },
    {
        "version": "0.10.1",
        "date": "2026-05-21",
        "title": "Stop login-page email spam from draining our daily quota",
        "changes": [
            "The login page now has a few new defenses against the bot that's been hammering it the last couple of days. The form looked too easy to spam, which is why our daily email quota was getting maxed out: every random email someone typed in (real or not) triggered an outgoing email, and Resend has a daily cap.",
            "Random emails no longer trigger an outgoing 'no account found' message. After you submit the login form, the page now says 'If you are a member at Past Lives, we sent a sign-in code to ...' — that wording is intentional. It doesn't confirm or deny whether the email is on file, which protects member privacy and also stops bots from using the form to enumerate real addresses. Nothing actually gets emailed unless the address matches a member.",
            "Tighter rate limiting on the login page: at most 5 requests per minute from any one device, and at most 3 codes per hour to the same email address. Repeated submissions beyond that quietly drop instead of sending.",
            "Added an invisible 'honeypot' field that real browsers ignore but most bots dutifully fill in. If it comes back filled, the request is treated the same as if the user had been rate-limited.",
            "Hard daily and hourly caps on total login emails (configurable, defaulting to 100/hour and 500/day) as a last-resort safety net. If anything ever slips past the other defenses, the site will stop sending login emails before the bill runs over instead of after.",
        ],
    },
    {
        "version": "0.10.0",
        "date": "2026-05-05",
        "title": "Mailchimp signup + site-wide Google Analytics",
        "changes": [
            "Class registration now has an opt-in checkbox — tick it and you'll start getting our newsletter about future classes, events, and what's happening at Past Lives. Leave it unchecked and nothing changes.",
            "Brand new Newsletter page at /newsletter/ — anyone (members or not) can drop their email in to get our updates, no account required.",
            "Site Settings now has working MailChimp credentials — paste in the API key and audience ID and the signup paths above start sending people to the right list, tagged so we can tell who came from a class versus the standalone newsletter form.",
            "Google Analytics now tracks every page on the site instead of just the public Classes pages — set the GA4 ID once in Site Settings and every member page, every public page, every auth page reports back. The Django admin is excluded so we don't pollute analytics with internal traffic.",
        ],
    },
    {
        "version": "0.9.0",
        "date": "2026-05-01",
        "title": "Calendar QOL + a redesigned Profile page that lets you control what's public",
        "changes": [
            "The Calendar's month view used to show the calendar month you're in, which meant by the end of April you were staring at a near-empty grid. It now shows a rolling 4-week window starting from the current week, so you always see this week plus the next three. The Prev/Next arrows step the window forward or back by 4 weeks at a time.",
            "Event titles are now visible right on the calendar grid in both Week and Month views — no more guessing which colored dot is which event. Click any title and the page jumps to that event in the list below and gives it a quick highlight. If the event lives on a different page, the list auto-flips to that page first.",
            "Today's column on the calendar grid now has a thin red line across the top, and any event that's happening right now gets a red outline plus a pulsing 'Live now' pill on its card.",
            "Profile settings got a new design with per-field privacy controls. Next to each field — phone, email, Discord, pronouns, photo, about-me, etc. — there's a small Public/Hidden eye-icon toggle so you can decide exactly what other members see when they look you up. A live preview on the side shows your directory card update in real time as you flip toggles or type. A panel at the bottom lists everything that's never shown to other members (legal name, billing, emergency contact, internal notes) so there's no ambiguity.",
            "On mobile, the Feedback button used to live at the bottom of the sidebar where it could get cut off and was hard to find. There's now a yellow Feedback bubble pinned to the bottom-right corner of every page on mobile so it's one tap away. The mobile sidebar also handles short screens better and respects the device's safe area.",
        ],
    },
    {
        "version": "0.8.8",
        "date": "2026-04-27",
        "title": "Hotfix: site layout broken for everyone",
        "changes": [
            "Fixed a bug introduced in the last release where the entire site rendered at half-width with a stray line of text near the top — the page was unusable. Everything looks correct again.",
        ],
    },
    {
        "version": "0.8.7",
        "date": "2026-04-26",
        "title": "Free classes & workshops — and a fix for the Register page styling",
        "changes": [
            'When you create or edit a class, there\'s now a "This is a free class / workshop" checkbox right above the price field. Tick it and the class is free — members and visitors register without entering any payment info, and they get a confirmation email immediately.',
            'Free classes show as "Free" everywhere on the site (the class card, the detail page, and the registration summary) instead of a $0 price tag.',
            "Fixed a bug where the Register page on a class could load with no styling if you clicked through from another page in the same browser tab. The page now keeps its full styling no matter how you arrive at it.",
        ],
    },
    {
        "version": "0.8.6",
        "date": "2026-04-25",
        "title": "Members can sign up for classes online — plus a unified Classes page",
        "changes": [
            "You can now register for classes and workshops directly on the website. Click Register on any class page, fill out a short form, sign the liability waiver online, and pay with a card through Stripe — no more email back-and-forth.",
            "Members get the member discount applied automatically when the email on the form matches a verified member email.",
            "Free classes skip the payment step and confirm immediately — you'll get a confirmation email right away.",
            "Discount codes are honored on the registration form, and stack on top of the member discount.",
            "Every confirmed registrant gets a confirmation email with the schedule, location, and a personal link they can use to view or cancel their spot — no login needed.",
            "There's now a single Classes & Workshops page in the sidebar — the same page everyone sees, members and visitors alike. Admins and instructors get a Manage button right on that page that opens the classes admin (or the Teaching dashboard for instructors), so you don't lose anything, it's just one entry point instead of two.",
            'Fixed: a class you publish now shows up on the Classes & Workshops page right away, even before its sessions are scheduled — it appears with an "Upcoming dates TBA" note until you add dates.',
        ],
    },
    {
        "version": "0.8.5",
        "date": "2026-04-25",
        "title": "One unified view — Classes, Calendar, Payments, and admin all live in the FOG dashboard",
        "changes": [
            "The Classes page at /classes/ is now public for everyone — no admin toggle needed.",
            "Everything now lives inside the FOG dashboard with the same sidebar and topnav. No more bouncing into a different admin layout — Classes, Community Calendar, Payments, Reports, Manage Members, Site Settings, and the new Voting Dashboard all open in the hub.",
            "Admins see one sidebar with everything they need: Community Calendar, Manage Classes, Manage Members, Member Directory, My Tab, Payments, Reports, Site Settings, and Voting Dashboard.",
            'The "Viewing as" dropdown stays in the topbar so admins can preview the hub as a Member, Guild Officer, Instructor, or Guest without switching accounts.',
            'All instructors are members by default — the "Non-member Instructor" label is gone, instructors are just instructors.',
        ],
    },
    {
        "version": "0.8.4",
        "date": "2026-04-23",
        "title": "Classes: public portal, instructor dashboard, and calendar integration",
        "changes": [
            "Browse classes at a new public page at /classes/ — grouped by category with sticky filters, hero, and polished card grid; gated behind an admin toggle in Classes → Settings so it only goes live when you're ready",
            "Class detail and instructor profile pages with schedule, description, pricing, member discount, and links back to the portal",
            "New Teaching section in the hub sidebar for instructors — lets them see their classes, submit drafts for admin review, view their registrations, and edit their own profile (photo, bio, website, social handle)",
            "Classes admin gets a status filter (All / Draft / Pending / Published / Archived) with live counts and dims archived rows, plus a Delete button for drafts with zero registrations — Archive preserves history; Delete is only for cleanup",
            "Published local classes now show up on the Community Calendar alongside the external feed during the transition period",
            "Scheduled reminder email infrastructure in place — `manage.py send_class_reminders` emails confirmed registrants for sessions starting soon (reminder window configurable in Classes → Settings)",
            "Small admin polish: shortened URLs from /classes/admin/classes/ → /classes/admin/, 'View public portal' link in the Classes admin header, proper small red delete buttons with confirmation modals across Categories, Discount Codes, and Classes",
        ],
    },
    {
        "version": "0.8.3",
        "date": "2026-04-23",
        "title": "Fix: logout page styling",
        "changes": [
            "Fixed the logout confirmation page so it uses the normal Past Lives site styling instead of showing an unstyled default page",
        ],
    },
    {
        "version": "0.8.2",
        "date": "2026-04-21",
        "title": "Photos for member profiles, guild banners, and classes",
        "changes": [
            "You can now upload a profile photo on your User Settings → Profile page — your photo replaces your initials in the member directory and in the top-right of the navbar",
            "Guild leads can add a banner image to their guild's page — it appears at the top of the guild detail page above the name and About section, with a tooltip explaining the recommended dimensions",
            "Class images (categories, instructors, and individual classes) and other photos uploaded through the admin now stay around between deploys instead of disappearing",
            "Image uploads have a 5 MB limit so things stay snappy and don't blow up our storage bill",
            "Replacing or removing a photo now also deletes the old file from storage so we don't accumulate orphaned images over time",
            "Friendlier image-upload UI everywhere — the small 'Currently / Clear checkbox' has been replaced with a thumbnail preview and a small red 'Remove' (or 'Delete photo') button that asks for confirmation before deleting",
        ],
    },
    {
        "version": "0.8.1",
        "date": "2026-04-21",
        "title": "Hotfix: deploy failing on Render",
        "changes": [
            "Fixed a deploy failure that was blocking new releases from going live on Render — the build step that packages up static files was being rejected because it couldn't see the production database settings. It now uses a harmless placeholder just for that build step, so deploys go through again",
        ],
    },
    {
        "version": "0.8.0",
        "date": "2026-04-21",
        "title": "Classes: admin tabs foundation",
        "changes": [
            "New Classes admin area (admins only) with six tabs — Classes, Categories, Instructors, Registrations, Discount Codes, and Settings — so we can build out the makerspace's class catalog natively inside Past Lives without needing the old booking site",
            "Admins can create categories, invite instructors (who get their own login), build classes with a full set of fields (prerequisites, materials, capacity, member discount, scheduling), and manage discount codes — the public registration flow + Stripe payments come in a follow-up release",
            'Instructors now exist as a first-class role: someone can be a member, an instructor, or both. The "Viewing as" dropdown recognizes the new role so admins can preview what teachers see',
            "Class titles and category names auto-fill the URL slug as you type — saves a step every time you make a new one",
        ],
    },
    {
        "version": "0.7.3",
        "date": "2026-04-17",
        "title": "Admin role preview in the hub",
        "changes": [
            'Admins and guild officers now have a new "Viewing as" button in the topbar — use it to preview the hub the way a plain member or guild officer would see it, without logging out. Unchecking a role hides that role\'s UI for the current session only.',
        ],
    },
    {
        "version": "0.7.2",
        "date": "2026-04-15",
        "title": "Fix: login page CSS broken after navigating from the hub",
        "changes": [
            "Fixed a bug where navigating to a protected page with an expired session would redirect you to the login page with completely broken styles — it now does a proper full-page load so the login page looks correct",
        ],
    },
    {
        "version": "0.7.1",
        "date": "2026-04-15",
        "title": "Calendar: Classes sync, configurable colors & pagination",
        "changes": [
            "Classes from classes.pastlives.space now show up on the Community Calendar — click the title to go straight to the registration page",
            "Classes have their own color on the calendar, separate from General events — customize it in Site Settings",
            "The event list now shows 10 events per page with prev/next arrows so the page doesn't get overwhelmed",
            "Admins can configure the Classes color in Site Settings → Community Calendar alongside the existing General color",
        ],
    },
    {
        "version": "0.7.0",
        "date": "2026-04-15",
        "title": "Community Calendar",
        "changes": [
            "New Community Calendar — see all upcoming makerspace events in one place, with week and month views so you can plan ahead",
            "Filter events by guild to find workshops, meetups, and events from just the guilds you care about",
            "Export any event to your personal calendar app (Google, Apple, Outlook — whatever you use) with one click",
            "Guild officers can now link their guild's Google Calendar so events sync automatically — no more posting events in two places",
        ],
    },
    {
        "version": "0.6.7",
        "date": "2026-04-15",
        "title": "Current Vote Standings — real totals",
        "changes": [
            "Current Vote Standings now show the real point totals — a guild that picked up votes at multiple ranks (say, one 1st, two 2nds, and three 3rds) was being displayed with those counts multiplied together instead of added, so some guilds were appearing at 60 points when the actual total was 17. Fixed on the Guild Voting page and the admin dashboard",
        ],
    },
    {
        "version": "0.6.6",
        "date": "2026-04-15",
        "title": "Voting hotfixes, User Settings, and sidebar polish",
        "changes": [
            "The Guild Voting page is now split into three tabs: Current Standings (live points), New Votes This Month (only votes cast or changed since the last snapshot), and Last Month's Results (the most recent snapshot with funding breakdown)",
            "Your vote still rolls over automatically every cycle — once you've submitted it, it stays in place and keeps counting until you change it",
            "The admin Voting Preferences page now shows a per-member voting history pulled from past snapshots, so you can audit how anyone's picks contributed to the totals cycle by cycle",
            "New unified User Settings page at /settings/ with Profile and Emails tabs — the old Manage Email Addresses page that looked like the login screen is gone, and adding/removing/verifying emails now happens inside the hub layout",
            "On the Emails tab, Manage Email Addresses and Email Preferences are now two separate cards, and the Re-send Verification button only appears when the selected email isn't verified yet",
            "The user menu in the top-right is now just Settings + Log Out — everything else lives inside the new User Settings page",
            "Resources links (Member Guide + Code of Conduct) and Feedback now live together in the bottom of the left sidebar, each styled consistently with the rest of the nav — no more floating button overlapping other content",
            "Fixed a Postgres error that was blocking the last deploy on Render — the migration that clears legacy product data and drops old columns now commits its cleanup before altering the schema, so the two steps can't collide in a single transaction",
        ],
    },
    {
        "version": "0.6.5",
        "date": "2026-04-14",
        "title": "Edit products in place",
        "changes": [
            "You can now edit a product right from your guild page — click the small pencil on a product card and the same pop-up you use to add products opens up pre-filled with that product's name, price, and revenue split",
            "Saving updates the product in place — no need to delete and re-add it just to fix a typo or change the percentages",
            "Product cards are cleaner — the 'Admin 20% · Guild 80%' split summary is hidden now that you can see and edit the same info from the product's edit pop-up",
        ],
    },
    {
        "version": "0.6.4",
        "date": "2026-04-14",
        "title": "Edit Your Guild Page Directly",
        "changes": [
            "Guild leads can now edit their guild's page without going through the admin — just open your guild page and you'll see an 'Edit Guild Page' button next to the title and an 'Add Product' button in the Products section",
            "Admins and guild officers see the same edit buttons on every guild page",
            "Changing the guild name or description happens right in a pop-up window on the guild page",
            "Adding a new product now opens a pop-up with the revenue-split builder instead of sending you to the admin panel — closing and re-opening the pop-up resets it cleanly",
            "Existing products show a small delete button in the corner so you can remove them without leaving the page",
            "The old 'Guilds' entry in the admin panel is gone — everything guild-related lives on the public guild pages now",
            "My Tab page now has a hoverable '?' next to the title that explains how tabs work, and the next-charge date shows as subtext below the heading instead of buried in the middle of the card",
            "Small copy fix on guild pages so the payment-method prompt reads 'add items to your tab' instead of just 'add items'",
            "Sidebar 'Guilds' section starts expanded by default, and remembers your preference if you collapse it",
        ],
    },
    {
        "version": "0.6.3",
        "date": "2026-04-14",
        "title": "Flexible Product Revenue Splits",
        "changes": [
            "Products can now split revenue across multiple guilds and admin in any combination — for example, a $10 product can be set up so 20% goes to admin, 60% to the Ceramics Guild, and 20% to the Art Framing Guild",
            "The percentages have to add up to 100%, but otherwise you can mix recipients however you like",
            "The Add Product form has been redesigned: it now appears inline on the guild edit page (no more popup) with a live preview that shows exactly how a sale will be divided up",
            "The 'active/inactive' toggle is gone — if a product exists, it's available; if you delete it, it's gone",
            "Existing products and past tab entries were cleared during this upgrade — you'll need to re-add your products with their new split configuration",
        ],
    },
    {
        "version": "0.6.2",
        "date": "2026-04-14",
        "title": "Local Dev Setup & Cart Toast Fix",
        "changes": [
            "New developer setup: clone the repo, run make setup, make db-up, make server — and you're running locally with PostgreSQL",
            "New 'make db-pull-prod' command downloads a copy of the production database for local testing",
            "Login codes now appear directly on screen during local development — no more checking the terminal",
            "The 'added to cart' notification now appears in the center of the screen instead of covering the tab balance",
        ],
    },
    {
        "version": "0.6.1",
        "date": "2026-04-13",
        "title": "My Tab & Guild Page Hotfixes",
        "changes": [
            "My Tab page redesigned: Current Balance and Pending Charges are combined into one card, and Payment Method now lives at the bottom",
            "My Tab page now shows your saved card with a link to add, replace, or remove it",
            "My Tab page tells you when your tab will be processed by Stripe so there are no surprises",
            "Removing a pending charge from your tab is now a one-click trash button — no confirmation dialog",
            "Removed the 'Add to Tab' form from the My Tab page — add items from guild pages instead",
            "Guild pages now have a 'General Consumables' card with a quantity picker so you can add custom-priced items directly to your tab",
            "Fixed a bug where one action would show two toast notifications at once",
            "Cleaner spacing between cards on the My Tab page and the payment-method setup page",
        ],
    },
    {
        "version": "0.6.0",
        "date": "2026-04-11",
        "title": "Single Stripe Account, Revenue Splits & Reports",
        "changes": [
            "Billing now runs through a single Past Lives Stripe account — each guild no longer links its own account separately",
            "Every charge has a configurable admin/guild percentage split (20% / 80% by default) — set site-wide or per-product",
            "New 'split equally across all guilds' option on any product or custom charge — the guild share gets divided evenly between active guilds",
            "New 'Enter Your Own Price' form on every guild page — add a custom-price item to your tab without an officer creating a product first",
            "Guild product cards now have an 'Add to tab' button so members can add items in one click from the guild page",
            "New admin Reports page (Payments → Reports) with date/guild/status filters and CSV export — shows exactly how much to pay each guild",
            "Product editor on the admin now includes admin % and split-mode controls for officers",
            "Saved card required again for tab entries (the direct-keys Checkout flow is gone)",
            "Cleanup: removed Stripe Connect OAuth flow, per-guild direct-keys management, and per-guild webhook endpoints — they're no longer needed",
        ],
    },
    {
        "version": "0.5.1",
        "date": "2026-04-11",
        "title": "Funding Results — Quieter Display & Admin Email Aliases",
        "changes": [
            "The funding results section no longer shows how many members contributed to each snapshot — keeping that detail private for now",
            "Admins can now add email aliases directly from the member page — handy for shared addresses like guild mailboxes where the member can't easily receive a verification code themselves",
            "Admins can also remove aliases, change which one is primary, and toggle whether an alias is marked verified",
            "Fixed a quiet bug where changing your primary email could silently revert on the next save — primary changes now stick",
        ],
    },
    {
        "version": "0.5.0",
        "date": "2026-04-11",
        "title": "Email Aliases & Smarter Funding Snapshots",
        "changes": [
            "New 'Manage email addresses' page in your profile menu — add, remove, verify, and set a primary email all in one place",
            "Sign in with any of your verified email addresses, not just the one you signed up with",
            "Cleaner admin: removed the confusing 'is primary' toggle on member email aliases",
            "Funding snapshots now have a $1,000 minimum pool so small-turnout months still allocate meaningful amounts to guilds",
            "New Snapshot Analyzer admin page — preview live vote results with filters by member type, role, and paying status before taking a snapshot",
            "Stored snapshots can be re-sliced with the same filters to see exactly who voted for what",
            "Admins can now delete a snapshot from the analyzer page",
            "Fixed: non-paying guild officers' votes are no longer silently zeroed out when the funding pool is calculated",
        ],
    },
    {
        "version": "0.4.2",
        "date": "2026-04-07",
        "title": "Pay-as-you-go Tabs, Guild Product Cards & Stripe in Settings",
        "changes": [
            "You no longer need to save a card before adding things to your tab — just add the item and pay later",
            "When you have charges ready to pay, a 'Pay Now' button appears on your Tab page that opens Stripe's secure checkout",
            "Money goes directly to the guild that owns the items you bought",
            "Guild pages now show products as cards instead of a table — easier to scan",
            "All Stripe configuration now lives in the admin Payments → Settings page — no more editing server environment variables",
            "Stripe Connect platform billing can now be enabled with a single toggle in Settings (for future membership and space-lease billing)",
        ],
    },
    {
        "version": "0.4.1",
        "date": "2026-04-07",
        "title": "Pay Guilds Directly for Consumables",
        "changes": [
            "Guilds can now connect their own Stripe account by pasting their API keys — no platform setup required",
            "Money for consumables (clay, materials, etc.) goes straight to the guild that owns the items",
            "Admins can test a guild's Stripe keys before saving to make sure everything is connected",
        ],
    },
    {
        "version": "0.4.0",
        "date": "2026-04-02",
        "title": "Tab Billing System",
        "changes": [
            "New tab system — charges accumulate on your tab and get billed on a schedule, just like a bar tab",
            "See your tab balance at a glance with the new balance pill in the top bar",
            "My Tab page shows your pending charges, tab limit, and remaining balance",
            "Tab History page shows all past charges with itemized details you can expand",
            "Add items to your own tab with the self-service form",
            "Set up a payment method securely through Stripe — your card info never touches our server",
            "Automated billing engine charges tabs on a configurable schedule (daily, weekly, or monthly)",
            "Failed charges automatically retry up to 3 times before locking the tab",
            "Email receipts after every successful charge with an itemized breakdown",
            "Guild Stripe accounts can be connected via Stripe Connect — each guild receives their share of charges directly",
            "Members can pick products when adding to their tab — price and description are filled in automatically",
            "Unified Payments admin — one page for outstanding tabs, charge history, billing settings, and Stripe accounts",
            "All financial records are preserved forever — entries are voided, never deleted",
            "Guild pages — each guild now has its own page with an about section and a list of products",
            "Guild leads can edit their guild's about text and manage their product listings directly from the guild page",
        ],
    },
    {
        "version": "0.3.1",
        "date": "2026-04-01",
        "title": "Mobile Sidebar Fix",
        "changes": [
            "Fixed the sidebar on mobile — it now slides open as a drawer with a dark backdrop you can tap to close",
            "Sidebar starts closed on mobile so you get the full screen for content",
            "Tapping a nav link on mobile automatically closes the sidebar",
        ],
    },
    {
        "version": "0.3.0",
        "date": "2026-03-30",
        "title": "Member Management Redesign",
        "changes": [
            "Admin Members page now lets you create users with a login right from the add form",
            "Added email aliases — members can have multiple email addresses and log in with any of them",
            "New 'Users' filter on the Members page to see who has logged into the app",
            "Search now finds members by their alias emails too",
            "Removed the separate User admin page — everything is managed through Members now",
        ],
    },
    {
        "version": "0.2.2",
        "date": "2026-03-30",
        "title": "Mobile Sidebar Fix",
        "changes": [
            "Fixed the sidebar menu button not working on Android phones — the side menu now opens and closes properly on mobile",
        ],
    },
    {
        "version": "0.2.1",
        "date": "2026-03-28",
        "title": "Better Discord Announcements",
        "changes": [
            "Discord now gets a friendly release announcement when updates go live — with version and what changed",
            "Removed noisy PR-opened notifications so the channel stays clean",
        ],
    },
    {
        "version": "0.2.0",
        "date": "2026-03-28",
        "title": "Vote Standings & Discord Notifications",
        "changes": [
            "Live vote standings with bar charts on the guild voting page — see who's leading in real time",
            "Admin voting dashboard now shows visual bar charts for vote leaders",
            "Discord notifications — the team gets pinged when PRs are opened or code is merged to main",
        ],
    },
    {
        "version": "0.1.3",
        "date": "2026-03-28",
        "title": "Login & Email Fixes",
        "changes": [
            "Members synced from Airtable can now log in immediately — no signup step needed",
            "All emails from Past Lives are now properly branded (no more 'example.com')",
            "Table columns in the admin panel are now left-aligned for easier reading",
        ],
    },
    {
        "version": "0.1.2",
        "date": "2026-03-28",
        "title": "Admin Fixes",
        "changes": [
            "Role management moved out of the member directory — it now lives in the admin panel only",
            "Login code entry allows up to 5 attempts before locking out (was 3)",
        ],
    },
    {
        "version": "0.1.1",
        "date": "2026-03-27",
        "title": "Hotfix",
        "changes": [
            "Guild vote history from before launch is now reflected in voting results",
            "Login code emails no longer show [example.com] in the subject line",
            "Login page email field now shows the correct placeholder",
        ],
    },
    {
        "version": "0.1.0",
        "date": "2026-03-27",
        "title": "Launch Day",
        "changes": [
            "Vote for your favorite guilds each month and see how funding gets split",
            "Your own member hub — one place for voting, directory, and settings",
            "Passwordless sign-in — just enter your email and we send you a code",
            "Member directory — find other members, see their bios and contact info",
            "Member roles — admins, guild officers, and regular members each see what they need",
            "Admins can invite new members, manage the roster, and take funding snapshots",
            "Send us feedback anytime from the Feedback button",
            "Works on your phone — install it like an app from your browser",
            "Forgot which email you signed up with? The new account finder has you covered",
        ],
    },
]
