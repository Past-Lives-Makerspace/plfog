# iOS push notifications

**Status:** approved 2026-09-06
**Ships as:** one PR off `main`

## Why

Android members get native push. iOS members get nothing, and the reason is
subtler than "it was never configured":

`@capacitor/push-notifications` on iOS emits the **raw APNs device token** as a hex
string (verified in
`mobile/node_modules/@capacitor/push-notifications/ios/Sources/PushNotificationsPlugin/PushNotificationsPlugin.swift`,
`didRegisterForRemoteNotifications` → `deviceToken.reduce("", { $0 + String(format: "%02X", $1) })`).
`core/fcm.py` posts whatever it is handed to FCM's `message.token` field, which
only accepts **FCM registration tokens**. An APNs token there is rejected.

On top of that, `mobile/ios/App/App/AppDelegate.swift` is the stock Capacitor
scaffold: it never forwards `didRegisterForRemoteNotificationsWithDeviceToken`,
so the plugin's `NotificationCenter` observer never fires and no token is
produced at all. There is no entitlements file and no `remote-notification`
background mode.

So iOS push is genuinely broken, not merely unconfigured, and the fix is to give
iOS a plugin that returns a real FCM token.

## Decisions (locked)

1. **Firebase for iOS, same as Android.** One transport, one credential
   (`FCM_SERVICE_ACCOUNT_JSON`, already set on Render), one place to look when
   push breaks. `FcmDevice` stays an honest name: every token it holds is an FCM
   token.
2. **`@capacitor-firebase/messaging` on iOS only. Android keeps
   `@capacitor/push-notifications`.** The Android push path shipped and works;
   this feature does not touch its bytes. Cost accepted: two push plugins in the
   app and a platform branch in the registration JS.
3. **No device-removal UI for other devices.** The FCM 404 reaper in
   `core/fcm.py` already deletes dead rows. This ships a "push on this device"
   control only.

## Constraint that shapes the whole plan

The app is **server-URL mode** (`mobile/capacitor.config.ts`): the webview loads
`https://members.pastlives.space`, so `static/js/native-push.js` reaches every
installed app the moment this merges, with no store review.

That cuts both ways:

- **Android needs no new release.** The rewritten JS must behave byte-identically
  on Android, because it starts running on shipped Android apps immediately.
- **The new JS runs on the CURRENT iOS build**, which has no
  `FirebaseMessaging` plugin. It must detect the missing plugin and return
  cleanly, exactly as the file already does today for a missing
  `PushNotifications`.

## Phase 1 — server: APNs options in the FCM payload

`core/fcm.py`. Today `send_fcm` sends only an `android` block, so an iOS
delivery would arrive with no priority, no sound, and no grouping.

Add an `apns` block alongside it, and a `_apns_priority` sibling to the existing
`_android_priority`:

```python
def _apns_priority(channel_id: str) -> str:
    """APNs delivery priority - the iOS counterpart of _android_priority.

    "10" delivers immediately; "5" lets iOS batch for power. Urgent notices
    (a class starting soon, a cancellation, a freed waitlist seat, a failed
    charge) go 10; everything else rides 5.
    """
    return "10" if channel_id == PUSH_CHANNEL_URGENT else "5"
```

The payload gains:

```python
"apns": {
    "headers": {
        "apns-priority": _apns_priority(channel_id),
        "apns-push-type": "alert",
    },
    "payload": {"aps": {"sound": "default", "thread-id": channel_id}},
},
```

`thread-id` is the closest iOS analogue to an Android notification channel: it
groups a member's notices by kind in the tray. iOS has no per-channel member
controls, so `channel_id` keeps its Android meaning and doubles as the group key.

Do **not** add a badge count. It needs an unread tally the push path does not
have, and a wrong badge is worse than none.

Nothing else on the server changes. Verify (do not assume) that:

- `core/models.py` `FcmDevice.Platform.IOS` already exists — it does.
- `core/views.py` `fcm_register` already validates and stores `platform` — it does.
- `core/events/channels.py` `PushAdapter.deliver` already loops every `FcmDevice`
  regardless of platform — it does, so per-event push preferences apply to iOS
  devices for free the moment one registers.

