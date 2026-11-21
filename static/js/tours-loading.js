/* The tours site's loading indicator (#1277): on every page of tours.pastlives.space and every
   kiosk screen, a link that loads a page, a form's submit or an htmx request shows a gold bar
   across the very top of the viewport and dims the page under a layer that takes every tap, in
   the same event. Until the next page arrives (or the htmx response does) every further click
   and submit is swallowed, so a second tap sends nothing; Enter on a focused control is a click
   or a submit too. Modifier clicks, new tabs, downloads, mailto and tel links and same page
   anchors are left alone, and a submit or click another listener cancelled (a failed native
   validation never submits at all) shows nothing: the decision is made on window, after every
   document listener. An htmx request ends with its response, failed or not; a page restored
   from the back/forward cache is cleared. A background poll inside [data-quiet-poll] never
   shows it, as with the hub's own bar (components/loading_bar.html), which tours-public.css
   hides on this site.

   Loaded deferred from tours/public/base.html (the kiosk base extends it). head-support keeps
   it across a boosted arrival, so it runs once per document: guarded on window, delegated, and
   the indicator lives on <html>, outside the body a boosted swap replaces. */
(function () {
    "use strict";

    if (window.plToursLoadingBound) {
        return;
    }
    window.plToursLoadingBound = true;

    var html = document.documentElement;
    var indicator = null;
    var leaving = false;
    var requests = new Set();
    var doneTimer = null;

    function show(state) {
        if (!indicator || !indicator.isConnected) {
            indicator = document.createElement("div");
            indicator.className = "pl-tours-loading";
            indicator.setAttribute("data-tours-loading-indicator", "");
            indicator.setAttribute("aria-hidden", "true");
            var bar = document.createElement("span");
            bar.className = "pl-tours-loading__bar";
            indicator.appendChild(bar);
            html.appendChild(indicator);
        }
        window.clearTimeout(doneTimer);
        html.setAttribute("data-tours-loading", state);
    }

    function busy() {
        return leaving || requests.size > 0;
    }

    function start() {
        if (!busy()) {
            show("on");
            if (document.body) {
                document.body.setAttribute("aria-busy", "true");
            }
        }
    }

    // The bar runs to the end and fades; the page takes taps again at once.
    function finish() {
        if (busy()) {
            return;
        }
        if (document.body) {
            document.body.removeAttribute("aria-busy");
        }
        if (html.getAttribute("data-tours-loading") !== "on") {
            return;
        }
        show("done");
        doneTimer = window.setTimeout(function () {
            html.removeAttribute("data-tours-loading");
        }, 400);
    }

    function clear() {
        leaving = false;
        requests.clear();
        window.clearTimeout(doneTimer);
        html.removeAttribute("data-tours-loading");
        if (document.body) {
            document.body.removeAttribute("aria-busy");
        }
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

    function quiet(event) {
        var elt = event.detail && event.detail.elt;
        return Boolean(elt && elt.closest && elt.closest("[data-quiet-poll]"));
    }

    // Capture on window runs before anything else: once busy, nothing gets a click or a submit.
    ["click", "submit"].forEach(function (name) {
        window.addEventListener(name, function (event) {
            if (busy()) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }, true);
    });

    // Bubble on window runs after every document listener, so a cancelled event is seen as one.
    // A boosted link or form is cancelled by htmx and shows through htmx:beforeRequest instead.
    window.addEventListener("click", function (event) {
        var link = event.target.closest && event.target.closest("a[href]");
        if (event.defaultPrevented || busy() || !link || !leavesForLink(link, event)) {
            return;
        }
        start();
        leaving = true;
    });

    window.addEventListener("submit", function (event) {
        var form = event.target;
        if (event.defaultPrevented || busy() || (form.target && form.target !== "_self")) {
            return;
        }
        start();
        leaving = true;
    });

    document.addEventListener("htmx:beforeRequest", function (event) {
        var xhr = event.detail && event.detail.xhr;
        if (quiet(event) || !xhr) {
            return;
        }
        start();
        requests.add(xhr);
    });

    // afterRequest follows every request, answered, failed, timed out or aborted; the error
    // events beside it are a second line, so each request counts once.
    ["htmx:afterRequest", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
        document.addEventListener(name, function (event) {
            if (requests.delete(event.detail && event.detail.xhr)) {
                finish();
            }
        });
    });

    window.addEventListener("pageshow", function (event) {
        if (event.persisted) {
            clear();
        }
    });
})();
