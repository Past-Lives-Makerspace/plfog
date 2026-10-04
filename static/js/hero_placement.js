/* Visual Hero Placement Tool — Lite Version.
 *
 * Provides an Alpine.js component 'heroPlacement' that uses simple range 
 * sliders to adjust CSS object-position in real-time. No external libraries.
 *
 * The hero img (data-hero-img in all three templates: the guild banner, the Help Center
 * banner and the class page's category hero) is cover fitted, so in any one frame the photo
 * overflows on one axis only and the other slider has nothing to move there (the cardFocus
 * precedent, card_focus.js, issue #427). These frames are fluid: one saved position serves
 * every screen, from a tall phone frame to a wide desktop one, so which axis is dead changes
 * with the screen. Each template states the range of frame shapes its CSS can produce
 * (data-frame-min and data-frame-max, width over height) and measure() compares the photo's
 * own shape with it: a slider is off only when it moves nothing on every screen, and why()
 * says so. Which screens each live slider moves it on depends on the photo (a 4:3 photo moves
 * up and down on most phones too), so when both are on the line only says that each one moves
 * it somewhere. The editor's own frame plays no part.
 */
(function () {
    "use strict";

    /* The one line under the sliders, by which of them can move. */
    const WHY = {
        x: "This photo already fits side to side on every screen, so only up and down moves it.",
        y: "This photo already fits top to bottom on every screen, so only left and right moves it.",
        both: "Each slider moves the photo on some screens, so both stay on.",
    };

    const registerComponent = () => {
        // Prevent double-registration
        if (window.Alpine && window.Alpine.components && window.Alpine.components['heroPlacement']) return;
        
        Alpine.data('heroPlacement', (config) => ({
            isAdjusting: false,
            isLoading: false,
            // Parse initial percentages from "X% Y%" string
            posX: 50,
            posY: 50,
            initialX: 50,
            initialY: 50,
            /* Whether each slider moves the photo on at least one screen. True until the
             * photo has been measured, so nothing is disabled on a guess. */
            measured: false,
            xMoves: true,
            yMoves: true,

            init() {
                this.parseInitial();
                // The guild page mounts the component with no banner (for the Edit button),
                // so there may be no img to watch.
                const img = this.heroImg();
                if (!img) { return; }
                // The photo's shape is known once its pixels arrive: now if they already
                // have, else on load. A photo that never arrives leaves both sliders on.
                this.remeasure = () => this.measure();
                img.addEventListener("load", this.remeasure);
                this.measure();
            },

            destroy() {
                const img = this.heroImg();
                if (img && this.remeasure) {
                    img.removeEventListener("load", this.remeasure);
                }
            },

            /* By attribute, not x-ref: Alpine registers a child's x-ref after the root's
             * init() has run. */
            heroImg() {
                return this.$root.querySelector("[data-hero-img]");
            },

            parseInitial() {
                const parts = (config.initialObjectPosition || '50% 50%').split(' ');
                this.posX = parseFloat(parts[0]) || 50;
                this.posY = parseFloat(parts[1]) || 50;
                this.initialX = this.posX;
                this.initialY = this.posY;
            },

            get objectPosition() {
                return `${this.posX}% ${this.posY}%`;
            },

            /* Which axis moves the photo on some screen. Cover fitted into a frame of shape
             * f, a photo of shape a overflows sideways when a > f and top to bottom when
             * a < f. So with frames from fmin to fmax, left and right moves nothing anywhere
             * when a <= fmin (every frame is at least as wide a shape as the photo), and up
             * and down moves nothing anywhere when a >= fmax. Never touches posX or posY, so
             * a saved position survives on a disabled slider. */
            measure() {
                const img = this.heroImg();
                if (!img) { return; }
                const w = img.naturalWidth;
                const h = img.naturalHeight;
                if (!img.complete || !w || !h) { return; }
                const fmin = parseFloat(img.dataset.frameMin);
                const fmax = parseFloat(img.dataset.frameMax);
                if (!(fmin > 0 && fmax > fmin)) {
                    throw new Error("heroPlacement: the hero img needs data-frame-min below data-frame-max");
                }
                const a = w / h;
                this.xMoves = a > fmin;
                this.yMoves = a < fmax;
                this.measured = true;
            },

            /* The line under the sliders: nothing until the photo is measured. */
            why() {
                if (!this.measured) { return ""; }
                if (!this.xMoves) { return WHY.x; }
                if (!this.yMoves) { return WHY.y; }
                return WHY.both;
            },

            startAdjusting() {
                this.isAdjusting = true;
            },

            cancel() {
                this.posX = this.initialX;
                this.posY = this.initialY;
                this.isAdjusting = false;
            },

            async save() {
                this.isLoading = true;
                
                const payload = {
                    content_type_id: config.contentTypeId,
                    object_id: config.objectId,
                    crop: {
                        x: Math.round(this.posX),
                        y: Math.round(this.posY),
                        w: 0, // 0 signals direct percentage mode to the server
                        h: 0
                    }
                };

                try {
                    const response = await fetch(config.updateUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': config.csrfToken
                        },
                        body: JSON.stringify(payload)
                    });
                    
                    const result = await response.json();
                    if (result.status === 'ok') {
                        this.initialX = this.posX;
                        this.initialY = this.posY;
                        this.isAdjusting = false;
                        if (window.showToast) window.showToast("Hero placement updated!");
                    } else {
                        alert("Error: " + (result.error || "Unknown error"));
                    }
                } catch (err) {
                    console.error("Failed to save hero placement:", err);
                    alert("Network error while saving.");
                } finally {
                    this.isLoading = false;
                }
            }
        }));
    };

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener('alpine:init', registerComponent);
    }
})();
