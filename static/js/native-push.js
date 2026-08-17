/*
 * Native push registration for the Capacitor (Android/iOS) app shell.
 *
 * No-op in a normal browser: the site's Web Push path handles those, and the
 * Android/iOS WebView cannot receive Web Push anyway. When the page is running
 * inside the native app, this asks for notification permission and registers the
 * device's FCM token with the server so core.events.channels.PushAdapter can reach it.
 */
(function () {
  "use strict";

  var Cap = window.Capacitor;
  if (!Cap || typeof Cap.isNativePlatform !== "function" || !Cap.isNativePlatform()) {
    return; // running in a normal browser, not the native app
  }

  var PushNotifications = Cap.Plugins && Cap.Plugins.PushNotifications;
  if (!PushNotifications) {
    return; // push plugin not present in this build
  }

  var platform = typeof Cap.getPlatform === "function" ? Cap.getPlatform() : "android";

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function postJSON(path, payload) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
  }

  PushNotifications.addListener("registration", function (token) {
    postJSON("/push/fcm/register/", { token: token.value, platform: platform }).catch(function (err) {
      console.error("[native-push] token register failed", err);
    });
  });

  PushNotifications.addListener("registrationError", function (err) {
    console.error("[native-push] registration error", err);
  });

  // Tapping a notification opens its target url inside the app.
  PushNotifications.addListener("pushNotificationActionPerformed", function (action) {
    var data = action && action.notification && action.notification.data;
    if (data && data.url) {
      window.location.href = data.url;
    }
  });

  PushNotifications.checkPermissions()
    .then(function (status) {
      if (status.receive === "prompt" || status.receive === "prompt-with-rationale") {
        return PushNotifications.requestPermissions();
      }
      return status;
    })
    .then(function (status) {
      if (status.receive === "granted") {
        PushNotifications.register();
      }
    })
    .catch(function (err) {
      console.error("[native-push] permission/register failed", err);
    });
})();
