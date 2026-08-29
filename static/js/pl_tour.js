/*
 * Guided tours runtime — a SEGMENT PLAYER over a persisted itinerary.
 *
 * A tour narrates a step, then drives the browser to the next screen for you:
 * a page hop (a full navigation carrying the resume param) or a same-page
 * Alpine tab flip, detects the new surface has loaded, and keeps narrating.
 * Driver.js (static/js/driver.min.js + static/css/driver.css, vendored v1.3.6)
 * is the spotlight lib, injected lazily the moment a tour starts.
 *
 * Persistence: the running tour survives navigation via BOTH sessionStorage
 * ("plTourRun") and a "?tour=<key>&step=<n>" URL param the navigation carries,
 * so the tour re-hydrates and resumes at the right step on the destination.
 *
 * Robustness posture (this runs live on a demo stage): nothing should freeze.
 * Every hop is guarded — a waitFor cap (~4s) lets async content paint, a
 * missing target degrades to a centered popover (never a hang), a wrong-page
 * 302 aborts cleanly, and Esc / the ✕ / an overlay click always end the tour
 * and strip the URL param so a refresh does not silently restart it.
 *
 * hx-boost re-executes this script on boosted navigations, so it is a guarded
 * window.plTour IIFE with no top-level const/let: the module is defined once,
 * and every execution just calls init() to re-read the payload and resume.
 */
