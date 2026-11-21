/* The front desk kiosk's reset (#950): every kiosk page goes back to the kiosk home after
   data-kiosk-idle seconds without input, and a finished screen (welcome, booked) after
   data-kiosk-done seconds whatever happens. 0 or absent turns either off. location.replace
   keeps the visitor's pages out of the back button's history. A page restored from the
   back/forward cache is never shown: it goes home too. Loaded deferred from
   tours/kiosk/base.html; kiosk pages are full page loads (hx-boost off), so this runs once per page.

   The keyboard alone drives every screen (#1155, cairn's mouse went missing): with no mouse
   plugged in ("any-pointer: fine" false, rechecked when one comes or goes) the
   [data-kiosk-keys] badge says so and names the keys. Escape goes home; arrow keys move between
   the items of a group (ARROW_GROUPS; Up and Down by row in a grid); Enter flips a switch rather
   than submitting its form; the day strip is one tab stop, so Tab reaches the times; Schedule a
   Tour focuses the picked day's first time, its form the first field to fill, and the pick list
   its first name.

   Every tap shows it went through (#1273, people tapped again): a click or Enter on a link, or a
   form's submit, inside [data-kiosk] marks the pressed control aria-busy (the spinner and
   "Loading...", or busy_submit.html's own label on its forms) and disables every other control on
   the page (links leave the tab order and stop taking the pointer), and until the next page
   arrives every further click or submit is swallowed, so a second tap sends nothing. Modifier
   clicks, mailto links and same page anchors are left alone. Inside the form being submitted
   nothing is disabled (a disabled field or submitter would drop out of what it posts); the
   swallowing and the pointer rule cover it. A page restored from the back/forward cache gets every
   control back before it goes home. */
