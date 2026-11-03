/*
 * Links that open a new tab, inside the native app (#1102).
 *
 * The iOS shell hands every navigation that asks for a new window to the system browser:
 * Capacitor's WebViewDelegationHandler answers createWebViewWith with
 * UIApplication.shared.open, which is Safari. Safari has none of the app's cookies, so a
 * target="_blank" link to one of our own pages (the class composer's Preview, Open in a New
 * Tab, review links) lands on the emailed code login, with no Face ID because Safari has no
 * Capacitor bridge. Android keeps such a link in its WebView already. Inside the app this file
 * opens a same-host target="_blank" link in the app itself, where the member is signed in.
 * Links to other hosts, mailto: and tel:, and download links (data-pl-download or download,
 * FRONTEND.md rule 24) are left to the shell exactly as before. So is a link marked
 * data-pl-new-window: a flyer, QR or sticker sheet, or print page is a standalone document
 * with no hub navigation, and iOS has no back swipe, so opened in place it would strand the
 * member; tests/new_window_links_lint_spec.py holds every link to such a page to the mark.
 * The iOS bridge is injected into the top frame only (forMainFrameOnly), so a link inside a
 * framed page (the composer's preview iframe) is not handled there; Android injects into
 * same-origin frames too, so this file also returns inside any frame, and both shells treat a
 * framed link as they do today. The native check is the one app-store-badges.js uses: the
 * Capacitor bridge is present and reports a native platform. In
 * a browser this file does nothing, so a new tab still opens, and no app release is involved.
 *
 * Loaded deferred from the hub <head> (once per document) and from base.html. One delegated
 * click listener on document survives every boosted body swap; it is bound once behind a flag
 * on window (FRONTEND.md, Scripts under hx-boost). htmx never boosts an anchor that carries a
 * target, so the two never handle the same click.
 */
(function () {
  "use strict";

  var Cap = window.Capacitor;
  if (!(Cap && typeof Cap.isNativePlatform === "function" && Cap.isNativePlatform())) {
    return; // a browser: a new tab opens as it always has
  }
  if (window.top !== window) {
    return; // a framed page: its links stay with the shell
  }

  if (window.plNativeNewTabLinksBound) {
    return;
  }
  window.plNativeNewTabLinksBound = true;

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    var start = event.target;
    if (!(start && start.closest)) {
      return;
    }
    var link = start.closest("a[href]");
    if (!link || (link.getAttribute("target") || "").toLowerCase() !== "_blank") {
      return;
    }
    if (link.hasAttribute("download") || link.hasAttribute("data-pl-download")) {
      return;
    }
    if (link.hasAttribute("data-pl-new-window")) {
      return; // a standalone page with no way back: the shell opens it outside, as before
    }
    var url;
    try {
      url = new URL(link.href, window.location.href);
    } catch (e) {
      return;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return; // mailto:, tel: and the rest stay with the shell
    }
    if (url.origin !== window.location.origin) {
      return; // another host: the system browser, as before
    }
    event.preventDefault();
    window.location.assign(url.href);
  });
})();
