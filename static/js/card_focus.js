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
 *
 * Loaded once, deferred, from hub/base.html's <head> with the other Alpine components,
 * never from the composer's body: Alpine initialises a boosted page a microtask after
 * htmx inserts it, before a script the page itself loads could arrive, so the component
 * has to be registered before the navigation starts (issue #378).
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

            /* Slider moves write the override; the composer root mirrors it onto the Review step. */
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

            /* The cropper announces the crop's centre (hero-crop on window, hero_cropper.js)
             * after every drag. Track it as the banner and, while the host has not moved
             * the sliders, follow it and announce, so the frames on this step and the
             * Review step move with the crop before any save (issue #536). */
            followBanner(position) {
                this.previewOnSource();
                const banner = parsePosition(position);
                this.bannerX = banner.x;
                this.bannerY = banner.y;
                if (!this.following) { return; }
                this.posX = banner.x;
                this.posY = banner.y;
                this.announce();
            },

            /* A class that already has a cropped copy shows that copy in the frames, and a
             * drag frames a NEW box measured on the ORIGINAL: its centre means nothing on the
             * old copy, which would just slide by a number taken off a different image. Each
             * frame img that shows a copy carries the original's URL in data-hero-source
             * (_class_card_media.html): the first drag swaps every such frame, the two on this
             * step and the phone frame on the Review step (the composer root mirrors our
             * position there, so the whole document is swept), to the original before the
             * centre lands. The attribute goes with the swap, so later drags find nothing to
             * do, and a save renders the new copy, centred, again. A class with no copy yet
             * already shows the original and carries no attribute (issue #547). */
            previewOnSource() {
                document.querySelectorAll("img[data-hero-source]").forEach((img) => {
                    img.setAttribute("src", img.getAttribute("data-hero-source"));
                    img.removeAttribute("data-hero-source");
                });
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
