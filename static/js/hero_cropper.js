/* Hero image cropper glue.
 *
 * Looks for a [data-hero-image-field] block (rendered by
 * templates/classes/_components/hero_image_field.html), wires Cropper.js to
 * the [data-hero-cropper-preview] img inside its #hero-preview mount, and
 * writes the crop box the user drags to the hidden [data-hero-crop-input]
 * field as
 *   {"x": int, "y": int, "w": int, "h": int}
 * in source-image pixels. Empty string means "no crop set". Only a drag
 * (Cropper's cropend) ever writes: an untouched cropper leaves the field
 * exactly as the server rendered it, so a class nobody cropped keeps an empty
 * hero_crop rather than a full-frame box it never asked for.
 *
 * Cropper.js measures its mount when it initialises, so it must never run
 * inside a hidden step pane: a display:none mount reads as 200x100 and stays
 * that size after the pane opens. The composer announces every reveal as a
 * window event, composer-step-shown {step: N}
 * (templates/classes/_components/class_composer.html). The first reveal of
 * the pane this field lives in mounts the cropper; every later reveal resizes
 * it, which also repairs a window resize that happened while it was hidden.
 * A field that is already on screen when Cropper.js arrives mounts at once.
 *
 * The template's own inline scripts own the file pick (an instant upload on a
 * saved class, a FileReader preview before the first save). They replace the
 * preview img and then call window.initHeroCropper(), which re-queries the img
 * and mounts on it once it has loaded.
 *
 * Loads Cropper.js from a CDN on first use; safe to include the script on
 * pages that don't have a hero image field: it just no-ops.
 */
(function () {
    "use strict";

    var CROPPER_CSS = "https://cdn.jsdelivr.net/npm/cropperjs@1.6.1/dist/cropper.min.css";
    var CROPPER_JS = "https://cdn.jsdelivr.net/npm/cropperjs@1.6.1/dist/cropper.min.js";
    var ASPECT = 16 / 9;
    var STEP_SHOWN_EVENT = "composer-step-shown";

    function loadStylesheet(href) {
        if (document.querySelector('link[href="' + href + '"]')) return;
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        document.head.appendChild(link);
    }

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[src="' + src + '"]');
            if (existing) {
                if (window.Cropper) return resolve();
                existing.addEventListener("load", function () { resolve(); });
                existing.addEventListener("error", reject);
                return;
            }
            var s = document.createElement("script");
            s.src = src;
            s.async = true;
            s.onload = function () { resolve(); };
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function readInitialCrop(input) {
        var raw = (input.value || "").trim();
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (e) { return null; }
    }

    function writeCrop(input, data) {
        if (!data) { input.value = ""; return; }
        input.value = JSON.stringify({
            x: Math.round(data.x),
            y: Math.round(data.y),
            w: Math.round(data.width),
            h: Math.round(data.height),
        });
    }

    /* Run fn once the img has pixels. An img that already decoded (cached, or a
     * data URL) never fires load again, so check complete + naturalWidth first;
     * a broken image (complete, naturalWidth 0) never calls fn, and there is
     * nothing to crop anyway. */
    function whenLoaded(img, fn) {
        if (img.complete && img.naturalWidth) { fn(); return; }
        img.addEventListener("load", fn, { once: true });
    }

    /* The composer step pane this field sits in, or null outside a stepped page. */
    function stepOf(container) {
        var pane = container.closest("[data-composer-step]");
        return pane ? parseInt(pane.getAttribute("data-composer-step"), 10) : null;
    }

    function attach(container) {
        var cropInput = container.querySelector("[data-hero-crop-input]");
        var mount = container.querySelector("#hero-preview");
        if (!cropInput || !mount) return null;

        var instance = null;
        var pending = null; // the preview img whose load event we are waiting on

        function currentPreview() {
            return mount.querySelector("[data-hero-cropper-preview]");
        }

        function isVisible() {
            return mount.offsetWidth > 0;
        }

        function destroy() {
            pending = null;
            if (!instance) return;
            var old = instance;
            instance = null;
            try {
                old.destroy();
            } catch (err) {
                // The img it was mounted on was replaced while Cropper was still
                // loading its copy; there is nothing left in the DOM to tear down.
                console.warn("Hero cropper teardown skipped:", err);
            }
        }

        function mountOn(preview) {
            var initial = readInitialCrop(cropInput);
            instance = new window.Cropper(preview, {
                aspectRatio: ASPECT,
                viewMode: 1,
                autoCropArea: 1,
                background: false,
                movable: false,
                zoomable: false,
                scalable: false,
                rotatable: false,
                ready: function () {
                    // Restore a saved crop; write nothing for an untouched one.
                    if (initial && initial.w && initial.h) {
                        instance.setData({
                            x: initial.x, y: initial.y,
                            width: initial.w, height: initial.h,
                        });
                    }
                },
                cropend: function () { writeCrop(cropInput, instance.getData(true)); },
            });
        }

        /* Mount on whatever preview img is in the mount right now, once it has
         * loaded and only while the mount is actually laid out. */
        function init() {
            destroy();
            var preview = currentPreview();
            if (!preview || !preview.getAttribute("src") || !isVisible()) return;
            pending = preview;
            whenLoaded(preview, function () {
                if (pending !== preview) return; // replaced or torn down while loading
                pending = null;
                mountOn(preview);
            });
        }

        function onStepShown(step) {
            if (step !== stepOf(container)) return;
            if (instance) { instance.resize(); } else { init(); }
        }

        function handleStepShown(event) {
            // A boosted navigation swaps the page under us; let the stale listener go.
            if (!container.isConnected) {
                window.removeEventListener(STEP_SHOWN_EVENT, handleStepShown);
                return;
            }
            onStepShown(event.detail && event.detail.step);
        }
        window.addEventListener(STEP_SHOWN_EVENT, handleStepShown);

        // Already on screen (?step=2, or a page with no step panes): mount now.
        init();

        return { refresh: init };
    }

    function boot() {
        var containers = document.querySelectorAll("[data-hero-image-field]");
        if (!containers.length) return;
        loadStylesheet(CROPPER_CSS);
        var fields = [];
        var loaded = loadScript(CROPPER_JS).then(function () {
            containers.forEach(function (container) {
                var field = attach(container);
                if (field) fields.push(field);
            });
        });
        loaded.catch(function (err) {
            // Cropper failed to load: the plain file input still works.
            console.warn("Hero cropper failed to load:", err);
        });

        /* The template's inline upload scripts call this after swapping in a new
         * preview img. Defined at boot, before Cropper.js has arrived, so a pick
         * during the CDN load still mounts once the library is here. */
        window.initHeroCropper = function () {
            loaded.then(function () {
                fields.forEach(function (field) { field.refresh(); });
            }, function () { /* already warned above */ });
        };
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
