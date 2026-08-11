/* Info View — Ableton-style hover help for the FOG hub (Spec B).
 *
 * Plain module, no dependencies. Installs a window singleton exactly once:
 * hx-boost body swaps re-execute body scripts, so re-execution must be a cheap
 * re-init, not a double-bind. All listeners live on `document` (never swapped);
 * the mode class lives on <html> (never swapped); the panel node and the
 * [data-help-key] targets are re-found in init() on htmx:afterSettle.
 */
(function () {
    "use strict";

    if (window.__plInfoView) {
        window.__plInfoView.init();
        return;
    }

    var MODE_KEY = "plHelpMode";
    var FOCUSABLE = "a[href], button, input, select, textarea, [tabindex]";

    function isMobile() {
        // Checked at event time (not cached) — the same breakpoint the CSS uses,
        // so a rotated tablet behaves consistently with what's painted.
        return window.matchMedia("(max-width: 768px)").matches;
    }

    function anyVisibleModal() {
        var backdrops = document.querySelectorAll(".pl-modal-backdrop");
        for (var i = 0; i < backdrops.length; i++) {
            if (getComputedStyle(backdrops[i]).display !== "none") return true;
        }
        return false;
    }

    function esc(text) {
        var div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        // textContent -> innerHTML escapes & < > but NOT quotes; add them so the
        // result is safe in attribute contexts (href="...") too.
        return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function safeUrl(url) {
        // Only http(s) or same-origin-relative URLs may land in an href.
        try {
            var u = new URL(String(url == null ? "" : url), window.location.origin);
            if (u.protocol === "http:" || u.protocol === "https:") return String(url);
        } catch (e) { /* fall through */ }
        return "#";
    }

    var InfoView = {
        active: false,
        topics: null, // {key: {title, short_text, url}} once fetched
        fetchState: "idle", // idle | loading | loaded | failed
        currentKey: null,
        pinnedKey: null,
        pinnedEl: null,
        panel: null,
        highlightDone: false,
        _tabbed: [], // elements we gave tabindex="0" (removed on .off())

        // ── Mode ────────────────────────────────────────────────────────────
        toggle: function () {
            if (this.active) this.off();
            else this.on();
        },

        on: function () {
            if (!this.panel || this.active) return;
            this.active = true;
            try {
                localStorage.setItem(MODE_KEY, "1");
            } catch (e) {
                /* private mode — per-tab only */
            }
            document.documentElement.classList.add("pl-help-mode");
            this._syncToggles();
            this.panel.hidden = false;
            if (this.fetchState === "loaded") {
                this._markTargets();
                this._dispatch("pl-help:on", {});
                this._renderIdle();
            } else if (this.fetchState === "loading") {
                // A boosted swap landed mid-fetch — the in-flight request will
                // mark the fresh body when it resolves; don't double-fetch.
                this._renderLoading();
            } else {
                this._fetchTopics();
            }
        },

        off: function () {
            if (!this.active) {
                return;
            }
            // Exiting must not strand focus on a node about to be hidden: if focus
            // is inside the panel (× click, Esc/Shift+/ while reading), land on the
            // topbar ? toggle so keyboard/screen-reader users are somewhere real.
            var refocus = this.panel && this.panel.contains(document.activeElement);
            this.active = false;
            this.pinnedKey = null;
            this.currentKey = null;
            this._clearPinMark();
            try {
                localStorage.setItem(MODE_KEY, "0");
            } catch (e) {
                /* private mode */
            }
            document.documentElement.classList.remove("pl-help-mode");
            this._syncToggles();
            if (this.panel) this.panel.hidden = true;
            this._removeTabindexes();
            if (refocus) {
                var btn = document.querySelector("[data-help-toggle]");
                if (btn) btn.focus();
            }
            this._dispatch("pl-help:off", {});
        },

        // ── Pin / topic ─────────────────────────────────────────────────────
        pin: function (key, el) {
            this._clearPinMark();
            this.pinnedKey = key;
            this.pinnedEl = el || null;
            if (el) el.classList.add("pl-infoview-pinned");
            this._renderTopic(key, true);
            this._dispatch("pl-help:topic", { key: key, pinned: true });
        },

        unpin: function () {
            this._clearPinMark();
            this.pinnedKey = null;
            this._renderIdle();
            this._dispatch("pl-help:topic", { key: null, pinned: false });
        },

        _clearPinMark: function () {
            if (this.pinnedEl) this.pinnedEl.classList.remove("pl-infoview-pinned");
            this.pinnedEl = null;
        },

        _showTopic: function (key) {
            // Hover/focus never pins and never replaces a pin — that's the pin's job.
            if (this.pinnedKey) return;
            if (this.currentKey === key) return;
            this.currentKey = key;
            this._renderTopic(key, false);
            this._dispatch("pl-help:topic", { key: key, pinned: false });
        },

        _clearTopic: function () {
            if (this.pinnedKey || this.currentKey === null) return;
            this.currentKey = null;
            this._renderIdle();
        },

        // ── Fetch ───────────────────────────────────────────────────────────
        _fetchTopics: function () {
            var self = this;
            this.fetchState = "loading";
            this._renderLoading();
            fetch("/help/topics.json")
                .then(function (resp) {
                    if (!resp.ok) throw new Error("HTTP " + resp.status);
                    return resp.json();
                })
                .then(function (data) {
                    self.topics = data.topics;
                    self.fetchState = "loaded";
                    if (!self.active) return;
                    self._markTargets();
                    self._dispatch("pl-help:on", {});
                    self._renderIdle();
                })
                .catch(function () {
                    // Fail-silent: one warn, retry state, no outlines. The page is
                    // never held hostage by help.
                    self.fetchState = "failed";
                    console.warn("Info View: help topics failed to load");
                    if (self.active) self._renderFailed();
                });
        },

        // ── Target marking ──────────────────────────────────────────────────
        _markTargets: function () {
            if (!this.topics) return;
            var unknown = [];
            var annotated = document.querySelectorAll("[data-help-key]");
            for (var i = 0; i < annotated.length; i++) {
                var el = annotated[i];
                var key = el.getAttribute("data-help-key");
                if (this.topics[key]) {
                    el.classList.add("pl-infoview-target");
                    // Keyboard reachability: pure containers (no focusable inside,
                    // not focusable themselves) get tabindex so focusin can reach
                    // them; recorded and removed on .off().
                    if (this.active && !el.matches(FOCUSABLE) && !el.querySelector(FOCUSABLE)) {
                        el.setAttribute("tabindex", "0");
                        this._tabbed.push(el);
                    }
                } else {
                    unknown.push(key);
                }
            }
            if (unknown.length) {
                // Dev-facing typo catcher; unknown keys stay inert for members.
                console.warn("Info View: unregistered help keys on this page:", unknown.join(", "));
            }
        },

        _removeTabindexes: function () {
            for (var i = 0; i < this._tabbed.length; i++) {
                this._tabbed[i].removeAttribute("tabindex");
            }
            this._tabbed = [];
        },

        // ── Panel rendering ─────────────────────────────────────────────────
        _body: function () {
            return this.panel ? this.panel.querySelector(".pl-infoview-panel__body") : null;
        },

        _renderLoading: function () {
            var body = this._body();
            if (body) body.innerHTML = '<p class="pl-infoview-panel__hint">Loading help topics…</p>';
        },

        _renderIdle: function () {
            // Hint — or, on a page with zero marked targets, the empty state (a
            // bare hint promising highlights that never appear reads as broken).
            var body = this._body();
            if (!body) return;
            if (this.fetchState === "loaded" && document.querySelectorAll(".pl-infoview-target").length === 0) {
                var home = this.panel.getAttribute("data-help-home") || "/help/";
                body.innerHTML =
                    '<p class="pl-infoview-panel__hint">Nothing on this page has help notes yet — ' +
                    '<a href="' + esc(safeUrl(home)) + '">browse the Help Center →</a></p>';
                return;
            }
            body.innerHTML =
                '<p class="pl-infoview-panel__hint">Hover or tap anything highlighted to learn what it does. ' +
                "Press Esc to exit.</p>";
        },

        _renderTopic: function (key, pinned) {
            var body = this._body();
            var topic = this.topics && this.topics[key];
            if (!body || !topic) return;
            var html =
                '<h3 class="pl-infoview-panel__title">' +
                (pinned ? '<span class="pl-infoview-panel__pin" aria-hidden="true">📌</span> ' : "") +
                esc(topic.title) +
                "</h3>" +
                '<p class="pl-infoview-panel__text">' + esc(topic.short_text) + "</p>" +
                '<div class="pl-infoview-panel__actions">' +
                '<a class="pl-infoview-panel__more" href="' + esc(safeUrl(topic.url)) + '">Read more →</a>' +
                (pinned ? '<button type="button" class="pl-infoview-panel__unpin" data-infoview-unpin>Unpin</button>' : "") +
                "</div>";
            body.innerHTML = html;
        },

        _renderFailed: function () {
            var body = this._body();
            if (!body) return;
            body.innerHTML =
                '<p class="pl-infoview-panel__hint">Help topics couldn’t load.</p>' +
                '<button type="button" class="pl-btn pl-btn--secondary pl-btn--sm" data-infoview-retry>Try again</button>';
        },

        _syncToggles: function () {
            var toggles = document.querySelectorAll("[data-help-toggle]");
            for (var i = 0; i < toggles.length; i++) {
                toggles[i].setAttribute("aria-pressed", this.active ? "true" : "false");
            }
        },

        _dispatch: function (name, detail) {
            document.dispatchEvent(new CustomEvent(name, { detail: detail }));
        },

        // ── ?highlight= reverse deep-link (§6.9) ────────────────────────────
        _maybeHighlight: function () {
            // Runs once per full page load: init() re-runs on every afterSettle
            // (including non-boost partial swaps) and the URL still carries the
            // param — without the flag the flash would replay on every swap.
            if (this.highlightDone) return;
            this.highlightDone = true;
            var key;
            try {
                key = new URLSearchParams(window.location.search).get("highlight");
            } catch (e) {
                return;
            }
            if (!key) return;
            var el = document.querySelector('[data-help-key="' + (window.CSS ? CSS.escape(key) : key) + '"]');
            if (!el) return; // silent no-op — the key isn't on this page
            el.scrollIntoView({ block: "center" });
            el.classList.add("pl-infoview-flash");
            setTimeout(function () {
                el.classList.remove("pl-infoview-flash");
            }, 2200);
        },

        // ── Init (first run + every htmx:afterSettle) ───────────────────────
        init: function () {
            this.panel = document.querySelector("[data-infoview-panel]");
            if (!this.panel) {
                // Belt to the template guard's suspenders: no panel (flag off, or a
                // non-hub surface) → no mode class, no shortcut effect, no fetch.
                this.active = false;
                document.documentElement.classList.remove("pl-help-mode");
                return;
            }
            this._bindPanel(this.panel);
            // A pin does not survive navigation — the pinned element is gone.
            this.pinnedKey = null;
            this.pinnedEl = null;
            this.currentKey = null;
            this._tabbed = [];

            var stored = null;
            try {
                stored = localStorage.getItem(MODE_KEY);
            } catch (e) {
                /* private mode */
            }
            if (stored === "1") {
                this.active = false; // let on() run its full path on the fresh body
                this.on();
            } else {
                this.active = false;
                document.documentElement.classList.remove("pl-help-mode");
                this._syncToggles();
                this.panel.hidden = true;
            }
            this._maybeHighlight();
        },

        _bindPanel: function (panel) {
            if (panel.__plInfoViewBound) return;
            panel.__plInfoViewBound = true;
            var self = this;
            // Containment: components/modal.html puts @click.outside on .pl-modal
            // and this panel lives outside the modal — without stopPropagation,
            // clicking Read more / Unpin / Try again / × with a modal open would
            // dismiss the modal and lose its form state.
            panel.addEventListener("click", function (e) {
                if (e.target.closest("[data-infoview-close]")) self.off();
                else if (e.target.closest("[data-infoview-unpin]")) self.unpin();
                else if (e.target.closest("[data-infoview-retry]")) self._fetchTopics();
                e.stopPropagation();
            });
        },
    };

    // ── Document-level bindings — bound ONCE; they survive every swap ───────

    document.addEventListener("click", function (e) {
        var toggleBtn = e.target.closest && e.target.closest("[data-help-toggle]");
        if (toggleBtn) {
            e.preventDefault();
            InfoView.toggle();
        }
    });

    // Capture-phase focus guard: a suppressed inspect-click must not steal focus
    // either — without this, clicking a ballot card focuses its inner <select>
    // on mousedown, and the Esc ladder then bails via the never-eat-typing guard
    // (Esc "stops working" right after pinning a form card).
    document.addEventListener(
        "mousedown",
        function (e) {
            if (!InfoView.active || !InfoView.topics) return;
            if (!e.target.closest) return;
            if (e.target.closest("[data-infoview-panel]")) return;
            if (e.target.closest("[data-help-toggle]")) return;
            var el = e.target.closest("[data-help-key]");
            if (el && InfoView.topics[el.getAttribute("data-help-key")]) e.preventDefault();
        },
        true
    );

    // Capture-phase inspect guard: in help mode, clicking an annotated element
    // pins its topic and suppresses the default action (a safe "inspect" mode).
    document.addEventListener(
        "click",
        function (e) {
            if (!InfoView.active || !InfoView.topics) return;
            if (!e.target.closest) return;
            if (e.target.closest("[data-infoview-panel]")) return; // panel clicks behave normally
            if (e.target.closest("[data-help-toggle]")) return; // toggle keeps toggling
            var el = e.target.closest("[data-help-key]"); // innermost annotated ancestor wins
            var key = el && el.getAttribute("data-help-key");
            if (el && InfoView.topics[key]) {
                e.preventDefault();
                e.stopPropagation();
                if (InfoView.pinnedKey === key && InfoView.pinnedEl === el) {
                    InfoView.unpin();
                    return;
                }
                InfoView.pin(key, el);
                if (isMobile()) {
                    // The bottom sheet (up to 45vh) must never sit on top of the
                    // very element it's explaining.
                    el.scrollIntoView({ block: "center", behavior: "smooth" });
                }
                return;
            }
            if (isMobile() && InfoView.pinnedKey) {
                // Mobile "safe close": a tap outside any target only collapses the
                // sheet; the NEXT tap acts normally (no hover to fall back to).
                e.preventDefault();
                e.stopPropagation();
                InfoView.unpin();
            }
        },
        true
    );

    // Capture-phase submit guard: focusing an input inside an annotated form and
    // pressing Enter fires a REAL submit the click listener never sees — the
    // inspect-mode promise must hold for the keyboard too.
    document.addEventListener(
        "submit",
        function (e) {
            if (!InfoView.active || !InfoView.topics) return;
            if (!e.target.closest || !e.target.closest(".pl-infoview-target")) return;
            e.preventDefault();
            var el = e.target.closest("[data-help-key]");
            var key = el && el.getAttribute("data-help-key");
            if (el && InfoView.topics[key]) InfoView.pin(key, el);
        },
        true
    );

    function onPoint(e) {
        if (!InfoView.active || !InfoView.topics) return;
        if (!e.target.closest) return;
        if (e.target.closest("[data-infoview-panel]")) return;
        var el = e.target.closest("[data-help-key]");
        var key = el && el.getAttribute("data-help-key");
        if (el && InfoView.topics[key] && el.classList.contains("pl-infoview-target")) {
            InfoView._showTopic(key);
        } else if (e.type === "mouseover") {
            InfoView._clearTopic();
        }
    }
    document.addEventListener("mouseover", onPoint);
    document.addEventListener("focusin", onPoint);

    // The one global keyboard shortcut listener — guarded so it can never eat
    // typing (inputs, textareas, selects, and contenteditable incl. Quill).
    document.addEventListener("keydown", function (e) {
        var t = e.target;
        if ((t.closest && t.closest("input, textarea, select")) || t.isContentEditable) return;
        if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !e.repeat) {
            e.preventDefault();
            InfoView.toggle();
        } else if (e.key === "Escape" && InfoView.active) {
            // Esc ladder: (1) yield to a visible modal (its own Escape handler
            // wins, help mode survives); (2) unpin; (3) exit. Dropdowns also
            // close on escape.window — a documented, accepted double-fire.
            if (anyVisibleModal()) return;
            if (InfoView.pinnedKey) InfoView.unpin();
            else InfoView.off();
        }
    });

    document.addEventListener("htmx:afterSettle", function () {
        InfoView.init();
    });

    window.__plInfoView = InfoView;
    InfoView.init();
})();
