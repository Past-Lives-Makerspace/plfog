-- scrub.sql: make a fresh copy of production safe to run as staging.
--
-- Applied by refresh-db.sh right after pg_restore and before migrate. Idempotent: running it
-- twice changes nothing the second time. Every table and column below is verified against
-- the models (core, membership) and the Django sites and sessions apps. The code refuses
-- the same credentials on staging (settings.IS_STAGING); blanking them here as well means
-- a code path that reads a field directly still finds nothing.

BEGIN;

-- The sites framework row is what invite and login-invite links are built from
-- (Invite.send_invite_email, Member.send_login_invite). Left alone, a practising admin's
-- invitee would be sent to production.
UPDATE django_site
   SET domain = 'staging.pastlives.space',
       name = 'Past Lives Makerspace (staging)'
 WHERE id = 1;

-- Site Settings carries production's Mailchimp key, Discord webhooks, server and channel
-- ids, the Discord mirror toggles and the GA measurement id. The legacy CMS sync stays off
-- so a stray sync_all_sources never re-imports classes over what the copy already holds.
UPDATE core_siteconfiguration
   SET mailchimp_api_key = '',
       discord_general_webhook_url = '',
       discord_leadership_webhook_url = '',
       discord_officers_webhook_url = '',
       discord_reservations_webhook_url = '',
       discord_server_id = '',
       discord_role_message_channel_id = '',
       discord_role_message_id = '',
       discord_calendar_channel_id = '',
       discord_classes_channel_id = '',
       discord_info_channel_id = '',
       discord_info_message_id = '',
       discord_events_sync_enabled = false,
       discord_calendar_posts_enabled = false,
       discord_classes_posts_enabled = false,
       google_analytics_measurement_id = '',
       legacy_cms_sync_enabled = false;

-- Per-event routing overrides point at real channels. Disabled and blanked, so the admin
-- routing page shows them off instead of silently posting nowhere.
UPDATE core_discordwebhookroute
   SET webhook_url = '',
       is_enabled = false;

-- Each guild's own channel webhook and its opt-in.
UPDATE membership_guild
   SET discord_webhook_url = '',
       discord_post_enabled = false;

-- A staging copy must never sit in Stripe live mode: the copied secrets already read back
-- blank under staging's own Fernet key, and test mode makes any key entered later land in
-- the test slot.
UPDATE billing_billingsettings
   SET test_mode = true;

-- Browser push subscriptions and app device tokens belong to real phones and browsers.
-- Staging has no VAPID or FCM credentials for them, but a copied token must not sit there
-- waiting for someone to add some.
DELETE FROM core_pushsubscription;
DELETE FROM core_fcmdevice;

-- Live production sessions have no business on another host.
DELETE FROM django_session;

-- Admin-edited notification copy can carry absolute production links typed by hand; the
-- seeded copy uses merge fields, so this only touches what a person pasted. The book host
-- is rewritten first so the members rewrite cannot double up on it.
UPDATE core_notificationtemplate
   SET subject   = replace(replace(subject,   'book.pastlives.space', 'classes.staging.pastlives.space'),
                           'members.pastlives.space', 'staging.pastlives.space'),
       body_text = replace(replace(body_text, 'book.pastlives.space', 'classes.staging.pastlives.space'),
                           'members.pastlives.space', 'staging.pastlives.space'),
       body_html = replace(replace(body_html, 'book.pastlives.space', 'classes.staging.pastlives.space'),
                           'members.pastlives.space', 'staging.pastlives.space')
 WHERE subject LIKE '%pastlives.space%'
    OR body_text LIKE '%pastlives.space%'
    OR body_html LIKE '%pastlives.space%';

COMMIT;
