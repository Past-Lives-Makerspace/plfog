/*
 * The Share button on a class's Share & Print card (#1254): a Story or a Post picture for any app in the share sheet.
 *
 * The card (components/qr_share_card.html) renders a Share button whose small menu Alpine owns,
 * and two menu items carrying the facts as data attributes: data-share-image (the PNG,
 * classes:class_instagram), data-share-image-title (the class title) and data-share-image-link
 * (the class's public link). A choice goes, in order:
 *
 *   1. Inside the native app with no navigator.share (the Android WebView): the Capacitor Share
 *      plugin cannot take a web file without @capacitor/filesystem, an app release, so it shares
 *      the class link as static/js/class_share.js does and tells the card (pl-share-image-note)
 *      to show the line that the picture shares from the phone's browser.
 *   2. navigator.share with the picture as a file, where navigator.canShare says files are
 *      welcome: phone browsers, the iOS WKWebView, some desktop browsers. An AbortError (the
 *      person closed the sheet) is silent; any other failure falls through to step 3.
 *   3. Elsewhere, mostly desktop browsers: the picture downloads, through a temporary
 *      <a download> on a blob URL, so the file arrives without a page load. Inside the app,
 *      where a download is dropped (FRONTEND.md rule 24), step 3 is the link share of step 1.
 *
 * Opening the menu starts both fetches, so a choice usually has its file in hand and the
 * share call runs inside the browser's user-activation window (Safari's is short); the fetch
 * is kept on the menu item as a property, never an attribute (FRONTEND.md, Scripts under
 * hx-boost).
 *
 * Loaded from the card itself, in the boosted hub body, so it re-runs on every boosted arrival:
 * one delegated click listener on document, bound once behind a flag on window. It registers
 * no Alpine component.
 */
(function () {
  "use strict";

  if (window.plShareImageBound) {
    return;
  }
  window.plShareImageBound = true;

  var FILENAME = /filename="([^"]+)"/;

  function tell(element, name) {
    element.dispatchEvent(new CustomEvent(name, { bubbles: true }));
  }

  function isNativeApp() {
    var Cap = window.Capacitor;
    return Boolean(Cap && typeof Cap.isNativePlatform === "function" && Cap.isNativePlatform());
  }

  function canShareFiles() {
    return typeof navigator.share === "function" && typeof navigator.canShare === "function";
  }

  // The PNG as a File named the way the server names its download; a fetch that fails is
  // forgotten, so the next choice tries again.
  function picture(item) {
    if (!item.plSharePicture) {
      item.plSharePicture = fetch(item.getAttribute("data-share-image"), { credentials: "same-origin" }).then(
        function (response) {
          var match = FILENAME.exec(response.headers.get("Content-Disposition") || "");
          if (!response.ok || !match) {
            throw new Error("No picture: HTTP " + response.status);
          }
          return response.blob().then(function (blob) {
            return new File([blob], match[1], { type: "image/png" });
          });
        }
      );
      item.plSharePicture.catch(function () {
        item.plSharePicture = null;
      });
    }
    return item.plSharePicture;
  }

  function shareLink(item) {
    tell(item, "pl-share-image-note");
    var facts = { title: item.getAttribute("data-share-image-title"), url: item.getAttribute("data-share-image-link") };
    var Cap = window.Capacitor;
    if (Cap && Cap.Plugins && Cap.Plugins.Share) {
      Cap.Plugins.Share.share({ title: facts.title, url: facts.url, dialogTitle: "Share this class" }).catch(
        function () {} // the person closed the sheet
      );
    } else if (typeof navigator.share === "function") {
      navigator.share(facts).catch(function () {});
    }
  }

  function download(file) {
    var url = URL.createObjectURL(file);
    var link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    link.hidden = true;
    link.setAttribute("hx-boost", "false");
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 10000);
  }

  // No sheet took the file: the app shares the link, a browser downloads the picture, or, when
  // even the fetch failed, opens its address so the server's own answer shows.
  function fallBack(item, file) {
    if (isNativeApp()) {
      shareLink(item);
    } else if (file) {
      download(file);
    } else {
      window.location.assign(item.getAttribute("data-share-image"));
    }
  }

  function share(item) {
    if (isNativeApp() && typeof navigator.share !== "function") {
      shareLink(item);
      return;
    }
    picture(item).then(
      function (file) {
        if (!(canShareFiles() && navigator.canShare({ files: [file] }))) {
          fallBack(item, file);
          return;
        }
        navigator.share({ files: [file], title: item.getAttribute("data-share-image-title") }).catch(function (error) {
          if (!(error && error.name === "AbortError")) {
            fallBack(item, file);
          }
        });
      },
      function () {
        fallBack(item, null);
      }
    );
  }

  function prefetch(menu) {
    if (isNativeApp() && typeof navigator.share !== "function") {
      return; // step 1 shares the link; no picture is needed
    }
    var items = menu.querySelectorAll("[data-share-image]");
    for (var i = 0; i < items.length; i++) {
      picture(items[i]).catch(function () {});
    }
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }
    var opener = target.closest("[data-share-image-open]");
    if (opener) {
      prefetch(opener.closest("[data-share-image-menu]"));
      return;
    }
    var item = target.closest("[data-share-image]");
    if (item) {
      share(item);
    }
  });
})();
