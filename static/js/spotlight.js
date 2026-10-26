/* The Spotlight's version card (#709, static since #935): Details and the version pill open the panel.
 *
 * An Alpine store, registered from hub/base.html's head before Alpine: expand() opens Details
 * (components/modal.html "spotlight-expanded"), and showChange(anchor) opens it on the changelog,
 * at the entry with that id ("" for the newest page).
 */
(function () {
    function registerStore() {
        window.Alpine.store("spotlight", {
            expand() {
                window.dispatchEvent(new CustomEvent("open-modal", { detail: "spotlight-expanded" }));
            },
            showChange(anchor) {
                this.expand();
                window.dispatchEvent(new CustomEvent("spotlight-changelog", { detail: anchor }));
            },
        });
    }

    if (window.Alpine) {
        registerStore();
    } else {
        document.addEventListener("alpine:init", registerStore);
    }
})();
