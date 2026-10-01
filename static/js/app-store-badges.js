/*
 * "Get the app" store badges (#467).
 *
 * Every badge block (templates/includes/app_store_badges.html) renders with the `hidden`
 * attribute and this file reveals it in a browser. Inside the native app the block stays
 * hidden, so the app never flashes a "download the app" prompt. The native check is the one
 * native-push.js and biometric-auth.js use: the Capacitor bridge is present and reports a
 * native platform. The server render is identical either way.
 *
 * Loaded deferred from the hub <head> (once per document) and from base.html. hub/base.html
 * boosts the body, so every boosted arrival brings the attribute back with the new body; the
 * reveal re-runs on htmx:afterSettle, bound once behind a flag on window (FRONTEND.md, Scripts
 * under hx-boost).
 */
(function () {
  "use strict";

  var Cap = window.Capacitor;
  if (Cap && typeof Cap.isNativePlatform === "function" && Cap.isNativePlatform()) {
    return; // the native app: the badges stay hidden
  }

  function reveal() {
    var blocks = document.querySelectorAll("[data-pl-app-badges]");
    for (var i = 0; i < blocks.length; i++) {
      blocks[i].hidden = false;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", reveal);
  } else {
    reveal();
  }

  if (!window.plAppBadgesBound) {
    window.plAppBadgesBound = true;
    document.addEventListener("htmx:afterSettle", reveal);
  }
})();
