/* The Spotlight's state in the browser (#709): minimized or not, and what the member has seen.
 *
 * Both live in localStorage, like the sidebar's open state, so they survive a reload and a new
 * tab and need no columns. The minimized choice is also mirrored on <html data-spotlight-min>,
 * which the stylesheet reads to show Standard or Minimized: an early script in hub/base.html's
 * head sets it before the first paint, so a minimized Spotlight never flashes open.
 *
 * The yellow dot: each page hands the store the Spotlight's signature (the open poll, the next
 * meeting date and the text-changed stamp, hub.spotlight.Spotlight.seen_signature). The dot
 * shows on Minimized while that differs from the last one the member opened the Spotlight on;
 * showing Standard or opening Expanded records it as seen.
 */
(function () {
    var MINIMIZED_KEY = "plSpotlightMinimized";
    var SEEN_KEY = "plSpotlightSeen";

    function read(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (e) {
            return null;
        }
    }

    function write(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (e) {
            /* Private mode or storage off: the Spotlight still works, it just forgets. */
        }
    }

    function mirror(minimized) {
        if (minimized) document.documentElement.dataset.spotlightMin = "1";
        else delete document.documentElement.dataset.spotlightMin;
    }

    function registerStore() {
        window.Alpine.store("spotlight", {
            minimized: read(MINIMIZED_KEY) === "1",
            signature: "",
            seen: read(SEEN_KEY) || "",
            get unseen() {
                return this.signature.replace(/\|/g, "") !== "" && this.signature !== this.seen;
            },
            arrive(signature) {
                this.signature = signature;
                mirror(this.minimized);
                if (!this.minimized) this.markSeen();
            },
            markSeen() {
                if (!this.signature) return;
                this.seen = this.signature;
                write(SEEN_KEY, this.signature);
            },
            minimize() {
                this.minimized = true;
                write(MINIMIZED_KEY, "1");
                mirror(true);
            },
            open() {
                this.minimized = false;
                write(MINIMIZED_KEY, "0");
                mirror(false);
                this.markSeen();
            },
            expand() {
                this.markSeen();
                window.dispatchEvent(new CustomEvent("open-modal", { detail: "spotlight-expanded" }));
            },
        });
    }

    if (window.Alpine) {
        registerStore();
    } else {
        document.addEventListener("alpine:init", registerStore);
    }
})();