(function () {
    "use strict";

    if (!window.plTour) {
        window.plTour = (function () {
            var RUN_KEY = "plTourRun";
            var WAIT_CAP_MS = 4000;

            var assetsPromise = null; // cached so double-start is safe
            var current = null; // this page's payload (re-read on every init)
            var csrf = "";
            var active = null; // { driverObj, segStart, segEnd } for the running segment
            var session = null; // cross-segment state: { sidebarPrev, restoreFocus }
            var htmxBound = false;
            var ending = false; // the tour is finishing (restore sidebar/focus on destroy)

            // ── payload + assets ────────────────────────────────────────────
            function readPayload() {
                var data = document.getElementById("pl-tour-data");
                var root = document.getElementById("pl-tour-root");
                if (!data || !root) {
                    current = null;
                    return;
                }
                try {
                    current = JSON.parse(data.textContent);
                } catch (e) {
                    current = null;
                    return;
                }
                csrf = root.getAttribute("data-csrf") || "";
            }

            function loadAssets() {
                if (assetsPromise) return assetsPromise;
                assetsPromise = new Promise(function (resolve, reject) {
                    var root = document.getElementById("pl-tour-root");
                    if (!root) {
                        reject(new Error("no tour root"));
                        return;
                    }
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
                if (!current) return;
                // A normal, normally-prioritized fetch. We do NOT use keepalive: every
                // caller (finish on completed / Esc / the close button) fires while the
                // page stays loaded, so there is no unload to survive — and keepalive
                // requests are deprioritized by the browser, which delayed this POST and
                // left the recorded state lagging.
                fetch(current.state_url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": csrf,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    body: "status=" + encodeURIComponent(status),
                }).catch(function () {
                    // Worst case on a lost dismiss: the row stays OFFERED and the
                    // offer reappears next visit — honest and self-healing.
                });
            }

            // ── persisted running state ─────────────────────────────────────
            function saveRun(index) {
                try {
                    var payload = {
                        key: current.key,
                        index: index,
                        total: current.steps.length,
                        sidebarPrev: session ? session.sidebarPrev : undefined,
                    };
                    window.sessionStorage.setItem(RUN_KEY, JSON.stringify(payload));
                } catch (e) {
                    /* private mode / no storage — the URL param still carries resume */
                }
            }

            function readRun() {
                try {
                    var raw = window.sessionStorage.getItem(RUN_KEY);
                    return raw ? JSON.parse(raw) : null;
                } catch (e) {
                    return null;
                }
            }

            function clearRun() {
                try {
                    window.sessionStorage.removeItem(RUN_KEY);
                } catch (e) {
                    /* nothing to clear */
                }
            }

            // ── Alpine helpers (tab flips + sidebar) ────────────────────────
            function bodyAlpineState() {
                if (!window.Alpine || !window.Alpine.$data) return null;
                try {
                    return window.Alpine.$data(document.body) || null;
                } catch (e) {
                    return null;
                }
            }

            function findAlpineOwner(varName) {
                // The element whose Alpine scope owns varName (e.g. the guild
                // editor root owns `section`; <body> owns `sidebarOpen`).
                if (!window.Alpine || !window.Alpine.$data) return null;
                var nodes = document.querySelectorAll("[x-data]");
                for (var i = 0; i < nodes.length; i++) {
                    var data = null;
                    try {
                        data = window.Alpine.$data(nodes[i]);
                    } catch (e) {
                        data = null;
                    }
                    if (data && varName in data) return data;
                }
                return null;
            }

            function applyTabOrClick(step) {
                if (step.tab_set) {
                    var owner = findAlpineOwner(step.tab_set[0]);
                    if (owner) {
                        try {
                            owner[step.tab_set[0]] = step.tab_set[1];
                        } catch (e) {
                            /* scope went away — the target simply won't show; waitFor handles it */
                        }
                    }
                } else if (step.click) {
                    var el = document.querySelector(step.click);
                    if (el) el.click();
                }
            }

            // ── the async guard ─────────────────────────────────────────────
            function isVisible(el) {
                return !!(el && el.offsetParent !== null);
            }

            function waitFor(step) {
                return new Promise(function (resolve) {
                    var selector = step.wait_for || step.target;
                    if (!selector) {
                        resolve(true); // centered step — nothing to wait on
                        return;
                    }
                    var startedAt = Date.now();
                    (function poll() {
                        if (isVisible(document.querySelector(selector))) {
                            resolve(true);
                            return;
                        }
                        if (Date.now() - startedAt > WAIT_CAP_MS) {
                            resolve(false); // never hang — the step degrades to centered
                            return;
                        }
                        requestAnimationFrame(poll);
                    })();
                });
            }

            // ── the advancing affordance ────────────────────────────────────
            function advancingEl() {
                return document.getElementById("pl-tour-advancing");
            }

            function showAdvancing() {
                var el = advancingEl();
                if (el) el.hidden = false;
            }

            function hideAdvancing() {
                var el = advancingEl();
                if (el) el.hidden = true;
            }

            // ── segments ────────────────────────────────────────────────────
            function stepStartsSegment(i) {
                if (i === 0) return true;
                var s = current.steps[i];
                return !!(s.navigate || s.tab_set || s.click);
            }

            function segmentBounds(globalIndex) {
                var start = globalIndex;
                while (start > 0 && !stepStartsSegment(start)) start--;
                var end = start;
                while (end + 1 < current.steps.length && !stepStartsSegment(end + 1)) end++;
                return { start: start, end: end };
            }

            function clampIndex(index) {
                var last = current.steps.length - 1;
                if (last < 0) return 0;
                if (index < 0) return 0;
                if (index > last) return last;
                return index;
            }

            // ── URL param plumbing ──────────────────────────────────────────
            function withTourParams(href, key, step) {
                var sep = href.indexOf("?") === -1 ? "?" : "&";
                return href + sep + "tour=" + encodeURIComponent(key) + "&step=" + step;
            }

            function stripTourParams() {
                try {
                    var url = new URL(window.location.href);
                    url.searchParams.delete("tour");
                    url.searchParams.delete("step");
                    var qs = url.searchParams.toString();
                    var clean = url.pathname + (qs ? "?" + qs : "") + url.hash;
                    window.history.replaceState(window.history.state, "", clean);
                } catch (e) {
                    /* best effort — leaving the param only risks a re-offer, not a break */
                }
            }

            function boostedGet(url) {
                // A full navigation is the reliable hop between tour pages. The runtime
                // is built to survive it: the destination reloads pl_tour.js and resumes
                // from the "?tour=&step=" param (and sessionStorage). We deliberately do
                // NOT drive tour hops through an hx-boost swap — a synthetic boosted <a>
                // click does not reliably trigger htmx's boosted navigation, which strands
                // the tour on the origin page (the popover tears down but nothing loads).
                window.location.assign(url);
            }

            // ── teardown / restore ──────────────────────────────────────────
            function restoreSession() {
                if (!session) return;
                if (session.sidebarPrev !== undefined) {
                    var bodyState = bodyAlpineState();
                    if (bodyState) bodyState.sidebarOpen = session.sidebarPrev;
                }
                if (session.restoreFocus && document.contains(session.restoreFocus)) {
                    try {
                        session.restoreFocus.focus({ preventScroll: true });
                    } catch (e) {
                        /* element unfocusable — harmless */
                    }
                }
            }

            function onSegmentDestroyed() {
                active = null;
                if (ending) {
                    ending = false;
                    restoreSession();
                    session = null;
                }
            }

            function finish(status) {
                if (!active && !session) return;
                ending = true;
                postState(status);
                clearRun();
                stripTourParams();
                if (active && active.driverObj) {
                    active.driverObj.destroy();
                } else {
                    ending = false;
                    restoreSession();
                    session = null;
                }
            }

            // ── driving a segment ───────────────────────────────────────────
            function popoverFor(step) {
                return { title: step.title, description: step.body };
            }

            function driveSegment(globalIndex) {
                if (!current) return;
                var bounds = segmentBounds(globalIndex);
                var driverSteps = [];
                for (var i = bounds.start; i <= bounds.end; i++) {
                    var s = current.steps[i];
                    if (s.target && isVisible(document.querySelector(s.target))) {
                        driverSteps.push({ element: s.target, popover: popoverFor(s) });
                    } else {
                        driverSteps.push({ popover: popoverFor(s) }); // missing target -> centered, never a hang
                    }
                }
                if (driverSteps.length === 0) {
                    finishSilently();
                    return;
                }
                var theme = document.documentElement.getAttribute("data-theme") || window.__plTheme || "dark";
                var localOffset = clampIndex(globalIndex) - bounds.start;
                // Driver.js labels the LAST step of each per-page instance with doneBtnText.
                // A segment that is NOT the final one hops to the next page, so its last
                // step must read "Next", not "Done" — set that at config time so the button
                // is correct the instant it renders (decoratePopover's later override would
                // otherwise leave a race window where a single-step segment briefly shows
                // "Done"). Only the final segment's last step gets the real "Done".
                var isFinalSegment = bounds.end >= current.steps.length - 1;
                var driverObj = window.driver.js.driver({
                    steps: driverSteps,
                    popoverClass: "pl-tour",
                    showProgress: true,
                    showButtons: ["next", "previous", "close"],
                    allowClose: true,
                    animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
                    overlayColor: theme === "light" ? "rgba(29, 30, 30, 0.45)" : "rgba(4, 5, 8, 0.72)",
                    overlayOpacity: 1,
                    nextBtnText: "Next",
                    prevBtnText: "Back",
                    doneBtnText: isFinalSegment ? "Done" : "Next",
                    onHighlighted: function () {
                        decoratePopover();
                    },
                    onNextClick: function () {
                        handleNext();
                    },
                    onPrevClick: function () {
                        handlePrev();
                    },
                    onCloseClick: function () {
                        finish("dismissed"); // the ✕ — an explicit stop
                    },
                    onDestroyStarted: function () {
                        // Esc / overlay click. Providing this hook suppresses Driver's
                        // default teardown, so finish() must call destroy() itself
                        // (which then bypasses this hook — no re-entry).
                        finish("dismissed");
                    },
                    onDestroyed: onSegmentDestroyed,
                });
                active = { driverObj: driverObj, segStart: bounds.start, segEnd: bounds.end };
                driverObj.drive(localOffset);
            }

            function globalIndexNow() {
                if (!active || !active.driverObj) return 0;
                var local = 0;
                try {
                    local = active.driverObj.getActiveIndex() || 0;
                } catch (e) {
                    local = 0;
                }
                return active.segStart + local;
            }

            function decoratePopover() {
                var popover = document.querySelector(".driver-popover");
                if (!popover) return;
                popover.setAttribute("tabindex", "-1");
                try {
                    popover.focus({ preventScroll: true });
                } catch (e) {
                    /* ignore */
                }
                var total = current.steps.length;
                var gi = globalIndexNow();
                var progress = popover.querySelector(".driver-popover-progress-text");
                if (progress) progress.textContent = gi + 1 + " of " + total;
                var nextBtn = popover.querySelector(".driver-popover-next-btn");
                if (nextBtn) nextBtn.textContent = gi >= total - 1 ? "Done" : "Next";
                var prevBtn = popover.querySelector(".driver-popover-prev-btn");
                if (prevBtn) {
                    if (gi <= 0) {
                        prevBtn.style.display = "none";
                    } else {
                        prevBtn.style.display = "";
                        // Driver.js disables the prev button on the first step of each
                        // per-page instance via "driver-popover-btn-disabled"; a segment
                        // that is not the first still has a global Back, so re-enable it.
                        prevBtn.classList.remove("driver-popover-btn-disabled");
                        prevBtn.removeAttribute("disabled");
                    }
                }
                // The Pause control: lift the spotlight without ending the tour. Lives
                // at the left of the footer so it never crowds Back/Next on the right.
                var footer = popover.querySelector(".driver-popover-footer");
                if (footer && !footer.querySelector(".pl-tour-pause-btn")) {
                    var pauseBtn = document.createElement("button");
                    pauseBtn.type = "button";
                    pauseBtn.className = "pl-tour-pause-btn";
                    pauseBtn.textContent = "Pause";
                    pauseBtn.setAttribute("aria-label", "Pause the tour so you can explore on your own");
                    pauseBtn.addEventListener("click", pauseTour);
                    footer.insertBefore(pauseBtn, footer.firstChild);
                }
                // Driver.js occasionally anchors a TARGETLESS (centered) popover
                // off-screen on its first paint after a page load — it measures the
                // viewport before layout settles and strands the popover above the
                // fold (top ≈ -viewportHeight/2). The opening step of every tour is
                // centered, so pin any targetless popover to the true viewport
                // center ourselves; element-anchored steps keep Driver's placement.
                var stepNow = current.steps[gi];
                if (!stepNow || !stepNow.target) {
                    popover.style.position = "fixed";
                    popover.style.left = "50%";
                    popover.style.top = "50%";
                    popover.style.right = "auto";
                    popover.style.bottom = "auto";
                    popover.style.transform = "translate(-50%, -50%)";
                }
            }

            function handleNext() {
                if (!active || !active.driverObj) return;
                var local = active.driverObj.getActiveIndex() || 0;
                var segLen = active.segEnd - active.segStart + 1;
                if (local < segLen - 1) {
                    active.driverObj.moveNext(); // within the segment
                    return;
                }
                var gi = active.segStart + local;
                if (gi >= current.steps.length - 1) {
                    finish("completed");
                } else {
                    hopTo(gi + 1);
                }
            }

            function handlePrev() {
                if (!active || !active.driverObj) return;
                var local = active.driverObj.getActiveIndex() || 0;
                if (local > 0) {
                    active.driverObj.movePrevious(); // within the segment
                    return;
                }
                var gi = active.segStart + local;
                if (gi <= 0) return; // already at the very first step
                hopTo(gi - 1);
            }

            // ── hopping between segments ────────────────────────────────────
            function hopTo(targetIndex) {
                var bounds = segmentBounds(targetIndex);
                var leader = current.steps[bounds.start];
                ending = false;
                var d = active && active.driverObj;
                // A hop NAVIGATES when the target segment lives on another page: a forward
                // step carries a resolved `navigate` href; a BACKWARD hop into the entry-page
                // segment has no `navigate` (its page was reached by the initial load, not an
                // action) yet still sits on a different pathname. Both full-navigate carrying
                // `?tour=&step=` so the destination re-hydrates and resumes. Only a truly
                // same-page leader (a tab flip / click) stays put.
                var destPath = leader.navigate || leader.page_path;
                var needsNav =
                    !!leader.navigate ||
                    (!!leader.page_path && leader.page_path !== window.location.pathname);
                if (needsNav && destPath) {
                    saveRun(targetIndex);
                    if (d) d.destroy(); // silent (bypasses onDestroyStarted); onSegmentDestroyed keeps session
                    showAdvancing();
                    boostedGet(withTourParams(destPath, current.key, targetIndex));
                } else {
                    saveRun(targetIndex);
                    if (d) d.destroy();
                    applyTabOrClick(leader);
                    requestAnimationFrame(function () {
                        waitFor(current.steps[targetIndex]).then(function () {
                            driveSegment(targetIndex);
                        });
                    });
                }
            }

            function finishSilently() {
                // No drivable steps at all (a resolver stripped everything): end
                // without recording, restore any sidebar/focus, clear the run.
                ending = false;
                clearRun();
                stripTourParams();
                restoreSession();
                session = null;
                active = null;
                hideAdvancing();
            }

            // ── resume / start ──────────────────────────────────────────────
            function resumeAt(globalIndex) {
                if (!current) return;
                var idx = clampIndex(globalIndex);
                var bounds = segmentBounds(idx);
                var leader = current.steps[bounds.start];
                // Location assertion: a 302 (e.g. a locked area) must not leave us
                // spotlighting the wrong page. page_path is the pathname the step
                // belongs to; if we are not there, stop cleanly.
                if (leader.page_path && window.location.pathname.indexOf(leader.page_path) !== 0) {
                    clearRun();
                    session = null;
                    hideAdvancing();
                    return;
                }
                var navigated = !!leader.navigate;
                if (!leader.navigate) applyTabOrClick(leader); // same-page leader on a fresh load
                if (navigated) showAdvancing();
                requestAnimationFrame(function () {
                    waitFor(current.steps[idx]).then(function () {
                        hideAdvancing();
                        driveSegment(idx);
                    });
                });
            }

            function beginSession(auto) {
                var sidebarPrev; // undefined = nothing to restore
                if (current.opens_sidebar) {
                    var bodyState = bodyAlpineState();
                    if (bodyState) {
                        sidebarPrev = bodyState.sidebarOpen;
                        bodyState.sidebarOpen = true;
                    }
                }
                session = { sidebarPrev: sidebarPrev, restoreFocus: document.activeElement };
                return sidebarPrev;
            }

            function start(auto) {
                if (!current || active) return;
                if (current.steps.length === 0 || (auto && current.steps.length < 2)) return;
                var sidebarPrev = beginSession(auto);
                saveRun(0);
                loadAssets().then(
                    function () {
                        requestAnimationFrame(function () {
                            resumeAt(0);
                        });
                    },
                    function () {
                        if (sidebarPrev !== undefined) {
                            var bodyState = bodyAlpineState();
                            if (bodyState) bodyState.sidebarOpen = sidebarPrev;
                        }
                        session = null;
                        clearRun();
                    }
                );
            }

            function resumeFromContext() {
                // 1. Authoritative seed: an autostart payload (?tour= present) —
                //    resume at the server-clamped resume_step on THIS page.
                if (current.autostart) {
                    var seed = clampIndex(current.resume_step || 0);
                    if (current.steps.length === 0) return;
                    beginSession(false);
                    saveRun(seed);
                    loadAssets().then(
                        function () {
                            resumeAt(seed);
                        },
                        function () {
                            session = null;
                            clearRun();
                        }
                    );
                    return;
                }
                // 2. A running tour continued onto this page via sessionStorage
                //    (covers an accidental same-page reload without the URL param).
                var run = readRun();
                if (run && run.key === current.key) {
                    var idx = clampIndex(run.index);
                    var bounds = segmentBounds(idx);
                    var leader = current.steps[bounds.start];
                    if (leader.page_path && window.location.pathname.indexOf(leader.page_path) !== 0) {
                        clearRun(); // the user navigated away on their own — end cleanly
                        return;
                    }
                    session = { sidebarPrev: run.sidebarPrev, restoreFocus: document.activeElement };
                    loadAssets().then(
                        function () {
                            resumeAt(idx);
                        },
                        function () {
                            session = null;
                        }
                    );
                }
            }

            // ── pause / resume pill ─────────────────────────────────────────
            // A pause LIFTS the spotlight without ending the tour: it writes a
            // "paused" run to sessionStorage (carrying the resume path, the state
            // URL and the csrf so the pill still works on pages that emit no tour
            // payload) and shows a persistent floating pill. Because the driver
            // overlay is torn down, teardownForHtmx stops clearing the run, so the
            // paused tour survives the presenter wandering to other pages. Resume
            // reuses the ONE battle-tested resume path: a "?tour=&step=" navigation
            // back to the paused step's page.
            function resumePillEl() {
                return document.getElementById("pl-tour-resume");
            }

            function showResumePill() {
                var el = resumePillEl();
                if (el) el.hidden = false;
            }

            function hideResumePill() {
                var el = resumePillEl();
                if (el) el.hidden = true;
            }

            function pauseTour() {
                if (!active || !active.driverObj) return;
                var gi = globalIndexNow();
                var path = window.location.pathname; // the step's own page
                try {
                    window.sessionStorage.setItem(
                        RUN_KEY,
                        JSON.stringify({
                            key: current.key,
                            index: gi,
                            total: current.steps.length,
                            sidebarPrev: session ? session.sidebarPrev : undefined,
                            paused: true,
                            resumePath: path,
                            stateUrl: current.state_url,
                            csrf: csrf,
                        })
                    );
                } catch (e) {
                    /* no storage — pausing is best-effort; still tear the overlay down */
                }
                // Silent teardown that RESTORES chrome (sidebar/focus) so the presenter
                // can roam freely, records nothing, and keeps the run in storage.
                ending = true;
                stripTourParams();
                active.driverObj.destroy();
                showResumePill();
            }

            function resumePausedTour() {
                var run = readRun();
                if (!run || !run.paused) {
                    hideResumePill();
                    return;
                }
                hideResumePill();
                clearRun(); // the destination re-seeds from the ?tour=&step= param
                boostedGet(withTourParams(run.resumePath || "/", run.key, run.index));
            }

            function endPausedTour() {
                var run = readRun();
                hideResumePill();
                clearRun();
                if (run && run.stateUrl) {
                    // Record the dismissal from wherever the presenter ended it, using
                    // the state URL + csrf stashed at pause time.
                    fetch(run.stateUrl, {
                        method: "POST",
                        headers: {
                            "X-CSRFToken": run.csrf || "",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        body: "status=dismissed",
                    }).catch(function () {
                        /* self-healing: a lost dismiss just re-offers next visit */
                    });
                }
            }

            function bindResumePill() {
                var el = resumePillEl();
                if (!el || el.hasAttribute("data-pl-init")) return;
                el.setAttribute("data-pl-init", "");
                var resumeBtn = el.querySelector("[data-tour-resume]");
                var endBtn = el.querySelector("[data-tour-resume-end]");
                if (resumeBtn) resumeBtn.addEventListener("click", resumePausedTour);
                if (endBtn) endBtn.addEventListener("click", endPausedTour);
            }

            // ── offer card ──────────────────────────────────────────────────
            function bindOffer() {
                var offer = document.querySelector("[data-pl-tour-offer]");
                if (!offer || offer.hasAttribute("data-pl-tour-init")) return;
                offer.setAttribute("data-pl-tour-init", "");
                var accept = offer.querySelector("[data-tour-accept]");
                var decline = offer.querySelector("[data-tour-decline]");
                if (accept) {
                    accept.addEventListener("click", function () {
                        offer.remove();
                        start(true);
                    });
                }
                if (decline) {
                    decline.addEventListener("click", function () {
                        postState("dismissed");
                        offer.remove();
                    });
                }
            }

            // ── htmx teardown split ─────────────────────────────────────────
            function teardownForHtmx() {
                if (!active || !active.driverObj) return;
                // Tour hops are full navigations, not htmx swaps, so this only fires when the
                // user themselves boosts away mid-tour (a link click) — abandon the tour.
                ending = false;
                clearRun();
                active.driverObj.destroy();
                session = null;
            }

            function init() {
                readPayload();
                if (!htmxBound) {
                    htmxBound = true;
                    ["htmx:beforeHistorySave", "htmx:beforeSwap"].forEach(function (name) {
                        document.body.addEventListener(name, teardownForHtmx);
                    });
                }
                bindResumePill(); // the pill markup is present on every member page
                var paused = readRun();
                if (paused && paused.paused) {
                    // A paused tour is in effect on this tab: show the pill and do NOT
                    // auto-resume or re-offer until the presenter clicks Resume.
                    showResumePill();
                    return;
                }
                if (!current) return;
                bindOffer();
                if (active) return; // already running (e.g. a mid-tab-hop re-init)
                resumeFromContext();
            }

            return { init: init, start: start };
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
