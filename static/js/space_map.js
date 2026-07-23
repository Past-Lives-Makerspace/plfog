/* Interactive space map — floor switching plus hand-rolled pan/zoom/pinch.
 *
 * Registered as the Alpine component `plMap`. Image and markers live in one
 * transformed wrapper (.pl-map-stage), so a single `translate() scale()` moves
 * them together and percent-positioned markers stay glued to the plan at any
 * zoom or viewport width — no per-marker maths, no pan/zoom dependency.
 *
 * Honors prefers-reduced-motion by dropping the transition (see hub.css).
 */
(function () {
    'use strict';

    var MIN_SCALE = 1;
    var MAX_SCALE = 4;

    function clamp(value, low, high) {
        return Math.min(high, Math.max(low, value));
    }

    function plMap(initialFloor) {
        return {
            floor: initialFloor || null,
            scale: 1,
            tx: 0,
            ty: 0,
            dragging: false,
            lastX: 0,
            lastY: 0,
            // Active pointers, keyed by pointerId — two of them means a pinch.
            pointers: {},
            pinchStart: 0,
            pinchScale: 1,

            setFloor: function (id) {
                this.floor = id;
                this.reset();
            },

            stageStyle: function () {
                return 'transform: translate(' + this.tx + 'px, ' + this.ty + 'px) scale(' + this.scale + ');';
            },

            reset: function () {
                this.scale = 1;
                this.tx = 0;
                this.ty = 0;
            },

            zoomIn: function () {
                this.scale = clamp(this.scale * 1.25, MIN_SCALE, MAX_SCALE);
                this.clampPan();
            },

            zoomOut: function () {
                this.scale = clamp(this.scale / 1.25, MIN_SCALE, MAX_SCALE);
                this.clampPan();
            },

            // Keep the plan from being dragged off-screen: the further you zoom, the more
            // slack there is, and at 1x there is none (so the map always snaps back square).
            clampPan: function () {
                var el = this.$refs.viewport;
                if (!el) return;
                var slackX = (el.clientWidth * (this.scale - 1)) / 2;
                var slackY = (el.clientHeight * (this.scale - 1)) / 2;
                this.tx = clamp(this.tx, -slackX, slackX);
                this.ty = clamp(this.ty, -slackY, slackY);
            },

            pointerList: function () {
                var out = [];
                for (var key in this.pointers) {
                    if (Object.prototype.hasOwnProperty.call(this.pointers, key)) out.push(this.pointers[key]);
                }
                return out;
            },

            pinchDistance: function () {
                var pts = this.pointerList();
                if (pts.length < 2) return 0;
                var dx = pts[0].x - pts[1].x;
                var dy = pts[0].y - pts[1].y;
                return Math.sqrt(dx * dx + dy * dy);
            },

            onPointerDown: function (event) {
                // A marker is a real button — let the click through instead of panning.
                if (event.target.closest && event.target.closest('.pl-map-marker')) return;
                this.pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
                if (this.pointerList().length === 2) {
                    this.pinchStart = this.pinchDistance();
                    this.pinchScale = this.scale;
                    this.dragging = false;
                    return;
                }
                this.dragging = true;
                this.lastX = event.clientX;
                this.lastY = event.clientY;
                if (event.currentTarget.setPointerCapture) {
                    event.currentTarget.setPointerCapture(event.pointerId);
                }
            },

            onPointerMove: function (event) {
                if (!(event.pointerId in this.pointers)) return;
                this.pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
                if (this.pointerList().length === 2) {
                    var distance = this.pinchDistance();
                    if (this.pinchStart > 0 && distance > 0) {
                        this.scale = clamp((distance / this.pinchStart) * this.pinchScale, MIN_SCALE, MAX_SCALE);
                        this.clampPan();
                    }
                    return;
                }
                if (!this.dragging) return;
                this.tx += event.clientX - this.lastX;
                this.ty += event.clientY - this.lastY;
                this.lastX = event.clientX;
                this.lastY = event.clientY;
                this.clampPan();
            },

            onPointerUp: function (event) {
                delete this.pointers[event.pointerId];
                if (this.pointerList().length < 2) this.pinchStart = 0;
                this.dragging = false;
            },

            onWheel: function (event) {
                if (event.deltaY < 0) {
                    this.zoomIn();
                } else {
                    this.zoomOut();
                }
            }
        };
    }

    window.plMap = plMap;
    document.addEventListener('alpine:init', function () {
        window.Alpine.data('plMap', plMap);
    });
})();
