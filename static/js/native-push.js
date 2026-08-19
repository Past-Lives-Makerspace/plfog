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

  // Android notification channels — the "categories" a member sees (and tunes) under the
  // app's system notification settings. The server tags every push with one of these ids
  // (see core.fcm / core.events.channels.push_channel_for), so they must line up exactly.
  // createChannel is idempotent and only defines the channel; it never re-overrides a
  // setting the member has since changed. Android-only: iOS has no channels.
  var CHANNELS = [
    {
      id: "urgent",
      name: "Urgent",
      description:
        "Time-sensitive alerts: a class starting soon, a cancelled class, a freed waitlist seat, a failed payment.",
      importance: 4,
      vibration: true,
    },
    { id: "guilds", name: "Guilds", description: "Guild announcements, meetings, and funding votes.", importance: 3 },
    { id: "classes", name: "Classes", description: "Class announcements, registrations, and updates.", importance: 3 },
    { id: "general", name: "General", description: "Everything else from Past Lives.", importance: 3 },
  ];

  function createChannels() {
    if (platform !== "android" || typeof PushNotifications.createChannel !== "function") {
      return; // iOS has no channels; guard older plugins without the method
    }
    CHANNELS.forEach(function (channel) {
      PushNotifications.createChannel(channel).catch(function (err) {
        console.error("[native-push] createChannel failed", channel.id, err);
      });
    });
  }

  createChannels();

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
