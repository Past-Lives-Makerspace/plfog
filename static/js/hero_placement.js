/* Visual Hero Placement Tool — Lite Version.
 *
 * Provides an Alpine.js component 'heroPlacement' that uses simple range 
 * sliders to adjust CSS object-position in real-time. No external libraries.
 */
(function () {
    "use strict";

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

            init() {
                this.parseInitial();
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
