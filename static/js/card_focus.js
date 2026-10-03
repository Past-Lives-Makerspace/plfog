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
 * The frames are cover fitted, so the photo overflows each frame on one axis only and the
 * other slider has nothing to move there. measure() works out which axis has slack in
 * which frame and the field's template disables a dead slider and says why (issue #427).
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

    /* Every live card frame photo: the two frames on the Photos step and the phone frame on
     * the Review step, which renders through the same partial (_class_card_media.html). */
    const FRAME_IMGS = ".pl-card-focus__frame .cls-img";
    const BOXED_CLASS = "pl-card-focus__img--boxed";
    const BOXED_PROPS = ["--pl-boxed-w", "--pl-boxed-h", "--pl-boxed-left", "--pl-boxed-top"];
    /* The two Photos step frames measure() reads, by the name the copy uses for each. */
    const PHOTOS_FRAMES = {
        laptop: ".pl-card-focus__frame--laptop .cls-media",
        phone: ".pl-card-focus__frame--phone .cls-media",
    };
    /* Less than a pixel of overflow is no room to move. */
    const MIN_SLACK = 1;
    /* What each slider's line says for every answer measure() can give. */
    const WHY = {
        x: {
            both: "",
            laptop: "Moves the laptop card. The phone card already fits side to side.",
            phone: "Moves the phone card. The laptop card already fits side to side.",
            none: "This photo already fits side to side, so only up and down moves it.",
        },
        y: {
            both: "",
            laptop: "Moves the laptop card. The phone card already fits top to bottom.",
            phone: "Moves the phone card. The laptop card already fits top to bottom.",
            none: "This photo already fits top to bottom, so only left and right moves it.",
        },
    };

    /* Cover fit a w by h region into a frame: the scale, and the pixels left over on each
     * axis once the region covers the frame. Exactly one axis has slack unless the shapes
     * match. render() lays the box out with it and measure() reads the slack, so the two
     * cannot drift. */
    function coverFit(frameW, frameH, w, h) {
        const s = Math.max(frameW / w, frameH / h);
        return { s: s, slackX: w * s - frameW, slackY: h * s - frameH };
    }

    /* "both", "laptop", "phone" or "none": where an axis can move. */
    function movesIn(laptop, phone) {
        if (laptop && phone) { return "both"; }
        if (laptop) { return "laptop"; }
        if (phone) { return "phone"; }
        return "none";
    }

    const registerComponent = () => {
        Alpine.data("cardFocus", (config) => ({
            posX: 50,
            posY: 50,
            bannerX: 50,
            bannerY: 50,
            following: true,
            localSrc: "",
            /* The crop box the host is dragging, in natural pixels of the photo the frames
             * show, and that photo's natural size; null until a box is announced. */
            box: null,
            natural: null,
            /* Where each slider can move anything: "both", "laptop", "phone" or "none".
             * "both" until a frame has been measured, so nothing is disabled on a guess. */
            xMoves: "both",
            yMoves: "both",

            init() {
                const banner = parsePosition(config.banner);
                this.bannerX = banner.x;
                this.bannerY = banner.y;
                const initial = parsePosition(config.initial);
                this.posX = initial.x;
                this.posY = initial.y;
                this.following = !this.input() || !this.input().value;
                this.watchHeroPreview();
                // The frames are two widths, and the Review step's frame has no size until
                // that step is on screen, so the box is laid out again on both.
                this.rerender = () => { this.render(); this.measure(); };
                window.addEventListener("resize", this.rerender);
                window.addEventListener("composer-step-shown", this.rerender);
                // A plain frame cannot be measured until its img has pixels. load and error
                // do not bubble, so they are caught in the capture phase on the root: one
                // listener covers every frame img, including the ones x-if renders later. A
                // photo that never arrives has nothing to measure, so neither slider is off.
                this.remeasure = (event) => {
                    if (!(event.target && event.target.matches && event.target.matches(FRAME_IMGS))) { return; }
                    if (event.type === "error") {
                        this.xMoves = "both";
                        this.yMoves = "both";
                        return;
                    }
                    this.measure();
                };
                this.$root.addEventListener("load", this.remeasure, true);
                this.$root.addEventListener("error", this.remeasure, true);
                this.measure();
            },

            destroy() {
                window.removeEventListener("resize", this.rerender);
                window.removeEventListener("composer-step-shown", this.rerender);
                this.$root.removeEventListener("load", this.remeasure, true);
                this.$root.removeEventListener("error", this.remeasure, true);
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
                this.render();
            },

            matchBanner() {
                this.posX = this.bannerX;
                this.posY = this.bannerY;
                this.following = true;
                const field = this.input();
                if (field) { field.value = ""; }
                this.announce();
                this.render();
            },

            announce() {
                this.$dispatch("card-focus", { position: this.objectPosition });
            },

            /* The cropper announces the crop box (hero-crop on window, hero_cropper.js) after
             * every drag, and on load for a saved box whose copy is not rendered yet. The
             * saved copy IS the box, and the server renders it centred, so with a box the
             * banner is 50% 50% and the sliders choose which part of the box shows; without
             * box data (an announcement with only a centre) the centre on the original is the
             * banner, as before. While the host has not moved the sliders, follow it and
             * announce, so the frames on this step and the Review step move with the crop
             * before any save (issue #536), and lay the box out in every frame (issue #547). */
            followBanner(detail) {
                this.previewOnSource();
                const boxed = !!(detail.box && detail.natural && detail.box.w > 0 && detail.box.h > 0
                    && detail.natural.w > 0 && detail.natural.h > 0);
                this.box = boxed ? detail.box : null;
                this.natural = boxed ? detail.natural : null;
                const banner = boxed ? { x: 50, y: 50 } : parsePosition(detail.position);
                this.bannerX = banner.x;
                this.bannerY = banner.y;
                if (this.following) {
                    this.posX = banner.x;
                    this.posY = banner.y;
                    this.announce();
                }
                this.render();
                this.measure();
            },

            /* Show the box region in every frame, cover fitted, exactly what the saved copy
             * will show: the photo is scaled so the box covers the frame, then shifted so
             * the box's top left sits at the frame's top left, less the overflow the sliders
             * choose (posX and posY, the card focus within the box). The four numbers go on
             * the frame's .cls-media wrapper as custom properties, never on the img's style
             * attribute: that attribute belongs to Alpine's object-position binding, which
             * rewrites it on every slider move. hub.css (.pl-card-focus__img--boxed) turns
             * them into a positioned, oversized img the wrapper's overflow:hidden crops. A
             * frame with no size (its step is not on screen) is laid out when the step shows.
             * Without a box the frames keep the object-position path, as before. */
            render() {
                document.querySelectorAll(FRAME_IMGS).forEach((img) => {
                    const media = img.closest(".cls-media");
                    if (!media) { return; }
                    if (!this.box) {
                        img.classList.remove(BOXED_CLASS);
                        BOXED_PROPS.forEach((prop) => media.style.removeProperty(prop));
                        return;
                    }
                    const frameW = media.clientWidth;
                    const frameH = media.clientHeight;
                    if (!frameW || !frameH) { return; }
                    const fit = coverFit(frameW, frameH, this.box.w, this.box.h);
                    const left = -(this.box.x * fit.s) - fit.slackX * this.posX / 100;
                    const top = -(this.box.y * fit.s) - fit.slackY * this.posY / 100;
                    media.style.setProperty("--pl-boxed-w", (this.natural.w * fit.s) + "px");
                    media.style.setProperty("--pl-boxed-h", (this.natural.h * fit.s) + "px");
                    media.style.setProperty("--pl-boxed-left", left + "px");
                    media.style.setProperty("--pl-boxed-top", top + "px");
                    img.classList.add(BOXED_CLASS);
                });
            },

            /* Which axis has room to move in each Photos step frame (issue #427). A frame
             * shows the box (or, without one, the whole photo) cover fitted, so it overflows
             * on one axis only; the other slider writes a value that frame cannot show. The
             * answer per axis is "both", "laptop", "phone" or "none"; the template disables
             * a "none" slider and whyX() / whyY() say what the others move. Never touches
             * posX, posY or the hidden input. A frame with no size (its step is off screen)
             * keeps the previous answer, which is still that photo's; the step reveal calls
             * back. A plain img whose pixels have not arrived keeps the answer too, so a
             * photo switch resets both flags first (watchHeroPreview) and the img's load or
             * error settles them. */
            measure() {
                const laptop = this.frameSlack(PHOTOS_FRAMES.laptop);
                const phone = this.frameSlack(PHOTOS_FRAMES.phone);
                if (!laptop || !phone) { return; }
                this.xMoves = movesIn(laptop.x, phone.x);
                this.yMoves = movesIn(laptop.y, phone.y);
            },

            /* {x, y}: whether each axis has slack in the sized frame matching selector, or
             * null when there is nothing to measure yet. The field's template can hold two
             * frames per shape (a hidden placeholder and the one the mirrored photo fills),
             * so the sized one is the one that counts. */
            frameSlack(selector) {
                const media = Array.from(this.$root.querySelectorAll(selector)).find((el) => el.clientWidth > 0);
                if (!media) { return null; }
                let w, h;
                if (this.box) {
                    w = this.box.w;
                    h = this.box.h;
                } else {
                    const img = media.querySelector(".cls-img");
                    if (!img || !img.naturalWidth || !img.naturalHeight) { return null; }
                    w = img.naturalWidth;
                    h = img.naturalHeight;
                }
                const fit = coverFit(media.clientWidth, media.clientHeight, w, h);
                return { x: fit.slackX >= MIN_SLACK, y: fit.slackY >= MIN_SLACK };
            },

            whyX() {
                return WHY.x[this.xMoves];
            },

            whyY() {
                return WHY.y[this.yMoves];
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
             * field's preview; mirror it so the frames are never a blank placeholder. A new
             * photo has no box yet (the pick clears the crop input), so a box laid out for
             * the old photo is dropped with it and the frames show the new photo whole. */
            watchHeroPreview() {
                const preview = document.getElementById("hero-preview");
                if (!preview) { return; }
                const sync = () => {
                    const img = preview.querySelector("img");
                    const src = img ? img.getAttribute("src") || "" : "";
                    if (src === this.localSrc) { return; }
                    this.localSrc = src;
                    if (this.box) {
                        this.box = null;
                        this.natural = null;
                        this.render();
                    }
                    // The old photo's verdict means nothing for the new one, and a frame img
                    // keeps reporting the old pixels until the new ones decode: nothing is off
                    // until the new photo's load (or error) says so.
                    this.xMoves = "both";
                    this.yMoves = "both";
                    this.$nextTick(() => this.measure());
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
