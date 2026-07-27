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
  ios: {
    // Let the web layer own safe-area insets via CSS env(safe-area-inset-*).
    contentInset: 'never',
  },
};

export default config;