## Phase 2 — JS: a platform-normalized registration path

`static/js/native-push.js`.

**The banner, `safeNavUrl`, and the cold-start pending-nav stash are not to be
touched.** That logic was won the hard way and is platform independent. Only the
top of the file changes: which plugin is used, what the listeners are called, and
how a token is requested.

Introduce a small adapter that both plugins are hidden behind, then leave the
rest of the file calling the adapter:

```js
// iOS uses @capacitor-firebase/messaging because @capacitor/push-notifications
// hands iOS the raw APNs token, which FCM's message.token field rejects.
// Android keeps @capacitor/push-notifications - it ships, it works, and this
// file reaches installed Android apps the moment it merges.
function pushBridge(platform, Plugins) {
  if (platform === "ios") { ... FirebaseMessaging ... }
  return { ... PushNotifications ... };   // android
}
```

The adapter exposes exactly five things, so the rest of the file is unchanged:
`onToken(cb)`, `onTokenError(cb)`, `onReceived(cb)`, `onActionPerformed(cb)`,
`ensurePermissionAndRegister()`. Return `null` when the platform's plugin is
absent so the existing early-return still guards a build without it.

The two plugins differ in event names, payload shape, and how a token is asked
for. **Read
`mobile/node_modules/@capacitor-firebase/messaging/dist/esm/definitions.d.ts`
after installing and normalize against what is actually declared there** — do not
trust a remembered API. In particular check whether the notification payload for
`notificationReceived` / `notificationActionPerformed` is the notification itself
or is wrapped in a `{ notification }` envelope, because the banner and the
deep-link both read `notification.data.url`.

`createChannel` stays Android-only and unchanged; iOS has no channels.

## Phase 3 — native iOS

- `mobile/`: `npm install @capacitor-firebase/messaging@^8.5.1` (peer deps:
  `@capacitor/core >=8.0.0`, `firebase ^12.6.0`). Commit `package.json` and
  `package-lock.json`.
- `mobile/ios/App/App/GoogleService-Info.plist` — from the Firebase console, see
  Handoff below. Commit it: it is client configuration, not a secret, and
  `mobile/android/app/google-services.json` is committed for the same reason.
- `mobile/ios/App/App/AppDelegate.swift` — add the two forwarding methods the
  scaffold is missing:

```swift
func application(_ application: UIApplication,
                 didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
    NotificationCenter.default.post(
        name: .capacitorDidRegisterForRemoteNotifications, object: deviceToken)
}

func application(_ application: UIApplication,
                 didFailToRegisterForRemoteNotificationsWithError error: Error) {
    NotificationCenter.default.post(
        name: .capacitorDidFailToRegisterForRemoteNotifications, object: error)
}
```

- `mobile/ios/App/App/Info.plist` — add `UIBackgroundModes` with
  `remote-notification`.
- Xcode Signing & Capabilities → Push Notifications. This writes
  `App/App/App.entitlements` with `aps-environment`; commit it.
- Bump `CURRENT_PROJECT_VERSION` in the Xcode project (build 2 shipped).

### Android manifest conflict (verified 2026-09-06, must be mitigated here)

npm and Capacitor plugin installs are **not per-platform**. `npx cap sync` adds
`@capacitor-firebase/messaging` to the Android project too, and both push plugins
declare an Android service bound to the same intent filter:

- `com.capacitorjs.plugins.pushnotifications.MessagingService`
- `io.capawesome.capacitorjs.plugins.firebase.messaging.MessagingService`

both with `<action android:name="com.google.firebase.MESSAGING_EVENT" />`.

Firebase delivers an incoming message to **only one** service, the first match in
the merged manifest. With both present, one silently wins and the other's JS
listeners never fire. The shipped Android APK is unaffected because it carries
only the one plugin, so this does not bite on merge. It bites on the **next
Android rebuild**, which is the biometric-login PR immediately after this one.

Fix it here, in `mobile/android/app/src/main/AndroidManifest.xml`: declare
`xmlns:tools` on the root element and remove the capawesome service from the
Android merge with `tools:node="remove"`, with a comment saying why. Android then
keeps `@capacitor/push-notifications` exactly as it ships today, and the JS never
calls `FirebaseMessaging` on Android.

