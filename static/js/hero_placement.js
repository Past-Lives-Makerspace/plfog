/* Visual Hero Placement Tool — Lite Version.
 *
 * Provides an Alpine.js component 'heroPlacement' that uses simple range 
 * sliders to adjust CSS object-position in real-time. No external libraries.
 *
 * The hero img (data-hero-img in all three templates: the guild banner, the Help Center
 * banner and the class page's category hero) is cover fitted, so the photo overflows its
 * frame on one axis only and the other slider has nothing to move. measure() works out
 * which axis has slack, the template disables a dead slider and why() says so: the same
 * math as cardFocus (card_focus.js, issue #427). The frames are fluid, so the answer is
 * worked out again whenever the img's box changes size.
 */
(function () {
    "use strict";

    /* Less than a pixel of overflow is no room to move. */
    const MIN_SLACK = 1;
    /* The one line under the sliders, by which of them can move. */
    const WHY = {
        x: "This photo already fits side to side, so only up and down moves it.",
        y: "This photo already fits top to bottom, so only left and right moves it.",
        both: "This photo already fits the banner exactly, so there is nothing to move.",
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
            /* Whether each slider moves anything. True until the photo has been measured,
             * so nothing is disabled on a guess. */
            xMoves: true,
            yMoves: true,

            init() {
                this.parseInitial();
                // The guild page mounts the component with no banner (for the Edit button),
                // so there may be no img to watch.
                const img = this.heroImg();
                if (!img) { return; }
                // A photo that never arrives has nothing to measure, so neither slider is off.
                this.remeasure = (event) => {
                    if (event.type === "error") {
                        this.xMoves = true;
                        this.yMoves = true;
                        return;
                    }
                    this.measure();
                };
                img.addEventListener("load", this.remeasure);
                img.addEventListener("error", this.remeasure);
                // The img fills the hero, whose shape follows the viewport and its own
                // content, so watch the img's box rather than only the window.
                this.resizer = new ResizeObserver(() => this.measure());
                this.resizer.observe(img);
                this.measure();
            },

            destroy() {
                const img = this.heroImg();
                if (img && this.remeasure) {
                    img.removeEventListener("load", this.remeasure);
                    img.removeEventListener("error", this.remeasure);
                }
                if (this.resizer) { this.resizer.disconnect(); }
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

            /* Which axis has room to move: cover fit the photo into the img's box (scale =
             * max(frameW / naturalW, frameH / naturalH)) and read what is left over on each
             * axis. Never touches posX or posY, so a saved position survives on a disabled
             * slider. An img with no size or no pixels yet keeps the previous answer; its
             * load, or the next resize, calls back. */
            measure() {
                const img = this.heroImg();
                if (!img) { return; }
                const frameW = img.clientWidth;
                const frameH = img.clientHeight;
                const w = img.naturalWidth;
                const h = img.naturalHeight;
                if (!frameW || !frameH || !img.complete || !w || !h) { return; }
                const s = Math.max(frameW / w, frameH / h);
                this.xMoves = w * s - frameW >= MIN_SLACK;
                this.yMoves = h * s - frameH >= MIN_SLACK;
            },

            /* The line under the sliders: empty while both move. */
            why() {
                if (!this.xMoves && !this.yMoves) { return WHY.both; }
                if (!this.xMoves) { return WHY.x; }
                if (!this.yMoves) { return WHY.y; }
                return "";
            },

            startAdjusting() {
                this.isAdjusting = true;
                // The frame may have changed size since the page measured it.
                this.measure();
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
