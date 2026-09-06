/*
 * Biometric sign in for the Capacitor (Android/iOS) app shell.
 *
 * The server mints a rotating secret; the phone keeps it in the Keychain/Keystore behind
 * Face ID or a fingerprint and trades it for a session on the next app open. The biometric
 * never authenticates to the server. It gates local access to the secret, and the server
 * trusts the secret and nothing else.
 *
 * DEFENSIVE ON PURPOSE. This file is served by Django, so it reaches every already-installed
 * app the moment it merges, including the builds that do NOT carry the native plugin. Every
 * exit below is a silent no-op:
 *   - a normal browser (no Capacitor bridge),
 *   - a native build without @capgo/capacitor-native-biometric,
 *   - a phone with no enrolled biometric.
 * Nothing here is verified by pytest or Playwright; there is no Keychain in CI.
 */
(function () {
  "use strict";

  // hx-boost swaps the <body> on hub navigations, which re-runs body scripts. Everything
  // below binds document-level listeners, so running twice would double-bind them.
  if (window.PLBiometricAuthLoaded) {
    return;
  }
  window.PLBiometricAuthLoaded = true;

  var Cap = window.Capacitor;
  if (!Cap || typeof Cap.isNativePlatform !== "function" || !Cap.isNativePlatform()) {
    return; // a normal browser: biometric sign in is an app-only feature
  }

  var Bio = Cap.Plugins && Cap.Plugins.NativeBiometric;
  if (!Bio) {
    return; // a native build from before the plugin shipped
  }

  // The Keychain/Keystore item is filed under a "server". The app id is used rather than a
  // hostname so the stored secret survives pointing the shell at a LAN IP in local dev.
  var SERVER = "app.pastlives.hub";
  var CREDENTIAL_USERNAME = "member";

  // AccessControl.BIOMETRY_ANY from the plugin's definitions.d.ts. The numeric literal is
  // used because the bridge exposes the plugin object, not its TypeScript enums.
  // BIOMETRY_ANY rather than BIOMETRY_CURRENT_SET: the latter invalidates the stored secret
  // whenever the member adds a fingerprint, which would silently drop them back to email
  // codes with nothing on screen to explain it.
  var ACCESS_CONTROL_BIOMETRY_ANY = 2;

  // Remembered decline, so the offer is made once and not on every app open.
  var DECLINED_KEY = "biometric:offer-declined";
  // This device's credential row id, so logout can revoke exactly this phone. Not a secret:
  // the server only honors it for the member whose session sends it. Kept out of the
  // Keychain deliberately, so reading it never raises a biometric prompt.
  var CREDENTIAL_ID_KEY = "biometric:credential-id";

  var ENROLL_URL = "/accounts/biometric/enroll/";
  var UNLOCK_URL = "/accounts/biometric/unlock/";
  var DISABLE_URL = "/accounts/biometric/disable/";

  // Logout must not hang on a slow network waiting for the server to acknowledge a revoke.
  var LOGOUT_CLEANUP_TIMEOUT_MS = 2500;

  function platform() {
    return typeof Cap.getPlatform === "function" && Cap.getPlatform() === "ios" ? "ios" : "android";
  }

  // Client-supplied, and the server treats it as untrusted text and escapes it on render.
  // It only has to be recognizable to the member in their own settings list.
  function deviceLabel() {
    var agent = navigator.userAgent || "";
    if (/iPad/.test(agent)) {
      return "iPad";
    }
    if (/iPhone/.test(agent)) {
      return "iPhone";
    }
    if (platform() === "ios") {
      return "iOS device";
    }
    return "Android device";
  }

  // What to call the biometric in copy shown to this member. BiometryType values come from
  // the plugin's definitions.d.ts; anything unrecognized gets neutral wording rather than a
  // confident wrong guess.
  function biometryName(biometryType) {
    if (biometryType === 1) {
      return "Touch ID";
    }
    if (biometryType === 2) {
      return "Face ID";
    }
    if (biometryType === 3) {
      return "your fingerprint";
    }
    if (biometryType === 4) {
      return "face unlock";
    }
    return "your face or fingerprint";
  }

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

  function isDeclined() {
    try {
      return window.localStorage.getItem(DECLINED_KEY) === "1";
    } catch (err) {
      return false; // storage blocked -> the offer just gets made again next launch
    }
  }

  function setDeclined(value) {
    try {
      if (value) {
        window.localStorage.setItem(DECLINED_KEY, "1");
      } else {
        window.localStorage.removeItem(DECLINED_KEY);
      }
    } catch (err) {
      /* storage blocked -> the choice does not survive this session */
    }
  }

  function storedCredentialId() {
    try {
      return window.localStorage.getItem(CREDENTIAL_ID_KEY);
    } catch (err) {
      return null;
    }
  }

  function setStoredCredentialId(value) {
    try {
      if (value === null) {
        window.localStorage.removeItem(CREDENTIAL_ID_KEY);
      } else {
        window.localStorage.setItem(CREDENTIAL_ID_KEY, String(value));
      }
    } catch (err) {
      /* storage blocked -> logout falls back to revoking every device, below */
    }
  }

  // ── Plugin wrappers ─────────────────────────────────────────────────────────
  // Every one resolves rather than rejects on a plugin error, so a phone in an odd state
  // falls back to the emailed login code instead of throwing into a dead end.

  // { available: bool, biometryType: number }. useFallback stays false: a device PIN is not
  // a biometric, and offering "sign in with your PIN" is not what this feature promises.
  function checkAvailable() {
    return Bio.isAvailable({ useFallback: false }).then(
      function (result) {
        return { available: !!(result && result.isAvailable), biometryType: result && result.biometryType };
      },
      function () {
        return { available: false, biometryType: undefined };
      }
    );
  }

  function hasStoredSecret() {
    return Bio.isCredentialsSaved({ server: SERVER }).then(
      function (result) {
        return !!(result && result.isSaved);
      },
      function () {
        return false;
      }
    );
  }

  function storeSecret(secret) {
    return Bio.setCredentials({
      username: CREDENTIAL_USERNAME,
      password: secret,
      server: SERVER,
      accessControl: ACCESS_CONTROL_BIOMETRY_ANY,
      title: "Protect your Past Lives sign in",
      negativeButtonText: "Cancel",
    });
  }

  // Shows the biometric prompt: on iOS the system draws it when the protected Keychain item
  // is read, on Android the plugin binds a BiometricPrompt to the decryption key.
  function readSecret() {
    return Bio.getSecureCredentials({
      server: SERVER,
      reason: "Sign in to Past Lives",
      title: "Sign in to Past Lives",
      negativeButtonText: "Use a code instead",
    }).then(function (credentials) {
      return credentials && credentials.password;
    });
  }

  function clearSecret() {
    return Bio.deleteCredentials({ server: SERVER }).catch(function () {
      /* nothing stored, or the plugin refused: there is nothing left to clean up */
    });
  }

  // Forget this device locally: the secret and the id that names its server-side row.
  function forgetDevice() {
    setStoredCredentialId(null);
    return clearSecret();
  }

  // ── Server calls ────────────────────────────────────────────────────────────

  function enroll() {
    return postJSON(ENROLL_URL, { device_label: deviceLabel(), platform: platform() })
      .then(function (response) {
        // fetch only rejects on a network failure, so a 400 or a 500 arrives looking like
        // success. Reading response.ok is the difference between storing a secret and
        // storing the string "undefined".
        if (!response.ok) {
          throw new Error("biometric enroll rejected: " + response.status);
        }
        return response.json();
      })
      .then(function (body) {
        return storeSecret(body.secret).then(function () {
          setStoredCredentialId(body.credential_id);
        });
      });
  }

  // Resolves true when the member is signed in, false when the credential is dead and the
  // emailed login code is the way through. Rejects only on an unexpected failure.
  function unlock() {
    return readSecret().then(function (secret) {
      if (!secret) {
        return false;
      }
      return postJSON(UNLOCK_URL, { secret: secret }).then(function (response) {
        if (response.status === 401) {
          // Spent, revoked, or expired. Drop the stored secret so the member is not trapped
          // pressing a dead button, and let the normal login-code form take over.
          return forgetDevice().then(function () {
            return false;
          });
        }
        if (response.status === 429) {
          // Rate limited, NOT a dead credential. Deliberately does not clear the secret:
          // the cap is per IP, so one busy shared connection would otherwise wipe the
          // enrollment of every member behind it. Fall through to the login-code form.
          return false;
        }
        if (!response.ok) {
          throw new Error("biometric unlock failed: " + response.status);
        }
        return response.json().then(function (body) {
          // Store the rotated secret BEFORE navigating. The old one is already spent, so a
          // navigation that interrupts this write costs the member their enrollment. The
          // server's 60 second grace window softens that; it does not remove it.
          return storeSecret(body.secret).then(
            function () {
              return true;
            },
            function () {
              // The write failed, so the Keychain still holds the SPENT secret. Leaving it
              // there is worse than clearing it: the next unlock would replay a spent
              // secret, which the server correctly reads as theft and revokes for. The
              // member is already signed in, so drop the credential and let them re-enrol.
              return forgetDevice().then(function () {
                return true;
              });
            }
          );
        });
      });
    });
  }

  // Revokes THIS phone's credential, named by the id enrollment handed back. A member
  // logging out on their phone is not asking to break sign in on their tablet, so an empty
  // body (which revokes everything) is only the fallback for a device that never stored an
  // id — a browser with storage blocked, or an enrollment from before this shipped.
  function disableOnServer() {
    var credentialId = storedCredentialId();
    var payload = credentialId === null ? {} : { credential_id: credentialId };
    return postJSON(DISABLE_URL, payload).catch(function () {
      /* offline: the local secret is cleared regardless, and Settings can revoke later */
    });
  }

  // ── The offer ───────────────────────────────────────────────────────────────

  function isAuthenticated() {
    return document.body && document.body.getAttribute("data-pl-authenticated") === "1";
  }

  // Styled inline rather than from a stylesheet, for the same reason the push banner in
  // native-push.js is: this prompt can appear on a plain base.html page (style.css) or a hub
  // page (hub.css + components.css), and neither sheet is loaded on the other's pages. One
  // set of inline rules is the only way it looks the same everywhere.
  function offerButtonStyle(primary) {
    return [
      "flex:1",
      "padding:10px 14px",
      "border-radius:8px",
      "border:0",
      "font:inherit",
      "font-size:14px",
      "font-weight:700",
      "cursor:pointer",
      primary ? "background:#EEB44B" : "background:rgba(255,255,255,0.12)",
      primary ? "color:#092E4C" : "color:#fff",
    ].join(";");
  }

  function buildOffer(name, onEnable, onDecline) {
    var card = document.createElement("div");
    card.className = "pl-biometric-offer";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-label", "Turn on biometric sign in");
    card.style.cssText = [
      "position:fixed",
      "left:12px",
      "right:12px",
      "bottom:calc(env(safe-area-inset-bottom, 0px) + 12px)",
      "z-index:2147483646",
      "background:#092E4C",
      "color:#fff",
      "border-radius:12px",
      "box-shadow:0 6px 24px rgba(0,0,0,0.35)",
      "padding:16px",
      "font-family:inherit",
    ].join(";");

    var title = document.createElement("h2");
    title.className = "pl-biometric-offer__title";
    title.textContent = "Sign In Faster Next Time";
    title.style.cssText = "margin:0 0 6px;font-size:16px;line-height:1.25;font-weight:700";
    card.appendChild(title);

    var body = document.createElement("p");
    body.className = "pl-biometric-offer__text";
    body.textContent =
      "Use " + name + " to sign in instead of waiting for an emailed code. You can turn this on or off later in Settings.";
    body.style.cssText = "margin:0 0 14px;font-size:14px;line-height:1.4;opacity:0.92";
    card.appendChild(body);

    var actions = document.createElement("div");
    actions.className = "pl-biometric-offer__actions";
    actions.style.cssText = "display:flex;gap:8px";

    var enableBtn = document.createElement("button");
    enableBtn.type = "button";
    enableBtn.className = "pl-biometric-offer__enable";
    enableBtn.textContent = "Turn It On";
    enableBtn.style.cssText = offerButtonStyle(true);
    actions.appendChild(enableBtn);

    var laterBtn = document.createElement("button");
    laterBtn.type = "button";
    laterBtn.className = "pl-biometric-offer__later";
    laterBtn.textContent = "Not Now";
    laterBtn.style.cssText = offerButtonStyle(false);
    actions.appendChild(laterBtn);

    card.appendChild(actions);

    function close() {
      if (card.parentNode) {
        card.remove();
      }
    }

    enableBtn.addEventListener("click", function () {
      enableBtn.disabled = true;
      laterBtn.disabled = true;
      onEnable().then(close, function () {
        body.textContent = "That did not work. You can try again from Settings.";
        enableBtn.remove();
        laterBtn.disabled = false;
        laterBtn.textContent = "Close";
      });
    });

    laterBtn.addEventListener("click", function () {
      onDecline();
      close();
    });

    document.body.appendChild(card);
    return card;
  }

  function maybeOffer() {
    if (!isAuthenticated() || isDeclined() || document.getElementById("biometric-unlock")) {
      return Promise.resolve();
    }
    return checkAvailable().then(function (state) {
      if (!state.available) {
        return; // never offer on a phone with no enrolled biometric
      }
      return hasStoredSecret().then(function (stored) {
        if (stored) {
          return;
        }
        buildOffer(
          biometryName(state.biometryType),
          function () {
            return enroll().then(function () {
              setDeclined(false);
              if (document.getElementById("biometric-devices")) {
                // The settings card is server rendered, so the new row only appears on a
                // fresh render. Reloading is also how the card's own button reports success.
                window.location.reload();
              }
            });
          },
          function () {
            setDeclined(true);
          }
        );
      });
    });
  }

  // ── The login page ──────────────────────────────────────────────────────────

  // Where to land after a successful unlock, from the login page's ?next=.
  //
  // The value is attacker supplied: anyone can send a member a link to the login page with
  // any ?next= on it. A prefix test is NOT enough to make it safe. "/\\evil.com" starts with
  // one slash and its second character is not a slash, so a charAt check waves it through,
  // and browsers then normalize the backslash to a slash, turning it into the protocol
  // relative "//evil.com" and sending the member off site right after they signed in. That
  // is a phishing handoff wearing our login page as the first hop.
  //
  // So parse it and let the URL parser do the normalizing, then insist on our own origin and
  // rebuild the target from the parsed parts. Nothing attacker supplied reaches the browser
  // as a raw string.
  function safeNextUrl(raw) {
    if (!raw) {
      return "/";
    }
    var parsed;
    try {
      parsed = new URL(raw, window.location.origin);
    } catch (err) {
      return "/"; // malformed -> home
    }
    if (parsed.origin !== window.location.origin) {
      return "/";
    }
    return parsed.pathname + parsed.search + parsed.hash;
  }

  function runUnlock(mount, button, statusEl) {
    button.disabled = true;
    statusEl.textContent = "Waiting for you to confirm it is really you.";
    return unlock()
      .then(function (signedIn) {
        if (signedIn) {
          statusEl.textContent = "Signed in. One moment.";
          var next = new URLSearchParams(window.location.search).get("next");
          window.location.replace(safeNextUrl(next));
          return;
        }
        // Nothing usable is stored any more: hide the button rather than leave a control
        // that can only fail, and let the login-code form below it do the work.
        mount.hidden = true;
      })
      .catch(function () {
        button.disabled = false;
        statusEl.textContent = "That did not work. Use the emailed code below.";
      });
  }

  function setUpLoginPage() {
    var mount = document.getElementById("biometric-unlock");
    if (!mount) {
      return Promise.resolve();
    }
    return checkAvailable().then(function (state) {
      if (!state.available) {
        return;
      }
      return hasStoredSecret().then(function (stored) {
        if (!stored) {
          return;
        }
        var button = mount.querySelector("[data-biometric-unlock-button]");
        var statusEl = mount.querySelector("[data-biometric-unlock-status]");
        if (!button || !statusEl) {
          return;
        }
        button.textContent = "Sign in with " + biometryName(state.biometryType);
        mount.hidden = false;
        button.addEventListener("click", function () {
          runUnlock(mount, button, statusEl);
        });
        // Offer it without being asked: the member opened the app to get in.
        return runUnlock(mount, button, statusEl);
      });
    });
  }

  // ── The settings card ───────────────────────────────────────────────────────

  // Reveals the card's app-only parts. Re-run after an htmx swap replaces the card, which
  // is what the per-row Revoke button does.
  function paintSettingsCard() {
    var card = document.getElementById("biometric-devices");
    if (!card) {
      return Promise.resolve();
    }
    var appOnly = card.querySelector("[data-biometric-app-only]");
    var enableBtn = card.querySelector("[data-biometric-enable]");
    var noteEl = card.querySelector("[data-biometric-note]");
    if (!appOnly || !enableBtn || !noteEl || card.dataset.biometricPainted === "1") {
      return Promise.resolve(); // an htmx swap brings a fresh card, so the flag comes back unset
    }
    card.dataset.biometricPainted = "1";
    appOnly.hidden = false;

    return checkAvailable().then(function (state) {
      if (!state.available) {
        noteEl.textContent = "This phone has no face or fingerprint set up, so there is nothing to turn on here.";
        noteEl.hidden = false;
        return;
      }
      return hasStoredSecret().then(function (stored) {
        if (stored) {
          noteEl.textContent = "This device is set up. Revoking it below sends this phone back to emailed codes.";
          noteEl.hidden = false;
          return;
        }
        enableBtn.textContent = "Turn on " + biometryName(state.biometryType) + " sign in";
        enableBtn.hidden = false;
        enableBtn.addEventListener("click", function onEnable() {
          enableBtn.removeEventListener("click", onEnable);
          enableBtn.disabled = true;
          enroll().then(
            function () {
              setDeclined(false);
              window.location.reload(); // the card is server rendered, so re-render the list
            },
            function () {
              enableBtn.disabled = false;
              noteEl.textContent = "That did not work. Try again in a moment.";
              noteEl.hidden = false;
            }
          );
        });
      });
    });
  }

  // ── Logout ──────────────────────────────────────────────────────────────────

  // Clearing the Keychain entry and revoking server side is done HERE, in the app, and not
  // from a user_logged_out signal: a member logging out on the web must not silently kill
  // biometric sign in on their phone.
  function handleLogoutSubmit(event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM" || form.dataset.biometricCleaned === "1") {
      return;
    }
    var action = form.getAttribute("action") || "";
    if (action.indexOf("/logout/") === -1) {
      return;
    }
    event.preventDefault();
    form.dataset.biometricCleaned = "1";

    var done = false;
    function proceed() {
      if (done) {
        return;
      }
      done = true;
      form.submit();
    }
    // Never let a slow network hold a member inside an account they asked to leave.
    setTimeout(proceed, LOGOUT_CLEANUP_TIMEOUT_MS);
    // The server call reads the id, so it has to be made before forgetDevice clears it.
    var revoked = disableOnServer();
    Promise.all([revoked, forgetDevice()]).then(proceed, proceed);
  }

  // ── Wiring ──────────────────────────────────────────────────────────────────

  function start() {
    setUpLoginPage();
    maybeOffer();
    paintSettingsCard();
  }

  document.addEventListener("submit", handleLogoutSubmit, true);
  // The settings card comes back as an htmx swap after a Revoke, which drops its wiring.
  document.body.addEventListener("htmx:afterSwap", function () {
    paintSettingsCard();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  // Exposed so a page can re-check this device without re-deriving any of the plugin work.
  window.PLBiometricAuth = {
    isAvailable: checkAvailable,
    hasStoredSecret: hasStoredSecret,
    enroll: enroll,
    clearSecret: clearSecret,
    refresh: paintSettingsCard,
  };
})();
