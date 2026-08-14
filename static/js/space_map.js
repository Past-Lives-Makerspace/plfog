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

    // Keep in sync with hub.css's mobile breakpoint (max-width: 768px).
    var MOBILE_BREAKPOINT = '(max-width: 768px)';
    var DESKTOP_BASE_SCALE = 1;
    var MOBILE_BASE_SCALE = 2;   // "opens enlarged" — ~2x room size on first paint, headroom to MAX_SCALE
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
            // Where the two-finger midpoint last was — its movement pans the map.
            pinchX: 0,
            pinchY: 0,

            // --- Accessible list state (see the .pl-map-list section) ---
            listQuery: '',
            listStatus: 'all',
            listLimit: LIST_PAGE,
            listPageSize: LIST_PAGE,
            listMatched: 0,
            listShown: 0,

            init: function () {
                this.scale = this.baseScale();
                if (!window.matchMedia) return;
                var mq = window.matchMedia(MOBILE_BREAKPOINT);
                var onChange = this.reset.bind(this);
                if (mq.addEventListener) { mq.addEventListener('change', onChange); }
                else if (mq.addListener) { mq.addListener(onChange); }
            },

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

            // The scale the map opens (and resets) to: enlarged on phones so rooms are
            // legible, 1:1 on desktop. Read live so a rotation across the breakpoint re-baselines.
            baseScale: function () {
                var mobile = window.matchMedia && window.matchMedia(MOBILE_BREAKPOINT).matches;
                return mobile ? MOBILE_BASE_SCALE : DESKTOP_BASE_SCALE;
            },

            reset: function () {
                this.scale = this.baseScale();
                this.tx = 0;
                this.ty = 0;
            },

            zoomIn: function () {
                this.scale = clamp(this.scale * 1.25, this.baseScale(), MAX_SCALE);
                this.clampPan();
            },

            zoomOut: function () {
                this.scale = clamp(this.scale / 1.25, this.baseScale(), MAX_SCALE);
                this.clampPan();
            },

            // The visible floor's viewport. Every floor renders its own (x-show keeps the
            // rest in the DOM at display:none, measuring 0×0), so the lookup has to follow
            // the chosen floor — a single x-ref pointed at whichever floor came last.
            viewportEl: function () {
                return this.$root.querySelector('.pl-map-viewport[data-floor="' + this.floor + '"]');
            },

            // Keep the plan from being dragged off-screen: the slack is however much of
            // the stage overflows the viewport — from zooming in, or from the viewport's
            // height cap cropping a tall floor even at 1x. A floor that fits entirely
            // still snaps back square.
            clampPan: function () {
                var el = this.viewportEl();
                var stage = el ? el.querySelector('.pl-map-stage') : null;
                if (!el || !stage) return;
                var slackX = Math.max(0, stage.offsetWidth * this.scale - el.clientWidth) / 2;
                var slackY = Math.max(0, stage.offsetHeight * this.scale - el.clientHeight) / 2;
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

            // The midpoint between the two touches — moving it pans, so a two-finger
            // swipe on a touchscreen drags the map even while it zooms, like a maps app.
            pinchCentroid: function () {
                var pts = this.pointerList();
                return { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
            },

            onPointerDown: function (event) {
                // A marker is a real button — let the click through instead of panning.
                if (event.target.closest && event.target.closest('.pl-map-marker')) return;
                this.pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
                if (this.pointerList().length === 2) {
                    this.pinchStart = this.pinchDistance();
                    this.pinchScale = this.scale;
                    var start = this.pinchCentroid();
                    this.pinchX = start.x;
                    this.pinchY = start.y;
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
                        this.scale = clamp((distance / this.pinchStart) * this.pinchScale, this.baseScale(), MAX_SCALE);
                    }
                    var mid = this.pinchCentroid();
                    this.tx += mid.x - this.pinchX;
                    this.ty += mid.y - this.pinchY;
                    this.pinchX = mid.x;
                    this.pinchY = mid.y;
                    this.clampPan();
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
                var pts = this.pointerList();
                if (pts.length < 2) this.pinchStart = 0;
                // Lifting one finger out of a pinch hands over to a plain drag, so the
                // remaining finger keeps panning without a lift-and-retouch.
                if (pts.length === 1) {
                    this.dragging = true;
                    this.lastX = pts[0].x;
                    this.lastY = pts[0].y;
                    return;
                }
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
