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

  // Only navigate to a same-origin relative path or an http(s) URL. A push payload's
  // url is server-set, but this guards against a "javascript:" or off-origin value ever
  // reaching window.location (XSS / open-redirect defense in depth).
  function safeNavUrl(raw) {
    if (typeof raw !== "string" || !raw) {
      return null;
    }
    if (raw.charAt(0) === "/" && raw.charAt(1) !== "/") {
      return raw; // relative path, but not protocol-relative "//host"
    }
    try {
      var parsed = new URL(raw, window.location.origin);
      if (parsed.protocol === "https:" || parsed.protocol === "http:") {
        return parsed.href;
      }
    } catch (err) {
      /* malformed url → refuse to navigate */
    }
    return null;
  }

  // A notification tap must deep-link even on a COLD START. When the app was evicted from
  // memory (e.g. a push that sat through Doze, then got tapped), tapping launches it fresh:
  // the WebView first navigates to the configured server url (home), and the queued tap event
  // can fire a beat later and get clobbered by that launch navigation — stranding the member
  // on home instead of the announcement. So a tap doesn't rely on an immediate navigation
  // alone: it also stashes its target, and the app re-applies a fresh stash once launch
  // settles (on script load and whenever the app returns to the foreground).
  var PENDING_NAV_KEY = "native-push:pending-url";
  var PENDING_NAV_TTL_MS = 60000; // honor only a very recent tap; never hijack a later launch

  function stashPendingNav(target) {
    try {
      window.localStorage.setItem(PENDING_NAV_KEY, JSON.stringify({ url: target, at: Date.now() }));
    } catch (err) {
      /* storage blocked → the immediate navigation below is the only path */
    }
  }

  function consumePendingNav() {
    var raw = null;
    try {
      raw = window.localStorage.getItem(PENDING_NAV_KEY);
      if (raw) {
        window.localStorage.removeItem(PENDING_NAV_KEY);
      }
    } catch (err) {
      return;
    }
    if (!raw) {
      return;
    }
    var parsed = null;
    try {
      parsed = JSON.parse(raw);
    } catch (err) {
      return; // corrupt entry → ignore
    }
    if (!parsed || typeof parsed.at !== "number" || Date.now() - parsed.at > PENDING_NAV_TTL_MS) {
      return; // stale tap from an earlier session — don't hijack this launch
    }
    var target = safeNavUrl(parsed.url);
    if (target && target !== window.location.href) {
      window.location.replace(target);
    }
  }

  // Tapping a notification opens its target url inside the app.
  PushNotifications.addListener("pushNotificationActionPerformed", function (action) {
    var data = action && action.notification && action.notification.data;
    var target = safeNavUrl(data && data.url);
    if (!target) {
      return;
    }
    stashPendingNav(target); // survives a cold-start launch that clobbers the immediate nav
    window.location.href = target;
  });

  // Re-apply a deep-link a cold-start tap stashed but couldn't navigate to: once now (for a
  // tap that fired before this script ran) and again each time the app becomes visible (for a
  // tap whose immediate navigation lost the race with the launch's initial page load).
  consumePendingNav();
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      consumePendingNav();
    }
  });

  // A push that arrives while the app is in the FOREGROUND is handed to this listener
  // instead of the system tray, so we surface it as a tappable in-app banner. Without
  // this, a foreground push (e.g. an admin using the "send test push" tool from inside
  // the app) is received and silently dropped, which looks like push is broken.
  function showInAppBanner(notification) {
    var title = (notification && notification.title) || "Past Lives";
    var body = (notification && notification.body) || "";
    var data = notification && notification.data;
    var url = data && data.url;

    var existing = document.getElementById("native-push-banner");
    if (existing) {
      existing.remove();
    }

    var banner = document.createElement("div");
    banner.id = "native-push-banner";
    banner.setAttribute("role", "alert");
    banner.style.cssText = [
      "position:fixed",
      "top:calc(env(safe-area-inset-top, 0px) + 12px)",
      "left:12px",
      "right:12px",
      "z-index:2147483647",
      "background:#092E4C",
      "color:#fff",
      "border-radius:12px",
      "box-shadow:0 6px 24px rgba(0,0,0,0.35)",
      "padding:12px 40px 12px 14px",
      "font-family:inherit",
      "cursor:pointer",
      "transform:translateY(-160%)",
      "transition:transform 0.28s ease",
    ].join(";");

    var titleEl = document.createElement("div");
    titleEl.textContent = title;
    titleEl.style.cssText = "font-weight:700;font-size:15px;line-height:1.25;margin-bottom:2px";
    banner.appendChild(titleEl);

    if (body) {
      var bodyEl = document.createElement("div");
      bodyEl.textContent = body;
      bodyEl.style.cssText = "font-size:14px;line-height:1.3;opacity:0.92";
      banner.appendChild(bodyEl);
    }

    var closeEl = document.createElement("button");
    closeEl.type = "button";
    closeEl.setAttribute("aria-label", "Dismiss notification");
    closeEl.textContent = "×";
    closeEl.style.cssText = [
      "position:absolute",
      "top:6px",
      "right:8px",
      "background:transparent",
      "border:0",
      "color:#fff",
      "font-size:22px",
      "line-height:1",
      "opacity:0.8",
      "cursor:pointer",
      "padding:4px",
    ].join(";");
    banner.appendChild(closeEl);

    document.body.appendChild(banner);
    requestAnimationFrame(function () {
      banner.style.transform = "translateY(0)";
    });

    var timer = null;
    function dismiss() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      banner.style.transform = "translateY(-160%)";
      setTimeout(function () {
        if (banner.parentNode) {
          banner.remove();
        }
      }, 300);
    }

    closeEl.addEventListener("click", function (event) {
      event.stopPropagation();
      dismiss();
    });
    banner.addEventListener("click", function () {
      var target = safeNavUrl(url);
      if (target) {
        window.location.href = target;
      }
      dismiss();
    });
    timer = setTimeout(dismiss, 6000);
  }

  PushNotifications.addListener("pushNotificationReceived", function (notification) {
    showInAppBanner(notification);
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
