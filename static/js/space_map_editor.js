/* Admin placement editor for the space map.
 *
 * Plain DOM, no build step. The editor owns the pointer on the drawn canvas:
 *
 *  1. Drag a tile (or Shift-drag a corner of a region) and POST the new {x, y, w, h} —
 *     percentages of the canvas — to the marker's position endpoint. Mirrors hero_cropper.js:
 *     a permission-gated JSON save that touches only the coordinate columns, so it can never
 *     fight the modal editor.
 *  2. Click an available or reserved tile (a pointer-up with no drag) to toggle its
 *     availability the other way — available <-> reserved — a quick, reversible status flip
 *     (click again to undo) that writes through to Airtable. Alt-click (or keyboard
 *     Enter/Space) opens the full "Edit marker" modal instead; a click on any other status
 *     opens that modal directly. "+ Add a marker" creates a centred tile and opens the same
 *     modal. A saved/created/deleted tile swaps itself on the map out-of-band.
 *  3. Keep drag-and-drop image upload alive on cloned floor rows (cloned innerHTML never runs
 *     its own <script>, so the drop zones are driven from one delegated listener here).
 *
 * Boot contract (issue #382). This file is loaded from <body>, and hx-boost swaps the body's
 * *contents*, so htmx re-runs this whole IIFE on every boosted arrival with a fresh scope.
 * That splits the wiring in two, and the split is the design:
 *
 *  - Bound to markup the swap replaced (initStage, initAddMarker, initAddButtons): must
 *    re-run each arrival, and must not double-wire a node if it runs twice against the same
 *    markup. The guard is a ready key on the node itself — rich-editor-init.js's readyOnce,
 *    same idea and, since #383, the same property-not-attribute reason.
 *  - Bound to document or document.body (initDropZones, the close-marker-edit listener):
 *    those nodes outlive a boosted swap, so re-running stacks a duplicate listener every
 *    visit. They run exactly once per document, flagged on window — a flag in this IIFE's
 *    scope cannot express "once", because the scope is rebuilt on every arrival.
 */
