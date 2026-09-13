/* One-time wiring for the member hub under hx-boost.
 *
 * hub/base.html loads htmx, Alpine and this file once, deferred, from <head>. hx-boost
 * swaps <body>, and the head-support extension keeps every <head> tag the next page
 * also carries, so a boosted navigation never runs any of them again: one htmx (one
 * window.onpopstate, one history cache), one Alpine (one registry of components), and
 * the listeners below bound once on document. Issue #378.
 *
 * Nothing here needs htmx or Alpine at parse time, and nothing touches document.body:
 * a head script runs before there is one, and htmx events bubble to document anyway.
 */
(function () {
    "use strict";

    var LOGIN_PATH = "/accounts/login/";

    /* Django masks the CSRF token per render, so head-support replaces the meta tag on
     * every boosted arrival; read it per request and the header is always current. */
    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") || "" : "";
    }

    document.addEventListener("htmx:configRequest", function (event) {
        event.detail.headers["X-CSRFToken"] = csrfToken();
    });

    /* A boosted request that lands on the login page does a full page load instead of
     * a swap: the login page has its own layout and CSS and would render bare. */
    document.addEventListener("htmx:beforeSwap", function (event) {
        var url = event.detail.xhr && event.detail.xhr.responseURL;
        if (url && url.indexOf(LOGIN_PATH) !== -1) {
            event.detail.shouldSwap = false;
            window.location.href = url;
        }
    });

    document.addEventListener("alpine:init", function () {
        window.Alpine.store("theme", { light: window.__plTheme === "light" });
    });
})();
