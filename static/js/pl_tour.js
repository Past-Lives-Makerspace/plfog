/*
 * Guided tours bootstrap (Spec C §6.2). Loaded only on tour entry pages via
 * templates/hub/partials/_tour.html; Driver.js itself (static/js/driver.min.js +
 * static/css/driver.css, vendored v1.3.6) is injected lazily the moment a tour
 * starts — ordinary pageviews never pay for it.
 *
 * hx-boost re-executes this script on boosted navigations, so it is a guarded
 * window.plTour IIFE with no top-level const/let: the module is defined once,
 * and every execution just calls init() to re-read the page's payload and bind
 * fresh DOM (the data-pl-tour-init attribute guards event binding; the
 * window.plTour check guards re-declaration — both guards are needed).
 *
 * Recording contract (locked): all state recording lives in onDestroyStarted.
 * Driver.js 1.x suppresses its default teardown when that hook is provided, so
 * the hook MUST call driverObj.destroy() itself — destroy() bypasses the hook
 * and runs the real teardown. Esc / the popover ✕ / an overlay click all route
 * through the hook (dismissed unless on the last step); htmx history/swap
 * teardown calls destroy() directly and records nothing (navigating away is
 * not deciding). Focus management is ours, not the library's: Driver.js 1.x
 * neither focuses the popover nor restores focus on close.
 */