Confirm the edit survives `npx cap sync android` (that command owns
`capacitor.build.gradle` and `capacitor.settings.gradle`, not the app manifest),
and check whether the two plugins pull in skewed `firebase-messaging` versions.

## Phase 4 — "Push on this device" in notification settings

The notification settings block (`templates/hub/_notifications_settings.html`,
included by `templates/hub/user_settings.html` and by
`templates/hub/settings_notifications_token.html`) is an event x channel matrix.
It has a Push column but nothing tells a member whether push works on the phone
in their hand, and there is no recovery if they dismissed the OS prompt.

Add one card above the matrix, in a new partial
`templates/hub/partials/_push_this_device.html`:

- A server-rendered muted line for context: how many devices are registered for
  push on this account, e.g. "Push is on for 2 other devices." Count
  `FcmDevice` + `PushSubscription` for `request.user`. Omit the line at zero.
- A JS-driven status line for the **current** device, one of: push is on / push
  is off with an Enable button / blocked in system settings with plain
  instructions to allow notifications for Past Lives and come back. The blocked
  state is the whole point of the card: it is the only state a member cannot fix
  from inside the app.
- Enable runs the same permission-and-register path as
  `static/js/native-push.js`; "Turn off on this device" POSTs the existing
  `/push/fcm/unregister/` with the current token.

Reuse the existing status/JS rather than duplicating it: export the bridge and
the register routine from `native-push.js` on a namespace the settings partial's
script can call.

In a normal browser the card describes the browser's Web Push state instead, or
is hidden if that is cleanly out of scope — decide from what
`core/views.py subscribe` already supports and say which you chose in the PR body.

Follow `FRONTEND.md` for the card. Copy is plain and short: no dashes, ELI14.

## Phase 5 — tests

- `tests/core/fcm_spec.py` — the `apns` block is present and correctly shaped;
  `apns-priority` is "10" for `PUSH_CHANNEL_URGENT` and "5" for each of the other
  three; `thread-id` equals the channel id; the `android` block is unchanged.
  Assert on the JSON actually posted (the file already uses `respx`).
- A spec for the settings card: the device-count line renders with the right
  count, is absent at zero, and the card does not appear for an anonymous or
  token-scoped viewer if that is how you scope it.
- `tests/core/push_admin_spec.py` and `tests/core/events/channels_spec.py` must
  stay green untouched — if either needs editing, the change went wider than
  intended.

## What cannot be verified here, and who verifies it

**A builder subagent cannot run the Capacitor bridge.** pytest proves the server
payload and the Django-rendered card. It proves nothing about
`static/js/native-push.js` — not the Android branch it rewrites, and not the iOS
branch it adds.

The JS is verified on hardware, by Jo:

1. Android first, and before anything else, because the rewritten JS reaches
   shipped Android apps on merge. Sideload a debug APK, confirm a token still
   registers, then fire a test push from `/admin/push-test/` and confirm the tray
   notification, the tap deep-link, and the foreground banner all still work.
2. iOS after the TestFlight build: permission prompt, token registers as
   `platform="ios"` (check `/admin/push-test/`), tray notification, tap
   deep-links into the right page, foreground banner.

Say plainly in the PR body that the JS is unverified by CI and name these two
checks.

## Handoff — what Jo does outside the repo

1. Firebase console → Project Settings → Add app → iOS, bundle id
   `app.pastlives.hub` → download `GoogleService-Info.plist`.
2. Apple Developer → Certificates, Identifiers & Profiles → Keys → new key with
   Apple Push Notifications service (APNs) enabled → download the `.p8` **once**.
3. Firebase console → Project Settings → Cloud Messaging → APNs Authentication
   Key → upload the `.p8` with its Key ID and your Team ID.
4. Xcode → Signing & Capabilities → add Push Notifications and Background Modes
   (Remote notifications) → Archive → TestFlight.

No new Render environment variables. `FCM_SERVICE_ACCOUNT_JSON` already covers
both platforms, which is the point of choosing Firebase for iOS.
