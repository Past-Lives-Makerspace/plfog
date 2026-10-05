/* The List / Calendar / Bookings panes on the Orientations and Reservations pages (#502, #626).
 *
 * Alpine component `plListCalendar(initialPane)`. The server picks the opening pane from
 * ?view=<pane> and builds that pane only; every other pane that costs queries holds a
 * placeholder carrying data-lazy-pane="<pane>" and data-pane-src="<partial url>", which this
 * component swaps for the pane the first time the member opens it, so the List view costs no
 * calendar or bookings queries. Switching keeps ?view in the address bar (replaceState), so
 * a reload or a shared link opens the same pane.
 *
 * A hash naming an element in the List pane (an orientation card, #orientation-type-5)
 * opens the List pane, so a calendar entry's link always lands on its card.
 *
 * Loaded once, deferred, from hub/base.html's <head> with the other Alpine components
 * (FRONTEND.md, Scripts under hx-boost).
 */
(function () {
    "use strict";

    function registerComponent() {
        window.Alpine.data("plListCalendar", (initialPane) => ({
            pane: initialPane,

            init() {
                let id = "";
                try {
                    id = decodeURIComponent(window.location.hash.slice(1));
                } catch (e) {
                    // A malformed hash (#%) names no card: open the pane the server chose.
                    return;
                }
                const target = id ? document.getElementById(id) : null;
                if (target && this.$refs.listPane && this.$refs.listPane.contains(target)) {
                    this.pane = "list";
                }
            },

            setPane(pane) {
                this.pane = pane;
                const url = new URL(window.location.href);
                if (pane === "list") {
                    url.searchParams.delete("view");
                } else {
                    url.searchParams.set("view", pane);
                }
                url.hash = "";
                window.history.replaceState(window.history.state, "", url.toString());
                const lazy = this.$root.querySelector('[data-lazy-pane="' + pane + '"]');
                if (lazy && !lazy.plRequested) {
                    lazy.plRequested = true;
                    window.htmx.ajax("GET", lazy.dataset.paneSrc, { target: lazy, swap: "outerHTML" });
                }
            },
        }));
    }

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener("alpine:init", registerComponent);
    }
})();
