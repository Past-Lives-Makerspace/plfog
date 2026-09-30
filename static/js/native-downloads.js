/*
 * Download links inside the native app.
 *
 * The Capacitor shells keep a same-host navigation inside the WebView, and neither shell
 * handles a Content-Disposition: attachment response: Android sets no DownloadListener and
 * iOS no WKDownloadDelegate, so a tap on a download link does nothing there
 * (mobile/README.md). Until the shells learn to download, every link to one of our own
 * attachments carries data-pl-download (FRONTEND.md rule 24) and this file hides them in
 * the app, so nothing dead sits on screen. Links to uploaded documents are not marked: they
 * live on another host, which the shells hand to the system browser. The native check is the
 * one app-store-badges.js uses: the Capacitor bridge is present and reports a native
 * platform. In a browser this file does nothing, and the server render is identical either
 * way, so no app release is involved.
 *
 * Loaded deferred from the hub <head> (once per document). hub/base.html boosts the body, so
 * every boosted arrival brings the links back with the new body; the hide re-runs on
 * htmx:afterSettle, bound once behind a flag on window (FRONTEND.md, Scripts under hx-boost).
 */
(function () {
  "use strict";

  var Cap = window.Capacitor;
  if (!(Cap && typeof Cap.isNativePlatform === "function" && Cap.isNativePlatform())) {
    return; // a browser: every download link stays
  }

  function hide() {
    var links = document.querySelectorAll("[data-pl-download]");
    for (var i = 0; i < links.length; i++) {
      links[i].classList.add("pl-native-hidden");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hide);
  } else {
    hide();
  }

  if (!window.plNativeDownloadsBound) {
    window.plNativeDownloadsBound = true;
    document.addEventListener("htmx:afterSettle", hide);
  }
})();