(function () {
    "use strict";

    if (!window.plTour) {
        window.plTour = (function () {
            var assetsPromise = null; // cached so double-start is safe
            var current = null; // this page's payload (re-read on every init)
            var csrf = "";
            var active = null; // { driverObj, sidebarPrev, restoreFocus }
            var htmxBound = false;

            function readPayload() {
                var data = document.getElementById("pl-tour-data");
                var root = document.getElementById("pl-tour-root");
                if (!data || !root) {
                    current = null;
                    return;
                }
                current = JSON.parse(data.textContent);
                csrf = root.getAttribute("data-csrf") || "";
            }

            function loadAssets() {
                if (assetsPromise) return assetsPromise;
                assetsPromise = new Promise(function (resolve, reject) {
                    var root = document.getElementById("pl-tour-root");
                    if (!document.querySelector("link[data-pl-driver-css]")) {
                        var link = document.createElement("link");
                        link.rel = "stylesheet";
                        link.href = root.getAttribute("data-driver-css");
                        link.setAttribute("data-pl-driver-css", "");
                        document.head.appendChild(link);
                    }
                    if (window.driver && window.driver.js) {
                        resolve();
                        return;
                    }
                    var script = document.createElement("script");
                    script.src = root.getAttribute("data-driver-js");
                    script.onload = function () {
                        resolve();
                    };
                    script.onerror = function () {
                        assetsPromise = null; // allow a retry next start
                        reject(new Error("driver.min.js failed to load"));
                    };
                    document.head.appendChild(script);
                });
                return assetsPromise;
            }

            function postState(status) {
                fetch(current.state_url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": csrf,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    body: "status=" + encodeURIComponent(status),
                    keepalive: true,
                }).catch(function () {
                    // Worst case on a lost dismiss: the row stays OFFERED and the
                    // offer reappears next visit — honest and self-healing.
                });
            }

            function bodyAlpineState() {
                if (!window.Alpine || !window.Alpine.$data) return null;
                try {
                    return window.Alpine.$data(document.body) || null;
                } catch (e) {
                    return null;
                }
            }

            function cleanup() {
                if (!active) return;
                var finished = active;
                active = null;
                if (finished.sidebarPrev !== undefined) {
                    var bodyState = bodyAlpineState();
                    if (bodyState) bodyState.sidebarOpen = finished.sidebarPrev;
                }
                if (finished.restoreFocus && document.contains(finished.restoreFocus)) {
                    finished.restoreFocus.focus({ preventScroll: true });
                }
            }

            function buildSteps() {
                // Missing/hidden targets are dropped BEFORE the driver is built, so
                // progress numbering stays contiguous — a renamed element degrades
                // to a shorter tour, never a broken one. Element-less (centered)
                // steps always survive.
                var steps = [];
                current.steps.forEach(function (step) {
                    if (!step.target) {
                        steps.push({ popover: { title: step.title, description: step.body } });
                        return;
                    }
                    var el = document.querySelector(step.target);
                    if (el && el.offsetParent !== null) {
                        steps.push({ element: step.target, popover: { title: step.title, description: step.body } });
                    }
                });
                return steps;
            }

            function runTour(auto, sidebarPrev, restoreFocus) {
                var steps = buildSteps();
                // <2 surviving steps aborts an auto-offered start silently (records
                // nothing); a manual start still shows what remains.
                if (steps.length === 0 || (auto && steps.length < 2)) {
                    active = { sidebarPrev: sidebarPrev, restoreFocus: null };
                    cleanup();
                    return;
                }
                var theme = document.documentElement.getAttribute("data-theme") || window.__plTheme || "dark";
                var driverObj = window.driver.js.driver({
                    steps: steps,
                    popoverClass: "pl-tour",
                    showProgress: true,
                    allowClose: true,
                    animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
                    overlayColor: theme === "light" ? "rgba(29, 30, 30, 0.45)" : "rgba(4, 5, 8, 0.72)",
                    overlayOpacity: 1,
                    nextBtnText: "Next",
                    prevBtnText: "Back",
                    doneBtnText: "Done",
                    onHighlighted: function () {
                        var popover = document.querySelector(".driver-popover");
                        if (popover) {
                            popover.setAttribute("tabindex", "-1");
                            popover.focus({ preventScroll: true });
                        }
                    },
                    onDestroyStarted: function () {
                        // Providing this hook suppresses Driver's default teardown —
                        // record, then destroy ourselves (destroy() bypasses the hook).
                        postState(driverObj.isLastStep() ? "completed" : "dismissed");
                        driverObj.destroy();
                    },
                    onDestroyed: cleanup,
                });
                active = { driverObj: driverObj, sidebarPrev: sidebarPrev, restoreFocus: restoreFocus };
                driverObj.drive();
            }

            function start(auto) {
                if (!current || active) return;
                var sidebarPrev; // undefined = nothing to restore
                if (current.opens_sidebar) {
                    var bodyState = bodyAlpineState();
                    if (bodyState) {
                        sidebarPrev = bodyState.sidebarOpen;
                        bodyState.sidebarOpen = true;
                    }
                }
                var restoreFocus = document.activeElement;
                loadAssets().then(
                    function () {
                        // One frame so the sidebar flip has painted before the
                        // visibility filter runs (sidebar targets must count as visible).
                        requestAnimationFrame(function () {
                            runTour(auto, sidebarPrev, restoreFocus);
                        });
                    },
                    function () {
                        if (sidebarPrev !== undefined) {
                            var bodyState = bodyAlpineState();
                            if (bodyState) bodyState.sidebarOpen = sidebarPrev;
                        }
                    }
                );
            }

            function teardown() {
                // htmx history snapshot / swap: kill the tour without recording —
                // the member navigated, they didn't decide. destroy() bypasses
                // onDestroyStarted; onDestroyed still runs cleanup().
                if (active && active.driverObj) active.driverObj.destroy();
            }

            function bindOffer() {
                var offer = document.querySelector("[data-pl-tour-offer]");
                if (!offer || offer.hasAttribute("data-pl-tour-init")) return;
                offer.setAttribute("data-pl-tour-init", "");
                offer.querySelector("[data-tour-accept]").addEventListener("click", function () {
                    offer.remove();
                    start(true);
                });
                offer.querySelector("[data-tour-decline]").addEventListener("click", function () {
                    // Remove immediately — don't wait on the response. Quiet: no toast.
                    postState("dismissed");
                    offer.remove();
                });
            }

            function init() {
                readPayload();
                if (!htmxBound) {
                    htmxBound = true;
                    ["htmx:beforeHistorySave", "htmx:beforeSwap"].forEach(function (name) {
                        document.body.addEventListener(name, teardown);
                    });
                }
                if (!current) return;
                bindOffer();
                if (current.autostart) start(false); // ?tour= manual start
            }

            return { init: init, start: start, teardown: teardown };
        })();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            window.plTour.init();
        });
    } else {
        window.plTour.init();
    }
})();
