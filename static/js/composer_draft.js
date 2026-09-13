/* Draft persistence for the class composer (issue #368, item 3c).
 *
 * The composer is one long form across five panes with one save at the end, so a
 * refresh, a closed tab or a dead battery took everything typed since the last save.
 * This mirrors the typed fields into localStorage as they are typed and offers them
 * back on the next load.
 *
 * Client side only, deliberately. A server autosave would create half empty
 * ClassOffering rows that Manage My Classes, the Needs Attention list and the admin
 * class list would each have to learn to hide, plus a slug strategy before there is a
 * title and a reaper for what gets abandoned. The loss being reported is a refresh,
 * and a browser held copy covers that.
 *
 * OFFERED, NEVER AUTOMATIC. What the server rendered may be newer than what is here:
 * another tab, an admin, or the person themselves on another device may have saved
 * since this copy was written. Overwriting that silently is worse than one click, so
 * a stored draft renders as an offer with a Restore button and the time it was kept.
 *
 * WHAT IS STORED. Named text boxes, dropdowns and text areas inside the composer form, and
 * only where the value differs from the baseline, which is what the database holds (see
 * boot: usually the rendered page, and nothing at all on a render the server marked
 * unsaved). Everything else is skipped on purpose:
 *   - file inputs (the hero photo and the gallery) cannot live in localStorage at all,
 *     and the notice says so rather than pretending otherwise;
 *   - hidden inputs belong to a widget, not to the person. The hero crop box, the
 *     scheduler's sessions-N-* rows, the card focus position, the CSRF token, the
 *     action and step fields are all hidden, and writing one back behind its widget
 *     would leave the widget showing something else. The crop especially: it belongs
 *     to a photo that may have been replaced since;
 *   - formset management forms (TOTAL_FORMS and friends) describe the rows the server
 *     rendered, so a stale count would mis-parse the POST;
 *   - checkboxes and radios, which in this form are the free/private ticks, the
 *     formset DELETE boxes and the scheduling type cards: a restored tick reads as a
 *     decision the person did not just make;
 *   - anything with no name: the browser never posts it, so no save can carry it.
 *
 * PER PERSON, PER CLASS. The key is rendered by the server (_composer_context in
 * classes/views.py) and carries the signed in user, the portal and the class pk, so a
 * shared browser never offers one member's draft to the next, and the admin composer
 * and the teach composer for the same class keep their own copies.
 *
 * CLEARED WHEN THE WORK IS SAFE. A successful save marks the session server side and
 * the next composer render carries data-composer-draft-saved, which is the signal to
 * forget the stored copy: the database has it now, and normalisation (a price typed as
 * 80 coming back as 80.00) means comparing values cannot tell that on its own. Create
 * mode redirects to the edit URL under a different key, so the key that was in flight
 * rides along in sessionStorage and is forgotten by the page that arrives. A Discard
 * click forgets it too; typing again starts a new copy, and the line says so. A copy that
 * matches the page is not offered but is NOT deleted: a save the server refuses re-renders
 * the composer bound to the POST, and there the copy is the only backup of it. That render
 * carries data-composer-draft-unsaved, which tells this file not to trust anything on the
 * page as saved.
 *
 * Two ways a copy outlives the composer that wrote it. A class published while an
 * instructor still has a draft for it moves to the light edit form
 * (classes/teach/class_form_published.html), which renders no composer and so no draft key:
 * that copy is orphaned and nothing will ever offer it again. And a copy nobody comes back
 * to is simply stale. Both are swept the same way, by dropping any record older than a
 * fortnight at boot, which also keeps a shared machine (the lobby kiosk) from holding an
 * admin composer's private client name indefinitely.
 *
 * Loads in <body>, so hx-boost re-runs it on every arrival, and the re-run has to do BOTH
 * halves of the handover: hand back to the copy already loaded, and re-boot that copy
 * against the DOM that just arrived. Guarding the way composer_validation.js does, an early
 * return, does only the first half and leaves the listeners pointing at the page that left,
 * so nothing typed after an in-app navigation is ever kept. Dropping the guard does only the
 * second and stacks up a closure per arrival. Both are covered in
 * tests/e2e/class_composer_draft_spec.py, which records which scenario catches which.
 */
