# Past Lives — Mobile Shell (Capacitor)

A thin native wrapper that ships the Past Lives Makerspace hub to the App Store
and Google Play. It does **not** reimplement the app — the webview loads the live
Django-served site (`https://members.pastlives.space`), so all UI, auth, and features come
straight from the existing web app and update instantly with no store re-review.

- **App ID:** `app.pastlives.hub`
- **App name:** Past Lives
- **Mode:** server-URL (see `capacitor.config.ts`)

## What's here

```
mobile/
├── capacitor.config.ts   # appId, appName, server.url → members.pastlives.space
├── www/index.html        # splash/offline fallback (rarely shown)
├── android/              # generated native Android project (committed)
└── ios/                  # generated on the Mac (see below) — not committed from WSL
```

## Prerequisites

- **Node** (installed) and the Capacitor deps (`npm install` in `mobile/`).
- **Android** (works on WSL): JDK 17 + Android SDK. Already present here
  (`ANDROID_HOME=~/Android/Sdk`).
- **iOS** (Mac only): Xcode + CocoaPods.

## Android (build on WSL)

Run a debug APK (installs to a connected device / emulator):

```
npm run android          # cap run android  (needs a device/emulator)
```

Or build the artifact directly with gradle:

```
cd android
./gradlew assembleDebug           # → app/build/outputs/apk/debug/app-debug.apk  (sideload/testing)
./gradlew bundleRelease           # → app/build/outputs/bundle/release/*.aab      (Play Store)
```

The `.aab` must be signed with an **upload key** before Play accepts it. Generate
one once, keep it OUT of git (see `.gitignore`), and wire it via
`android/key.properties`. Upload to the Play Console **Internal testing** track
first — Google is fine with a well-behaved webview wrapper.

## iOS (build on the Mac)

The `ios/` project is created on macOS because `pod install` needs it. On the Mac,
from `mobile/`:

```
npm install
npx cap add ios          # scaffolds ios/App + runs pod install
npx cap open ios         # opens Xcode
```

In Xcode: set the signing team, then Product → Archive → distribute to TestFlight.
⚠️ Apple **Guideline 4.2** rejects pure "website in a box" apps — the native push
+ QR check-in features (Phase 3) are what clear that bar, so land those before the
first iOS submission.

## Pointing at a dev server

The emulator/device cannot reach `pastlives.test` or `localhost`. To test against
the local WSL server, edit `server` in `capacitor.config.ts` to your machine's LAN
IP (e.g. `http://192.168.1.50:8000`, `cleartext: true`), add that host to
`ALLOWED_HOSTS`, then `npx cap sync`.

## Roadmap

- **Phase 1 — shell** ✅ (this folder; Android builds, points at prod)
- **Phase 2 — Android internal testing** — sign an `.aab`, upload to Play internal track
- **Phase 3 — native value-add** — push (FCM/APNs → new Django device-token endpoint),
  QR check-in scanner, biometric unlock; make templates app-aware (`window.Capacitor`,
  safe-area insets, external links → system browser, Android back button)
- **Phase 4 — iOS + TestFlight** (on the Mac)
- **Phase 5 — store listings** — icons, screenshots, privacy policy, data-safety forms
