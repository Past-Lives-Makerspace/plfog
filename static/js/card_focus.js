/* Catalog card focal point tool.
 *
 * Alpine component `cardFocus`, the sibling of `heroPlacement` (hero_placement.js)
 * with the network half removed: two range sliders drive the CSS object-position of
 * the live card frames, and every change is written as {"x": int, "y": int} into the
 * hidden `card_focus` input so the value rides the composer's own form POST. An
 * empty input means "follow the banner"; `matchBanner()` writes that and snaps the
 * sliders back to the banner's derived position, which the server passes in as
 * `banner`.
 *
 * On a class that has no saved hero yet, the hero field's local preview (a data URL
 * the create-mode fallback writes into #hero-preview) is mirrored into the frames, so
 * the instructor sees the card before the first save.
 */
(function () {
    "use strict";

    function parsePosition(value) {
        const parts = String(value || "50% 50%").trim().split(/\s+/);
        return {
            x: Math.round(parseFloat(parts[0])) || 50,
            y: Math.round(parseFloat(parts[1])) || 50,
        };
    }

    const registerComponent = () => {
        Alpine.data("cardFocus", (config) => ({
            posX: 50,
            posY: 50,
            bannerX: 50,
            bannerY: 50,
            following: true,
            localSrc: "",

            init() {
                const banner = parsePosition(config.banner);
                this.bannerX = banner.x;
                this.bannerY = banner.y;
                const initial = parsePosition(config.initial);
                this.posX = initial.x;
                this.posY = initial.y;
                this.following = !this.input() || !this.input().value;
                this.watchHeroPreview();
            },

            input() {
                return this.$root.querySelector("[data-card-focus-input]");
            },

            get objectPosition() {
                return this.posX + "% " + this.posY + "%";
            },

            /* Slider moves write the override; the composer root mirrors it onto step 5. */
            update() {
                this.following = false;
                const field = this.input();
                if (field) {
                    field.value = JSON.stringify({ x: Math.round(this.posX), y: Math.round(this.posY) });
                }
                this.announce();
            },

            matchBanner() {
                this.posX = this.bannerX;
                this.posY = this.bannerY;
                this.following = true;
                const field = this.input();
                if (field) { field.value = ""; }
                this.announce();
            },

            announce() {
                this.$dispatch("card-focus", { position: this.objectPosition });
            },

            /* A freshly picked hero (no pk yet) only exists as a data URL in the hero
             * field's preview; mirror it so the frames are never a blank placeholder. */
            watchHeroPreview() {
                const preview = document.getElementById("hero-preview");
                if (!preview) { return; }
                const sync = () => {
                    const img = preview.querySelector("img");
                    this.localSrc = img ? img.getAttribute("src") || "" : "";
                };
                new MutationObserver(sync).observe(preview, { childList: true, subtree: true });
                sync();
            },
        }));
    };

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener("alpine:init", registerComponent);
    }
})();
