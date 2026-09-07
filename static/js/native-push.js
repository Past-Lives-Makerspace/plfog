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

  var platform = typeof Cap.getPlatform === "function" ? Cap.getPlatform() : "android";

  // iOS uses @capacitor-firebase/messaging because @capacitor/push-notifications hands iOS
  // the raw APNs device token, which FCM's message.token field rejects. Android keeps
  // @capacitor/push-notifications - it ships, it works, and this file reaches every installed
  // Android app the moment it merges, so its behavior here must not change.
  //
  // The two plugins differ in three ways that matter: what the listeners are called, whether a
  // notification arrives bare or wrapped in a { notification } envelope, and how a token is
  // asked for. This adapter normalizes all three so the rest of the file stays platform blind.
  // Returns null when the platform's plugin is missing from the build - notably the CURRENT iOS
  // app, which has no FirebaseMessaging - so the caller bails exactly as it always has.
  function pushBridge(platform, Plugins) {
    if (platform === "ios") {
      var FirebaseMessaging = Plugins && Plugins.FirebaseMessaging;
      if (!FirebaseMessaging) {
        return null;
      }
      var tokenHandler = null;
      var tokenErrorHandler = null;
      return {
        onToken: function (cb) {
          tokenHandler = cb; // ensurePermissionAndRegister resolves the FIRST token through getToken()
          FirebaseMessaging.addListener("tokenReceived", function (event) {
            cb(event && event.token); // TokenReceivedEvent is { token }, not { value }
          });
        },
        onTokenError: function (cb) {
          tokenErrorHandler = cb; // FirebaseMessaging has no registrationError event; getToken() rejects instead
        },
        onReceived: function (cb) {
          FirebaseMessaging.addListener("notificationReceived", function (event) {
            cb(event && event.notification); // unwrap the envelope so the banner still reads notification.data.url
          });
        },
        onActionPerformed: function (cb) {
          FirebaseMessaging.addListener("notificationActionPerformed", cb); // already carries .notification
        },
        checkPermissions: function () {
          return FirebaseMessaging.checkPermissions();
        },
        ensurePermissionAndRegister: function () {
          return FirebaseMessaging.checkPermissions()
            .then(function (status) {
              if (status.receive === "prompt" || status.receive === "prompt-with-rationale") {
                return FirebaseMessaging.requestPermissions();
              }
              return status;
            })
            .then(function (status) {
              if (status.receive !== "granted") {
                return status;
              }
              return FirebaseMessaging.getToken().then(
                function (result) {
                  if (tokenHandler && result && result.token) {
                    tokenHandler(result.token);
                  }
                  return status;
                },
                function (err) {
                  if (tokenErrorHandler) {
                    tokenErrorHandler(err);
                  }
                  return status;
                }
              );
            });
        },
      };
    }
    var PushNotifications = Plugins && Plugins.PushNotifications;
    if (!PushNotifications) {
      return null;
    }
    return {
      onToken: function (cb) {
        PushNotifications.addListener("registration", function (token) {
          cb(token.value);
        });
      },
      onTokenError: function (cb) {
        PushNotifications.addListener("registrationError", cb);
      },
      onReceived: function (cb) {
        PushNotifications.addListener("pushNotificationReceived", cb); // hands the notification itself, no envelope
      },
      onActionPerformed: function (cb) {
        PushNotifications.addListener("pushNotificationActionPerformed", cb);
      },
      checkPermissions: function () {
        return PushNotifications.checkPermissions();
      },
      ensurePermissionAndRegister: function () {
        return PushNotifications.checkPermissions()
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
            return status;
          });
      },
    };
  }

  var bridge = pushBridge(platform, Cap.Plugins);
  if (!bridge) {
    return; // push plugin not present in this build
  }

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
    // Deliberately reaches past the bridge: channels are an Android-only concept and the
    // Android transport is, and stays, @capacitor/push-notifications.
    var PushNotifications = Cap.Plugins && Cap.Plugins.PushNotifications;
    if (platform !== "android" || !PushNotifications || typeof PushNotifications.createChannel !== "function") {
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

  // The most recent token this device produced, so the settings card can unregister
  // exactly this device without asking the plugin for the token a second time.
  var lastToken = null;

  // A member who turns push off on this device would otherwise be re-registered by the very
  // next page load, because the OS permission is still granted. This flag is that member's
  // "no" and it is checked before registering. The key is new, so it is always absent on
  // every shipped app: the Android path below behaves exactly as it does today.
  var OPT_OUT_KEY = "native-push:opted-out";

  function isOptedOut() {
    try {
      return window.localStorage.getItem(OPT_OUT_KEY) === "1";
    } catch (err) {
      return false; // storage blocked -> treat as opted in, same as before this flag existed
    }
  }

  function setOptedOut(value) {
    try {
      if (value) {
        window.localStorage.setItem(OPT_OUT_KEY, "1");
      } else {
        window.localStorage.removeItem(OPT_OUT_KEY);
      }
    } catch (err) {
      /* storage blocked -> the choice just doesn't survive this session */
    }
  }

  // Why the server can refuse a token, so the settings card can say so instead of claiming
  // push is on when nothing can reach the device: null (fine), "conflict" (HTTP 409, the token
  // is still bound to whoever last used this device and never signed out), or "failed".
  var registerProblem = null;
  var changeHandlers = [];

  function notifyChanged() {
    changeHandlers.forEach(function (cb) {
      cb();
    });
  }

  function registerDevice(token) {
    return postJSON("/push/fcm/register/", { token: token, platform: platform })
      .then(function (response) {
        // fetch only rejects on a network failure, so a 409 or a 500 lands here looking like
        // success. Reading response.ok is the difference between "push is on" and a lie.
        if (response.ok) {
          registerProblem = null;
        } else {
          registerProblem = response.status === 409 ? "conflict" : "failed";
          console.error("[native-push] token register rejected", response.status);
        }
      })
      .catch(function (err) {
        console.error("[native-push] token register failed", err);
        registerProblem = "failed";
      })
      .then(notifyChanged);
  }

  bridge.onToken(function (token) {
    // Remember the token even while opted out: it describes the device, not the member's
    // consent, and the card needs it to unregister exactly this device. The opt-out gates the
    // side effect below, not this bookkeeping.
    lastToken = token;
    if (isOptedOut()) {
      // iOS replays tokenReceived on every launch (the plugin emits it retainUntilConsumed) and
      // Android re-fires it whenever the token rotates. Without this gate, a member who turned
      // push off is silently re-registered the next time they open the app, and the card would
      // still read "off" while the phone buzzes.
      return;
    }
    registerDevice(token);
  });

  bridge.onTokenError(function (err) {
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
  bridge.onActionPerformed(function (action) {
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

  bridge.onReceived(function (notification) {
    showInAppBanner(notification);
  });

  if (!isOptedOut()) {
    bridge.ensurePermissionAndRegister().catch(function (err) {
      console.error("[native-push] permission/register failed", err);
    });
  }

  // Exposed for the "Push On This Device" card in notification settings
  // (templates/hub/partials/_push_this_device.html) so the card drives this same permission,
  // register, and unregister path instead of re-deriving it, plugin branch and all.
  window.PLNativePush = {
    // Resolves to the plugin's PermissionStatus: { receive: "granted" | "denied" | "prompt" |
    // "prompt-with-rationale" }. Asks nothing of the member; safe to call on page load.
    // This plus isOptedOut is the honest device status. Deliberately NOT a "has a token yet"
    // check: the token lands a moment after page load, so reading it would report "off" on a
    // device where push is working.
    checkPermissions: function () {
      return bridge.checkPermissions();
    },

    isOptedOut: isOptedOut,

    // null when the last registration succeeded (or none has run yet), else "conflict" or
    // "failed". Pair it with checkPermissions: the OS can say granted while the server refused.
    registrationProblem: function () {
      return registerProblem;
    },

    // Registration finishes after the settings card first paints, so the card subscribes here
    // and repaints when the answer lands.
    onChange: function (cb) {
      changeHandlers.push(cb);
    },

    // Clears the opt-out, then runs the normal prompt-and-register path. Resolves to the
    // resulting PermissionStatus, so the caller can repaint from the real OS state.
    enable: function () {
      setOptedOut(false);
      return bridge.ensurePermissionAndRegister();
    },

    // Drops this device's token server side and remembers the "no" so the next page load
    // does not silently re-register it. Rejects if no token has arrived yet, rather than
    // claiming push is off when it is not.
    disable: function () {
      if (lastToken === null) {
        return Promise.reject(new Error("no token for this device yet"));
      }
      var token = lastToken;
      return postJSON("/push/fcm/unregister/", { token: token }).then(function (response) {
        if (!response.ok) {
          throw new Error("unregister failed: " + response.status);
        }
        setOptedOut(true);
        lastToken = null;
        registerProblem = null; // whatever the server said about the old row no longer applies
        return true;
      });
    },
  };
})();
