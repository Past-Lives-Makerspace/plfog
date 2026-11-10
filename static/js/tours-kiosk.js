/* The front desk kiosk's reset (#950): every kiosk page goes back to the kiosk home after
   data-kiosk-idle seconds without input, and a finished screen (welcome, booked) after
   data-kiosk-done seconds whatever happens. 0 or absent turns either off. location.replace
   keeps the visitor's pages out of the back button's history. A page restored from the
   back/forward cache is never shown: it goes home too. Loaded deferred from
   tours/kiosk/base.html; kiosk pages are full page loads (hx-boost off), so this runs once per page.

   The keyboard alone drives every screen (#1155, cairn's mouse went missing): with no mouse
   plugged in ("any-pointer: fine" false, rechecked when one comes or goes) the
   [data-kiosk-keys] badge says so and names the keys. Escape goes home; arrow keys move between
   the items of a group (ARROW_GROUPS); the day strip is one tab stop, so Tab reaches the times;
   Schedule a Tour focuses the picked day's first time, and its form the first field to fill. */
(function () {
    "use strict";

    // The kiosk's own groups, and the shared picker's day strip and times (tours/public/_picker.html).
    var ARROW_GROUPS = "[data-kiosk-arrows], .pl-tourpub__days, .pl-tourpub__times";
    var ARROW_STEP = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };

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

    function onKey(root, event) {
        if (event.altKey || event.ctrlKey || event.metaKey) {
            return;
        }
        if (event.key === "Escape") {
            home(root);
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
        var next = items[at + step];
        if (at < 0 || !next) {
            return;
        }
        event.preventDefault();
        next.focus();
    }

    // Where the keyboard starts on Schedule a Tour: the form's first field to fill, else the
    // picked day's first time, else the picked day. Home, check in and the rest autofocus in markup.
    function placeFocus(root) {
        var form = root.querySelector("[data-tour-form]");
        var first = form
            ? form.querySelector('[aria-invalid="true"]') || form.querySelector('input[name="name"]')
            : root.querySelector(".pl-tourpub__time") || root.querySelector('.pl-tourpub__day[aria-current="date"]');
        if (first) {
            first.focus();
        }
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