(function () {
    'use strict';

    /* Claim a node for one binding. False when this node was already wired, which is what
     * makes the per-arrival inits safe to run twice against the same markup. A swap brings
     * fresh nodes carrying no key, so they wire up normally.
     *
     * The key is a property on the element, deliberately NOT a data- attribute. htmx caches
     * a history snapshot by serializing the body's innerHTML, and an attribute would be
     * captured in it: on Back the restored markup would arrive pre-stamped, boot() would
     * find every node already claimed, and the editor would come back dead. That is the
     * exact bug this file is fixing, reintroduced one navigation later. A property lives on
     * the element object rather than in its markup, so it never serializes and dies with the
     * node it belongs to.
     */
    function readyOnce(element, key) {
        if (element[key]) return false;
        element[key] = true;
        return true;
    }

    function percent(value) {
        return Math.round(Math.min(100, Math.max(0, value)) * 100) / 100;
    }

    function setStatus(root, message, isError) {
        var status = root.querySelector('[data-editor-status]');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('pl-map-editor__status--error', !!isError);
    }

    function save(root, marker, box) {
        var template = root.getAttribute('data-position-url-template') || '';
        var url = template.replace(/0\/position\/$/, marker.getAttribute('data-editor-marker') + '/position/');
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': root.getAttribute('data-csrf') || '' },
            body: JSON.stringify(box)
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok) {
                    setStatus(root, result.data.error || "That position couldn't be saved.", true);
                    return;
                }
                setStatus(root, 'Position saved.', false);
            })
            .catch(function () {
                setStatus(root, "That position couldn't be saved — check your connection.", true);
            });
    }

    // ── Click-to-edit ────────────────────────────────────────────────────
    // A click (not a drag) on any tile loads its editor into the modal body over htmx, then
    // opens the modal. The modal form owns saving, status, and delete; a saved/deleted tile
    // comes back as an out-of-band swap, so the map stays in sync without a reload.

    function openEditor(url) {
        window.htmx.ajax('GET', url, { target: '#marker-edit-body', swap: 'innerHTML' }).then(function () {
            window.dispatchEvent(new CustomEvent('open-modal', { detail: 'marker-edit' }));
        });
    }

    function updateSpaceStatus(root, marker, newStatus) {
        var prevStatus = marker.getAttribute('data-status');
        var url = editUrl(root, marker).replace('/edit/', '/status/');
        var formData = new URLSearchParams();
        formData.append('status', newStatus);

        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': root.getAttribute('data-csrf') || ''
            },
            body: formData.toString()
        })
        .then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok, data: data };
            });
        })
        .then(function (result) {
            if (!result.ok) {
                setStatus(root, result.data.error || "Status couldn't be updated.", true);
                return;
            }
            setStatus(root, 'Status updated to ' + newStatus + '.', false);
            marker.setAttribute('data-status', newStatus);
            if (prevStatus) marker.classList.remove('pl-map-marker--' + prevStatus);
            marker.classList.add('pl-map-marker--' + newStatus);
        })
        .catch(function () {
            setStatus(root, "Status couldn't be updated — check your connection.", true);
        });
    }

    function editUrl(root, marker) {
        var template = root.getAttribute('data-edit-url-template') || '';
        return template.replace(/0\/edit\/$/, marker.getAttribute('data-editor-marker') + '/edit/');
    }

    function initStage(root) {
        var stage = root.querySelector('[data-editor-stage]');
        if (!stage) return;
        if (!readyOnce(stage, 'plStageReady')) return;
        var active = null;
        var mode = '';
        var startX = 0;
        var startY = 0;
        var originLeft = 0;
        var originTop = 0;
        var moved = false;

        function stageBox() {
            return stage.getBoundingClientRect();
        }

        stage.addEventListener('pointerdown', function (event) {
            var marker = event.target.closest ? event.target.closest('[data-editor-marker]') : null;
            if (!marker) return;
            event.preventDefault();
            active = marker;
            moved = false;
            mode = event.shiftKey && marker.getAttribute('data-shape') === 'region' ? 'resize' : 'move';
            var rect = stageBox();
            startX = ((event.clientX - rect.left) / rect.width) * 100;
            startY = ((event.clientY - rect.top) / rect.height) * 100;
            originLeft = parseFloat(marker.style.left) || 0;
            originTop = parseFloat(marker.style.top) || 0;
            stage.setPointerCapture(event.pointerId);
        });

        stage.addEventListener('pointermove', function (event) {
            if (!active) return;
            var rect = stageBox();
            var nowX = ((event.clientX - rect.left) / rect.width) * 100;
            var nowY = ((event.clientY - rect.top) / rect.height) * 100;
            if (Math.abs(nowX - startX) > 0.5 || Math.abs(nowY - startY) > 0.5) moved = true;
            if (mode === 'resize') {
                active.style.width = percent(nowX - originLeft) + '%';
                active.style.height = percent(nowY - originTop) + '%';
                return;
            }
            active.style.left = percent(originLeft + (nowX - startX)) + '%';
            active.style.top = percent(originTop + (nowY - startY)) + '%';
        });

        stage.addEventListener('pointerup', function (event) {
            if (!active) return;
            var marker = active;
            active = null;
            if (stage.releasePointerCapture) stage.releasePointerCapture(event.pointerId);
            if (!moved) {
                // A click, not a drag (never re-saves an unchanged position). A plain click on an
                // available/reserved tile flips its availability the other way — reversible, click
                // again to undo. Alt-click (or keyboard) opens the full editor instead; any other
                // status opens the editor directly.
                var status = marker.getAttribute('data-status');
                if (!event.altKey && (status === 'available' || status === 'reserved')) {
                    updateSpaceStatus(root, marker, status === 'available' ? 'reserved' : 'available');
                    return;
                }
                openEditor(editUrl(root, marker));
                return;
            }
            var box = {
                x: percent(parseFloat(marker.style.left) || 0),
                y: percent(parseFloat(marker.style.top) || 0)
            };
            if (marker.getAttribute('data-shape') === 'region') {
                box.w = percent(parseFloat(marker.style.width) || 0);
                box.h = percent(parseFloat(marker.style.height) || 0);
            }
            save(root, marker, box);
        });

        // Keyboard: Enter/Space on a focused tile opens its editor (pointer clicks are handled
        // on pointerup above, so this never double-fires).
        stage.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
            var marker = event.target.closest ? event.target.closest('[data-editor-marker]') : null;
            if (!marker) return;
            event.preventDefault();
            openEditor(editUrl(root, marker));
        });
    }

    // "+ Add a marker" creates a centred tile on the current floor and opens its editor.
    function initAddMarker(root) {
        // Keyed on the button, not the root: this queries the whole document, so with two
        // .pl-map-editor roots on a page the second pass would otherwise bind each button
        // again.
        document.querySelectorAll('[data-add-marker]').forEach(function (button) {
            if (!readyOnce(button, 'plAddMarkerReady')) return;
            button.addEventListener('click', function () {
                var url = root.getAttribute('data-create-url');
                var floorId = root.getAttribute('data-floor-id');
                window.htmx
                    .ajax('POST', url, { target: '#marker-edit-body', swap: 'innerHTML', values: { floor_id: floorId } })
                    .then(function () {
                        window.dispatchEvent(new CustomEvent('open-modal', { detail: 'marker-edit' }));
                    });
            });
        });
    }

    function initAddButtons() {
        document.querySelectorAll('[data-add-row]').forEach(function (button) {
            if (!readyOnce(button, 'plAddRowReady')) return;
            button.addEventListener('click', function () {
                var which = button.getAttribute('data-add-row');
                var prefix = which === 'floor' ? 'floors' : 'markers';
                var template = document.getElementById(which + '-empty-template');
                var total = document.getElementById('id_' + prefix + '-TOTAL_FORMS');
                var rows = document.getElementById(which === 'floor' ? 'floor-rows' : 'marker-rows');
                if (!template || !total || !rows) return;
                var index = parseInt(total.value, 10);
                var wrapper = document.createElement('div');
                wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', index);
                rows.appendChild(wrapper.firstElementChild);
                total.value = index + 1;
            });
        });
    }

    // Delegated drag-and-drop for every image drop zone, cloned rows included.
    function initDropZones() {
        document.addEventListener('dragover', function (event) {
            var zone = event.target.closest ? event.target.closest('.cls-image-upload-zone') : null;
            if (!zone) return;
            event.preventDefault();
            zone.classList.add('drag-hover');
        });
        document.addEventListener('dragleave', function (event) {
            var zone = event.target.closest ? event.target.closest('.cls-image-upload-zone') : null;
            if (zone) zone.classList.remove('drag-hover');
        });
        document.addEventListener('drop', function (event) {
            var zone = event.target.closest ? event.target.closest('.cls-image-upload-zone') : null;
            if (!zone) return;
            event.preventDefault();
            zone.classList.remove('drag-hover');
            var input = zone.querySelector('input[type="file"]');
            var file = event.dataTransfer && event.dataTransfer.files[0];
            if (input && file && file.type.indexOf('image/') === 0) {
                input.files = event.dataTransfer.files;
                input.dispatchEvent(new Event('change'));
            }
        });
    }

    /* The once-per-document half. document and document.body both survive a boosted swap, so
     * everything here binds exactly once for the life of the document. The flag has to live
     * somewhere that outlives a re-execution of this file, and window is where
     * rich-editor-init.js keeps plRteInitAll for the same reason.
     */
    function initDocumentOnce() {
        if (window.plMapEditorDocumentBound) return;
        window.plMapEditorDocumentBound = true;
        initDropZones();
        // A saved or deleted marker answers with an HX-Trigger that closes the modal.
        document.body.addEventListener('close-marker-edit', function () {
            window.dispatchEvent(new CustomEvent('close-modal', { detail: 'marker-edit' }));
        });
    }

    function boot() {
        document.querySelectorAll('.pl-map-editor').forEach(function (root) {
            initStage(root);
            initAddMarker(root);
        });
        initAddButtons();
        initDocumentOnce();
    }

    /* DOMContentLoaded fired once, on the first document, and never fires again under
     * hx-boost, so waiting on it alone left the editor inert on every boosted arrival
     * (issue #382). Run straight away when the document is already parsed — true both for
     * this file's own defer on a hard load and for htmx re-running it after a swap — and
     * wait only when it genuinely has not fired yet. Mirrors rich-editor-init.js.
     */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