(function () {
    "use strict";
    if (window.plComposerDraft) {
        window.plComposerDraft.boot();
        return;
    }

    var ROOT = ".pl-composer[data-composer-draft-key]";
    var SAVED_ATTR = "data-composer-draft-saved";
    var UNSAVED_ATTR = "data-composer-draft-unsaved";
    var PENDING_KEY = "plfog.composer.pending";
    var NOTICE = "[data-composer-draft]";
    var LINE = "[data-composer-draft-line]";
    var WHEN = "[data-composer-draft-when]";
    var RESTORE = "[data-composer-draft-restore]";
    var DISCARD = "[data-composer-draft-discard]";
    var LIVE = "[data-composer-draft-live]";
    var MANAGEMENT = /-(?:TOTAL|INITIAL|MIN_NUM|MAX_NUM)_FORMS$/;
    var TEXTISH = ["text", "email", "url", "number", "tel", "search", "date", "time"];
    var DEBOUNCE_MS = 400;
    var MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
    var VERSION = 1;

    var page = null;
    var timer = null;

    // Every storage call is wrapped: reading window.localStorage itself throws in a
    // browser set to block site data, and a write throws again when the quota is full.
    function box(name) {
        try {
            return window[name];
        } catch (err) {
            return null;
        }
    }

    function read(name, key) {
        var store = box(name);
        if (!store) return null;
        try {
            return store.getItem(key);
        } catch (err) {
            return null;
        }
    }

    function forget(name, key) {
        var store = box(name);
        if (!store) return;
        try {
            store.removeItem(key);
        } catch (err) {
            /* Nothing kept, so nothing to drop. */
        }
    }

    // One retry after dropping our own entry: the quota is usually full of someone else's
    // data (htmx keeps its history cache here too), and our own previous copy is the one
    // thing we may reclaim. If the retry fails as well the old copy goes back: a good copy
    // is never traded for nothing, which would lose the very work it was holding.
    function write(name, key, value) {
        var store = box(name);
        if (!store) return false;
        try {
            store.setItem(key, value);
            return true;
        } catch (err) {
            /* Out of room; the previous copy is still there, untouched. */
        }
        var previous = read(name, key);
        forget(name, key);
        try {
            store.setItem(key, value);
            return true;
        } catch (err) {
            if (previous === null) return false;
            try {
                store.setItem(key, previous);
            } catch (err2) {
                /* The room we just freed went to someone else. Nothing left to do. */
            }
            return false;
        }
    }

    function isValues(values) {
        // A string passes a bare truthiness check and then renders an offer reading
        // "at Invalid Date" with a Restore button that does nothing. Anything that is not
        // a plain object is a record we did not write, or one we no longer understand.
        return typeof values === "object" && values !== null && !Array.isArray(values);
    }

    function load(key) {
        var raw = read("localStorage", key);
        if (!raw) return null;
        var record;
        try {
            record = JSON.parse(raw);
        } catch (err) {
            record = null;
        }
        if (!record || record.v !== VERSION || typeof record.at !== "number" || !isValues(record.values)) {
            forget("localStorage", key);
            return null;
        }
        // Nothing typed into a composer is worth holding for a fortnight, and a shared
        // machine (the lobby kiosk) would otherwise keep an admin's private client name
        // in storage forever.
        if (Date.now() - record.at > MAX_AGE_MS) {
            forget("localStorage", key);
            return null;
        }
        return record;
    }

    // The controls a save would carry and a person actually types into. See the header
    // for why hidden, file, checkbox and radio inputs are all left out.
    function fields(form) {
        var found = [];
        var all = form.querySelectorAll("input, select, textarea");
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (!el.name || el.name === "csrfmiddlewaretoken" || MANAGEMENT.test(el.name)) continue;
            if (el.tagName === "INPUT" && TEXTISH.indexOf(el.type) === -1) continue;
            found.push(el);
        }
        return found;
    }

    function snapshot(form) {
        var values = {};
        fields(form).forEach(function (el) {
            values[el.name] = el.value;
        });
        return values;
    }

    /* Only what differs from the baseline, which is what the database holds. A copy that
     * matches it restores nothing, so it is never written and never offered.
     *
     * A field the baseline says nothing about counts as empty, which is what makes the
     * empty baseline of an unsaved render (see boot) mean "keep the whole page". */
    function changes(form, baseline) {
        var values = {};
        var any = false;
        fields(form).forEach(function (el) {
            var was = Object.prototype.hasOwnProperty.call(baseline, el.name) ? baseline[el.name] : "";
            if (el.value === was) return;
            values[el.name] = el.value;
            any = true;
        });
        return any ? values : null;
    }

    function stamp(at) {
        return " at " + new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }

    /* One notice, four states: nothing to say, an offer to restore, a copy being kept,
     * and a browser that will not keep one. */
    function show(state, at) {
        if (!page || !page.notice) return;
        Array.prototype.forEach.call(page.notice.querySelectorAll(LINE), function (line) {
            line.hidden = line.getAttribute("data-composer-draft-line") !== state;
        });
        Array.prototype.forEach.call(page.notice.querySelectorAll(WHEN), function (when) {
            when.textContent = at ? stamp(at) : "";
        });
        var restore = page.notice.querySelector(RESTORE);
        var discard = page.notice.querySelector(DISCARD);
        if (restore) restore.hidden = state !== "offer";
        if (discard) discard.hidden = state !== "offer" && state !== "kept";
        page.notice.hidden = !state;
        // The notice is toggled with `hidden`, and a live region inside a hidden subtree
        // announces nothing, so the announcement is its own always present region next to
        // it, reading back whichever line is on screen. One source of copy, two audiences.
        if (page.live) {
            var line = state ? page.notice.querySelector('[data-composer-draft-line="' + state + '"]') : null;
            page.live.textContent = line ? line.textContent : "";
        }
    }

    function save() {
        timer = null;
        if (!page) return;
        var values = changes(page.form, page.baseline);
        if (!values) {
            forget("localStorage", page.key);
            show(null);
            return;
        }
        var record = { v: VERSION, at: Date.now(), values: values };
        if (write("localStorage", page.key, JSON.stringify(record))) show("kept", record.at);
        else show("blocked");
    }

    function flush() {
        if (timer) {
            window.clearTimeout(timer);
            save();
        }
    }

    /* Restoring dispatches input and change on every control it writes, so the per step
     * check (static/js/composer_validation.js) takes down any stale reason under a field
     * that is now filled in, and the Alpine root's liveTitle mirror catches the title. */
    function restore() {
        if (!page) return;
        var record = load(page.key);
        if (!record) {
            show(null);
            return;
        }
        fields(page.form).forEach(function (el) {
            if (!Object.prototype.hasOwnProperty.call(record.values, el.name)) return;
            var wanted = record.values[el.name];
            if (el.value === wanted) return;
            var had = el.value;
            el.value = wanted;
            // A dropdown whose stored option no longer exists silently blanks itself;
            // keep what the server rendered rather than posting an empty choice.
            if (el.value !== wanted) {
                el.value = had;
                return;
            }
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
        });
        // Write now rather than let the debounce those events just armed do it: the notice
        // would otherwise show the old kept time and silently change it 400ms later.
        if (timer) {
            window.clearTimeout(timer);
            timer = null;
        }
        save();
    }

    function discard() {
        if (!page) return;
        forget("localStorage", page.key);
        show(null);
    }

    function boot() {
        if (timer) {
            window.clearTimeout(timer);
            timer = null;
        }
        page = null;
        var root = document.querySelector(ROOT);
        if (!root) return;
        var form = root.querySelector("form");
        var key = root.getAttribute("data-composer-draft-key");
        if (!form || !key) return;
        /* The baseline is what the DATABASE holds, which is usually what the page renders.
         * On a render the server marked unsaved (a save it refused, re-rendered from the
         * POST) it holds none of this, so the baseline is empty and the whole page counts as
         * work to keep. Reading the rendered values as safe there would quietly shrink the
         * copy on the next keystroke: every field that matched the re-render would drop out
         * of it, and the refresh that followed would bring back only the field last edited. */
        var unsaved = root.hasAttribute(UNSAVED_ATTR);
        page = {
            root: root,
            form: form,
            key: key,
            unsaved: unsaved,
            notice: root.querySelector(NOTICE),
            live: root.querySelector(LIVE),
            baseline: unsaved ? {} : snapshot(form),
        };

        // Whatever was in flight when this page's form was submitted. Read once per
        // arrival and dropped either way: a failed save re-renders the same composer,
        // where the stored copy is still the only copy of anything typed since.
        var pending = read("sessionStorage", PENDING_KEY);
        forget("sessionStorage", PENDING_KEY);

        if (root.hasAttribute(SAVED_ATTR)) {
            forget("localStorage", key);
            // Create mode saves under one key and lands on the edit URL under another.
            if (pending && pending !== key) forget("localStorage", pending);
            show(null);
            return;
        }

        var record = load(key);
        if (!record) {
            show(null);
            return;
        }
        var differs = Object.keys(record.values).some(function (name) {
            var el = form.elements[name];
            return el && el.value !== record.values[name];
        });
        if (!differs) {
            // Nothing to offer: the page already shows every value the copy holds. The copy
            // itself STAYS. A save the server refuses re-renders the composer bound to the
            // POST, which matches the copy exactly, and that is the render where the copy is
            // the only backup there is: deleting it here would lose the work on the next
            // refresh, which is the whole thing this file exists to stop. On that render the
            // notice says so, since a page of unsaved work being backed up is worth saying.
            show(page.unsaved ? "kept" : null, record.at);
            return;
        }
        show("offer", record.at);
    }

    function schedule(event) {
        if (!page || !page.form.contains(event.target)) return;
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(save, DEBOUNCE_MS);
    }

    document.addEventListener("input", schedule);
    document.addEventListener("change", schedule);
    document.addEventListener("click", function (event) {
        if (!page || !page.notice || !page.notice.contains(event.target)) return;
        if (event.target.closest(RESTORE)) restore();
        else if (event.target.closest(DISCARD)) discard();
    });
    // The debounce is the one window where a refresh could still lose the last few
    // keystrokes, so anything that looks like leaving writes immediately.
    document.addEventListener("visibilitychange", flush);
    window.addEventListener("pagehide", flush);
    document.addEventListener("submit", function (event) {
        if (!page || event.target !== page.form) return;
        write("sessionStorage", PENDING_KEY, page.key);
    });

    window.plComposerDraft = { boot: boot, save: save, restore: restore, discard: discard };
    boot();
})();
