/*
 * The Share button on the public class page.
 *
 * The button carries the facts as data attributes the server renders (data-share-url, the
 * class's readable public page; data-share-title; data-share-text). A click goes, in order:
 *
 *   1. The Capacitor Share plugin, when the page runs inside the native app and the shell
 *      carries the plugin (mobile/capacitor.config.ts). Nothing in the app has it yet; this
 *      branch is what the next app release lights up with no further web change.
 *   2. navigator.share, the Web Share API: Chrome for Android, Safari, iOS Safari and the
 *      iOS WKWebView. The Android WebView has no navigator.share, so inside the Android app
 *      it is undefined until step 1 applies. An AbortError (the person closed the sheet) is
 *      silent; any other rejection falls through to the menu.
 *   3. The Copy / Text / Email menu under the button, which Alpine owns: the wrapper's inline
 *      x-data holds open, copied and manual, and this file reaches it only through bubbling
 *      custom events (pl-share-menu, pl-share-copied, pl-share-manual). Copy link writes the
 *      url to the clipboard; when that fails the wrapper reveals a readonly input holding the
 *      url so the person can copy it by hand.
 *
 * Loaded deferred from the detail page's classes_extra_head. The public surface extends
 * hub/base.html, whose body is boosted, so this file runs once per document: it binds one
 * delegated click listener on document behind a flag on window (FRONTEND.md, Scripts under
 * hx-boost), and the inline x-data is markup, which Alpine initialises on every arrival.
 * It registers no Alpine component, which is why it may live outside the hub head.
 */
(function () {
  "use strict";

  if (window.plClassShareBound) {
    return;
  }
  window.plClassShareBound = true;

  function tell(element, name) {
    element.dispatchEvent(new CustomEvent(name, { bubbles: true }));
  }

  function nativeSharePlugin() {
    var Cap = window.Capacitor;
    var isNative = Cap && typeof Cap.isNativePlatform === "function" && Cap.isNativePlatform();
    if (isNative && Cap.Plugins && Cap.Plugins.Share) {
      return Cap.Plugins.Share;
    }
    return null;
  }

  function share(button) {
    var facts = {
      title: button.getAttribute("data-share-title"),
      text: button.getAttribute("data-share-text"),
      url: button.getAttribute("data-share-url"),
    };
    var plugin = nativeSharePlugin();
    if (plugin) {
      plugin
        .share({ title: facts.title, text: facts.text, url: facts.url, dialogTitle: "Share this class" })
        .catch(function () {}); // the person closed the sheet
      return;
    }
    if (typeof navigator.share === "function") {
      navigator.share(facts).catch(function (error) {
        if (!(error && error.name === "AbortError")) {
          tell(button, "pl-share-menu");
        }
      });
      return;
    }
    tell(button, "pl-share-menu");
  }

  function copy(button) {
    var url = button.getAttribute("data-share-copy");
    function byHand() {
      tell(button, "pl-share-manual");
    }
    if (!(navigator.clipboard && typeof navigator.clipboard.writeText === "function")) {
      byHand();
      return;
    }
    navigator.clipboard.writeText(url).then(function () {
      tell(button, "pl-share-copied");
    }, byHand);
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    var shareButton = target.closest("[data-share-url]");
    if (shareButton) {
      share(shareButton);
      return;
    }
    var copyButton = target.closest("[data-share-copy]");
    if (copyButton) {
      copy(copyButton);
    }
  });
})();
