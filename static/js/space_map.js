/* Interactive space map — floor switching, hand-rolled pan/zoom/pinch, and the
 * filtering of the accessible list that sits under the map.
 *
 * Registered as the Alpine component `plMap`. Image and markers live in one
 * transformed wrapper (.pl-map-stage), so a single `translate() scale()` moves
 * them together and percent-positioned markers stay glued to the plan at any
 * zoom or viewport width — no per-marker maths, no pan/zoom dependency.
 *
 * The Map and Listings tabs share this one component, so the chosen floor follows
 * the reader between them. Every list row is server-rendered (see
 * _space_listings.html): filtering only hides rows in the browser, so with
 * JavaScript off the whole list survives intact — and the tab nav, which would do
 * nothing without Alpine, is x-cloak'd away entirely.
 *
 * Honors prefers-reduced-motion by dropping the transition (see hub.css).
 */
(function () {
    'use strict';

    var MIN_SCALE = 1;
    var MAX_SCALE = 4;
    // How many list rows a "page" is — the first read, and each Show-more step.
    var LIST_PAGE = 20;

    function clamp(value, low, high) {
        return Math.min(high, Math.max(low, value));
    }

    function plMap(initialFloor, initialPane) {
        return {
            floor: initialFloor || null,
            // Which tab is showing: 'map' or 'listings'.
            pane: initialPane === 'listings' ? 'listings' : 'map',
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

            // --- Accessible list state (see the .pl-map-list section) ---
            listQuery: '',
            listStatus: 'all',
            listLimit: LIST_PAGE,
            listPageSize: LIST_PAGE,
            listMatched: 0,
            listShown: 0,

            setPane: function (name) {
                this.pane = name;
                // Keep the tab in the URL so a Listings link can be shared and a reload,
                // or the browser Back button, lands on the tab the reader was actually on.
                if (window.history && window.history.replaceState) {
                    var url = new URL(window.location.href);
                    if (name === 'map') {
                        url.searchParams.delete('tab');
                    } else {
                        url.searchParams.set('tab', name);
                    }
                    window.history.replaceState({}, '', url);
                }
            },

            setFloor: function (id) {
                this.floor = id;
                // The status chips belong to the floor they are counting, so a new floor
                // starts unfiltered — no invisible chip filtering a list you can't see it on.
                // The search box is the member's own words, so that carries across.
                this.listStatus = 'all';
                this.resetList();
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
                // Trackpad pinch (browsers report it as a ctrl-wheel) and Ctrl+wheel zoom;
                // a plain two-finger swipe pans, matching native trackpad behaviour. The
                // +/- buttons remain for explicit zoom. @wheel.prevent stops the page scroll.
                if (event.ctrlKey) {
                    if (event.deltaY < 0) {
                        this.zoomIn();
                    } else {
                        this.zoomOut();
                    }
                    return;
                }
                this.tx -= event.deltaX;
                this.ty -= event.deltaY;
                this.clampPan();
            },

            // --- The accessible list ---

            // Back to the first pageful. Any change of floor, status or search text is a
            // new question, so it deserves a fresh (short) answer.
            resetList: function () {
                this.listLimit = LIST_PAGE;
            },

            setListStatus: function (slug) {
                this.listStatus = slug;
                this.resetList();
            },

            // One button, two jobs: reveal the next pageful, or — once everything is out —
            // fold back to the first. It never leaves the DOM, so keyboard focus survives.
            toggleListMore: function () {
                this.listLimit = this.listShown >= this.listMatched ? LIST_PAGE : this.listLimit + LIST_PAGE;
            },

            // Put the caller back on the search box it just emptied, so a keyboard user
            // isn't dropped at the top of the document when the empty state disappears.
            clearListFilters: function (event) {
                this.listQuery = '';
                this.listStatus = 'all';
                this.resetList();
                var block = event && event.currentTarget ? event.currentTarget.closest('.pl-map-list__floor-block') : null;
                var input = block ? block.querySelector('.pl-map-list__search-input') : null;
                if (input) input.focus();
            },

            /* Show the rows on the chosen floor that match the chips and the search box,
             * up to the current limit, and hide the rest. Driven by x-effect, so it re-runs
             * whenever any of that state changes — every dependency is therefore read up
             * front, before any early exit, or Alpine would stop tracking it.
             *
             * Row state is read off data-* attributes rather than a parallel JS array, so
             * an HTMX out-of-band row swap (after a request) drops straight back into the
             * filter on the next htmx:after-settle.
             */
            syncList: function () {
                var floor = this.floor;
                var status = this.listStatus;
                var needle = this.listQuery.trim().toLowerCase();
                var limit = this.listLimit;
                var root = this.$refs.list;
                if (!root) return;
                var rows = root.querySelectorAll('.pl-map-list__row');
                var matched = 0;
                var shown = 0;
                for (var i = 0; i < rows.length; i++) {
                    var row = rows[i];
                    var keep = Number(row.dataset.floor) === floor
                        && (status === 'all' || row.dataset.status === status)
                        && (needle === '' || (row.dataset.search || '').indexOf(needle) !== -1);
                    if (!keep) {
                        row.hidden = true;
                        continue;
                    }
                    matched += 1;
                    row.hidden = matched > limit;
                    if (!row.hidden) shown += 1;
                }
                this.listMatched = matched;
                this.listShown = shown;
            },

            // The live-region sentence — what a screen reader hears after every change.
            listCountLabel: function (floorName) {
                if (this.listMatched === 0) return 'No spaces on ' + floorName + ' match those filters.';
                var noun = this.listMatched === 1 ? ' space on ' : ' spaces on ';
                if (this.listShown >= this.listMatched) {
                    return 'Showing all ' + this.listMatched + noun + floorName + '.';
                }
                return 'Showing ' + this.listShown + ' of ' + this.listMatched + noun + floorName + '.';
            },

            listMoreLabel: function () {
                if (this.listShown >= this.listMatched) return 'Show fewer';
                var rest = this.listMatched - this.listShown;
                return rest <= LIST_PAGE ? 'Show the last ' + rest : 'Show ' + LIST_PAGE + ' more';
            }
        };
    }

    window.plMap = plMap;
    document.addEventListener('alpine:init', function () {
        window.Alpine.data('plMap', plMap);
    });
})();