(function () {
    "use strict";

    // The kiosk's own groups, and the shared picker's day strip and times (tours/public/_picker.html).
    var ARROW_GROUPS = "[data-kiosk-arrows], .pl-tourpub__days, .pl-tourpub__times";
    var ARROW_STEP = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
    var ARROW_ROWS = { ArrowUp: true, ArrowDown: true };

    function home(root) {
        window.location.replace(root.getAttribute("data-kiosk-home") || "/kiosk/");
    }

    function showKeys(root) {
        var badge = document.querySelector("[data-kiosk-keys]");
        if (!badge || !window.matchMedia) {
            return;
        }
        var mouse = window.matchMedia("(any-pointer: fine)");
        var apply = function () {
            badge.hidden = mouse.matches;
            root.toggleAttribute("data-kiosk-keyboard", !mouse.matches);
        };
        apply();
        mouse.addEventListener("change", apply);
    }

    function groupItems(group) {
        return Array.prototype.slice.call(group.querySelectorAll("a[href], button:not([disabled])"));
    }

    // The day strip is one tab stop, the picked day; arrow keys walk the rest of it.
    function rove(root) {
        root.querySelectorAll(".pl-tourpub__days").forEach(function (strip) {
            var days = groupItems(strip);
            var on = strip.querySelector('[aria-current="date"]') || days[0];
            days.forEach(function (day) {
                day.setAttribute("tabindex", day === on ? "0" : "-1");
            });
        });
    }

    // The nearest item in the next row up or down, closest to the same column; none in one row.
    function nextRow(items, from, step) {
        var at = from.getBoundingClientRect();
        var best = null;
        var bestScore = Infinity;
        items.forEach(function (item) {
            var box = item.getBoundingClientRect();
            var down = (box.top - at.top) * step;
            if (down <= 1) {
                return;
            }
            var score = down * 1000 + Math.abs(box.left - at.left);
            if (score < bestScore) {
                bestScore = score;
                best = item;
            }
        });
        return best;
    }

    function onKey(root, event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing) {
            return;
        }
        if (event.key === "Escape") {
            home(root);
            return;
        }
        // Enter on the account switch would submit the booking; on the kiosk it flips the switch.
        if (event.key === "Enter" && event.target.matches && event.target.matches('input[type="checkbox"]')) {
            event.preventDefault();
            event.target.click();
            return;
        }
        var step = ARROW_STEP[event.key];
        var target = event.target;
        var group = step && target.closest && target.closest(ARROW_GROUPS);
        if (!group || !root.contains(group)) {
            return;
        }
        var items = groupItems(group);
        var at = items.indexOf(target);
        var next = ARROW_ROWS[event.key] ? nextRow(items, target, step) : items[at + step];
        if (at < 0 || !next) {
            return;
        }
        event.preventDefault();
        next.focus();
    }

    // Where the keyboard starts on Schedule a Tour (the form's first field to fill: the Guide
    // dropdown where guides share the time (#1255), else the name; else the picked day's first
    // time, else the picked day) and on the pick list (its first name). Home, check in and the
    // rest autofocus in markup.
    function placeFocus(root) {
        var form = root.querySelector("[data-tour-form]");
        var first = form
            ? form.querySelector('[aria-invalid="true"]') ||
              form.querySelector("[data-tour-guide]") ||
              form.querySelector('input[name="name"]')
            : root.querySelector(".pl-tourpub__time") ||
              root.querySelector('.pl-tourpub__day[aria-current="date"]') ||
              root.querySelector("[data-kiosk-pick] [data-kiosk-name]");
        if (first) {
            first.focus();
        }
    }

    // The busy state (#1273): what it changed, so a page restored from the cache can undo it.
    var BUSY_LABEL = "Loading...";
    var CONTROLS = "a[href], button, input, select, textarea";
    var busyState = null;

    function markBusy(pressed, form) {
        var changed = [];
        if (!pressed.hasAttribute("aria-busy")) {
            pressed.setAttribute("aria-busy", "true");
            // busy_submit.html already swapped its own buttons' label; everything else gets one.
            if (!(form && form.hasAttribute("data-pl-busy-submit") && pressed.hasAttribute("data-pl-busy"))) {
                var label = document.createElement("span");
                label.className = "pl-kiosk__busy";
                label.setAttribute("data-kiosk-busy-label", "");
                label.textContent = BUSY_LABEL;
                pressed.appendChild(label);
            }
        }
        document.querySelectorAll(CONTROLS).forEach(function (control) {
            if (control === pressed) {
                return;
            }
            var entry = { el: control, tabindex: control.getAttribute("tabindex"), disabled: null };
            if (control.matches("a[href]") || (form && form.contains(control))) {
                control.setAttribute("tabindex", "-1");
                control.setAttribute("aria-disabled", "true");
            } else {
                entry.disabled = control.disabled;
                control.disabled = true;
            }
            changed.push(entry);
        });
        document.body.setAttribute("data-kiosk-busy", "");
        busyState = { pressed: pressed, changed: changed };
    }

    function unmarkBusy() {
        if (!busyState) {
            return;
        }
        var pressed = busyState.pressed;
        if (!pressed.hasAttribute("data-pl-busy")) {
            pressed.removeAttribute("aria-busy");
        }
        pressed.querySelectorAll("[data-kiosk-busy-label]").forEach(function (label) {
            label.remove();
        });
        busyState.changed.forEach(function (entry) {
            if (entry.tabindex === null) {
                entry.el.removeAttribute("tabindex");
            } else {
                entry.el.setAttribute("tabindex", entry.tabindex);
            }
            entry.el.removeAttribute("aria-disabled");
            if (entry.disabled !== null) {
                entry.el.disabled = entry.disabled;
            }
        });
        document.body.removeAttribute("data-kiosk-busy");
        busyState = null;
    }

    // A link that loads another page here: a plain left click or Enter, this tab, http(s), not this page's own anchor.
    function leavesForLink(link, event) {
        if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
            return false;
        }
        var target = link.getAttribute("target");
        if ((target && target !== "_self") || link.hasAttribute("download")) {
            return false;
        }
        if (link.protocol !== "http:" && link.protocol !== "https:") {
            return false;
        }
        // An empty or bare fragment ("#", "#top") never loads a page, so it must never stick busy.
        if ((link.getAttribute("href") || "").charAt(0) === "#") {
            return false;
        }
        var here = window.location;
        return !(link.hash && link.pathname === here.pathname && link.search === here.search);
    }

    function watchBusy(root) {
        // Capture runs first: once busy, nothing else gets a click or a submit.
        ["click", "submit"].forEach(function (name) {
            document.addEventListener(name, function (event) {
                if (busyState) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            }, true);
        });
        document.addEventListener("click", function (event) {
            if (event.defaultPrevented || busyState) {
                return;
            }
            var link = event.target.closest && event.target.closest("a[href]");
            // The brand bar's home link sits outside the root but loads a page all the same.
            if (link && (root.contains(link) || link.closest(".pl-kiosk-bar")) && leavesForLink(link, event)) {
                markBusy(link, null);
            }
        });
        document.addEventListener("submit", function (event) {
            var form = event.target;
            if (event.defaultPrevented || busyState || !root.contains(form) || (form.target && form.target !== "_self")) {
                return;
            }
            markBusy(event.submitter || form, form);
        });
    }

    function start() {
        var root = document.querySelector("[data-kiosk]");
        if (!root || root.hasAttribute("data-kiosk-started")) {
            return;
        }
        root.setAttribute("data-kiosk-started", "");
        showKeys(root);
        rove(root);
        placeFocus(root);
        watchBusy(root);
        document.addEventListener("keydown", function (event) { onKey(root, event); });
        var idle = Number(root.getAttribute("data-kiosk-idle") || 0) * 1000;
        var done = Number(root.getAttribute("data-kiosk-done") || 0) * 1000;
        if (done > 0) {
            window.setTimeout(function () { home(root); }, done);
        }
        if (idle > 0) {
            var timer = window.setTimeout(function () { home(root); }, idle);
            var restart = function () {
                window.clearTimeout(timer);
                timer = window.setTimeout(function () { home(root); }, idle);
            };
            ["mousemove", "mousedown", "keydown", "input", "wheel", "touchstart"].forEach(function (name) {
                document.addEventListener(name, restart, { passive: true });
            });
        }
        window.addEventListener("pageshow", function (event) {
            if (event.persisted) {
                unmarkBusy();
                home(root);
            }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
