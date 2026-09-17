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

    function isLoginUrl(url) {
        return !!url && url.indexOf(LOGIN_PATH) !== -1;
    }

    /* A boosted request that lands on the login page does a full page load instead of
     * a swap: the login page has its own layout and CSS and would render bare. */
    document.addEventListener("htmx:beforeSwap", function (event) {
        var url = event.detail.xhr && event.detail.xhr.responseURL;
        if (isLoginUrl(url)) {
            event.detail.shouldSwap = false;
            window.location.href = url;
        }
    });

    /* htmx 2 does not swap a 4xx. It fires htmx:responseError and leaves the page exactly
     * where it was, so a boosted click into a class this viewer may not open is a dead
     * click: no branded 404, no "Viewing as" switcher, no feedback at all. Force the
     * browser to navigate instead, so the real error page renders with the hub chrome
     * around it and a previewing admin has a way back out. Issue #399.
     *
     * Only boosted navigations: an hx-get that fills a modal or a panel gets its 4xx
     * handled where it was fired, and yanking the whole document out from under it would
     * be worse than the dead click. The login rule above already owns a 4xx served at the
     * login path, and this one steps aside for it so the two never fight over
     * window.location.
     *
     * No unit test reaches this. The Django test client is not htmx, so every spec that
     * asserts the 404 passes whether this rule exists or not — it is the browser's
     * behaviour being corrected here, not the server's. */
    document.addEventListener("htmx:beforeSwap", function (event) {
        var xhr = event.detail.xhr;
        if (!event.detail.boosted || !xhr || xhr.status < 400 || xhr.status > 499) return;
        var url = xhr.responseURL || (event.detail.pathInfo && event.detail.pathInfo.requestPath);
        if (!url || isLoginUrl(url)) return;
        event.detail.shouldSwap = false;
        window.location.href = url;
    });

    document.addEventListener("alpine:init", function () {
        window.Alpine.store("theme", { light: window.__plTheme === "light" });
    });
})();
