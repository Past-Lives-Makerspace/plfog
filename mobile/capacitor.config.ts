import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Capacitor shell for the Past Lives Makerspace hub.
 *
 * Strategy: server-URL mode. The native webview loads the live Django-served
 * site instead of bundled assets. This means:
 *   - Django keeps serving 100% of the UI; content/features ship instantly
 *     with NO app-store re-review.
 *   - The existing passwordless login-code auth + host-only cookies work
 *     unchanged, because the webview origin IS pastlives.app.
 *   - The Capacitor JS bridge still injects into the remote page, so native
 *     plugins (push, camera/QR, biometrics) are available to the web layer.
 *
 * The `www/` folder is only a splash/offline fallback shown before the remote
 * page loads or when fully offline.
 */
const config: CapacitorConfig = {
  appId: 'app.pastlives.hub',
  appName: 'Past Lives',
  webDir: 'www',
  server: {
    // Canonical origin. `pastlives.app` 301-redirects here; pointing directly
    // avoids the redirect hop and keeps the webview origin (and its auth
    // cookies) unambiguous.
    url: 'https://members.pastlives.space',
    cleartext: false,
    // --- Local dev against the WSL server ---
    // The emulator/device cannot reach `pastlives.test` or `localhost`.
    // Point at your machine's LAN IP + the dev port, add that host to
    // ALLOWED_HOSTS in settings, and allow cleartext for plain http:
    //   url: 'http://192.168.1.50:8000',
    //   cleartext: true,
  },
  /*
   * PUSH PLUGINS ARE SPLIT BY PLATFORM ON PURPOSE.
   *
   * Android uses @capacitor/push-notifications; iOS uses @capacitor-firebase/messaging,
   * because on iOS the former only ever yields the raw APNs device token and FCM's
   * message.token field rejects that. Both packages are real dependencies, and npm has no
   * notion of "iOS only", so without the allowlists below `npx cap sync` installs BOTH into
   * BOTH native projects. That breaks push on each platform in its own way:
   *
   *   Android: both plugins declare a service bound to com.google.firebase.MESSAGING_EVENT.
   *            Firebase delivers a message to only the first matching service in the merged
   *            manifest, so one plugin silently swallows every push.
   *   iOS:     both plugins claim the single bridge.notificationRouter.pushNotificationHandler
   *            slot in load(). Capacitor iterates plugins out of a Swift Set, whose order is
   *            unspecified and hash seeded per process, so the winner can differ from launch
   *            to launch and foreground banners plus tap deep links work only sometimes.
   *
   * !!! THESE ARE ALLOWLISTS, NOT DENYLISTS !!!
   * Any NEW Capacitor plugin must be added to the list for every platform that should get it,
   * or `npx cap sync` will silently leave it out of the native project.
   */
  plugins: {
    FirebaseMessaging: {
      /*
       * This block is global (it is copied into the Android config too), but only iOS has the
       * FirebaseMessaging plugin, so only iOS reads it.
       *
       * The plugin defaults to ["badge", "sound", "alert"], which tells iOS to draw
       * its own system banner for a push that arrives while the app is open. It ALSO fires
       * notificationReceived, and static/js/native-push.js draws the in-app banner from that,
       * so a foreground push would appear twice. Android's plugin does not present in the
       * foreground at all, so the custom banner is the established behavior; an empty array
       * (explicitly supported) matches iOS to it and leaves one banner.
       */
      presentationOptions: [],
    },
  },
  android: {
    // @capgo/capacitor-native-biometric is here AND in the iOS list below: biometric sign in
    // is a both-platforms feature, and these lists are allowlists, so a plugin missing from
    // one is silently absent from that native project with no build error.
    includePlugins: ['@capacitor/push-notifications', '@capgo/capacitor-native-biometric'],
  },
  ios: {
    includePlugins: ['@capacitor-firebase/messaging', '@capgo/capacitor-native-biometric'],
    // Let the web layer own safe-area insets via CSS env(safe-area-inset-*).
    contentInset: 'never',
  },
};

export default config;
