/* The front desk kiosk's reset (#950): every kiosk page goes back to the kiosk home after
   data-kiosk-idle seconds without input, and a finished screen (welcome, booked) after
   data-kiosk-done seconds whatever happens. 0 or absent turns either off. location.replace
   keeps the visitor's pages out of the back button's history. A page restored from the
   back/forward cache is never shown: it goes home too. Loaded deferred from
   tours/kiosk/base.html; kiosk pages are full page loads (hx-boost off), so this runs once per page. */
(function () {
    "use strict";

    function home(root) {
        window.location.replace(root.getAttribute("data-kiosk-home") || "/kiosk/");
    }

    function start() {
        var root = document.querySelector("[data-kiosk]");
        if (!root || root.hasAttribute("data-kiosk-started")) {
            return;
        }
        root.setAttribute("data-kiosk-started", "");
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
