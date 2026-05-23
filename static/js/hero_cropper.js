/* Hero image cropper glue.
 *
 * Looks for a [data-hero-image-field] block (rendered by
 * templates/classes/_components/hero_image_field.html), wires Cropper.js to
 * the preview img (or to a freshly-picked file once chosen), and writes the
 * crop box to the hidden [data-hero-crop-input] field as
 *   {"x": int, "y": int, "w": int, "h": int}
 * in source-image pixels. Empty string means "no crop set".
 *
 * Loads Cropper.js from a CDN on first use; safe to include the script on
 * pages that don't have a hero image field — it just no-ops.
 */
(function () {
    "use strict";

    var CROPPER_CSS = "https://cdn.jsdelivr.net/npm/cropperjs@1.6.1/dist/cropper.min.css";
    var CROPPER_JS = "https://cdn.jsdelivr.net/npm/cropperjs@1.6.1/dist/cropper.min.js";
    var ASPECT = 16 / 9;

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

    function attach(container) {
        var fileInput = container.querySelector('input[type="file"]');
        var cropInput = container.querySelector('[data-hero-crop-input]');
        if (!cropInput) return;

        // Build (or reuse) a preview <img> the cropper attaches to.
        var preview = container.querySelector('[data-hero-cropper-preview]');
        if (!preview) {
            var existingThumb = container.querySelector('img');
            preview = document.createElement("img");
            preview.setAttribute("data-hero-cropper-preview", "");
            preview.style.maxWidth = "100%";
            preview.style.maxHeight = "320px";
            preview.style.marginBottom = "0.5rem";
            preview.style.display = existingThumb ? "block" : "none";
            if (existingThumb) {
                preview.src = existingThumb.src;
                existingThumb.replaceWith(preview);
            } else {
                container.insertBefore(preview, container.querySelector('input[type="file"]') || container.firstChild);
            }
        }

        var instance = null;

        function destroy() {
            if (instance) { instance.destroy(); instance = null; }
        }

        function init() {
            destroy();
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
                    if (initial && initial.w && initial.h) {
                        instance.setData({
                            x: initial.x, y: initial.y,
                            width: initial.w, height: initial.h,
                        });
                    }
                    writeCrop(cropInput, instance.getData(true));
                },
                cropend: function () { writeCrop(cropInput, instance.getData(true)); },
            });
        }

        if (preview.src && preview.style.display !== "none") {
            init();
        }

        if (fileInput) {
            fileInput.addEventListener("change", function () {
                var file = fileInput.files && fileInput.files[0];
                if (!file) return;
                var reader = new FileReader();
                reader.onload = function (ev) {
                    preview.src = ev.target.result;
                    preview.style.display = "block";
                    cropInput.value = "";
                    // Wait a tick for the new src to take effect.
                    setTimeout(init, 50);
                };
                reader.readAsDataURL(file);
            });
        }
    }

    function boot() {
        var containers = document.querySelectorAll('[data-hero-image-field]');
        if (!containers.length) return;
        loadStylesheet(CROPPER_CSS);
        loadScript(CROPPER_JS).then(function () {
            containers.forEach(attach);
        }).catch(function (err) {
            // Cropper failed to load — silently fall back to the plain file input.
            console.warn("Hero cropper failed to load:", err);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
