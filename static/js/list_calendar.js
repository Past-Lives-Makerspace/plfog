/* The List / Calendar pair on the Orientations and Reservations pages (#502).
 *
 * Alpine component `plListCalendar(initialPane)`. The server picks the opening pane from
 * ?view=calendar and builds the calendar only then; otherwise the Calendar pane holds a
 * placeholder (x-ref="calendarLazy", data-calendar-src) that this component swaps for the
 * whole calendar shell the first time the member opens the pane, so the List view costs
 * no calendar queries. Switching keeps ?view in the address bar (replaceState), so a
 * reload or a shared link opens the same pane.
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
                if (pane === "calendar") {
                    url.searchParams.set("view", "calendar");
                } else {
                    url.searchParams.delete("view");
                }
                url.hash = "";
                window.history.replaceState(window.history.state, "", url.toString());
                const lazy = this.$refs.calendarLazy;
                if (pane === "calendar" && lazy && !lazy.plRequested) {
                    lazy.plRequested = true;
                    window.htmx.ajax("GET", lazy.dataset.calendarSrc, { target: lazy, swap: "outerHTML" });
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
