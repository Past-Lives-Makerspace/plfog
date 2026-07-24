/* Admin placement editor for the space map.
 *
 * Plain DOM, no build step. The editor owns the pointer on the drawn canvas:
 *
 *  1. Drag a tile (or Shift-drag a corner of a region) and POST the new {x, y, w, h} —
 *     percentages of the canvas — to the marker's position endpoint. Mirrors hero_cropper.js:
 *     a permission-gated JSON save that touches only the coordinate columns, so it can never
 *     fight the modal editor.
 *  2. Click a tile (a pointer-up with no drag) to open the "Edit marker" modal — its status
 *     and every detail members see — loaded over htmx. "+ Add a marker" creates a centred tile
 *     and opens the same modal. A saved/created/deleted tile swaps itself on the map out-of-band.
 *  3. Keep drag-and-drop image upload alive on cloned floor rows (cloned innerHTML never runs
 *     its own <script>, so the drop zones are driven from one delegated listener here).
 */
(function () {
    'use strict';

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

    function editUrl(root, marker) {
        var template = root.getAttribute('data-edit-url-template') || '';
        return template.replace(/0\/edit\/$/, marker.getAttribute('data-editor-marker') + '/edit/');
    }

    function initStage(root) {
        var stage = root.querySelector('[data-editor-stage]');
        if (!stage) return;
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
                // A click, not a drag: open the tile's editor, and never re-save an unchanged position.
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
        document.querySelectorAll('[data-add-marker]').forEach(function (button) {
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

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.pl-map-editor').forEach(function (root) {
            initStage(root);
            initAddMarker(root);
        });
        initAddButtons();
        initDropZones();
        // A saved or deleted marker answers with an HX-Trigger that closes the modal.
        document.body.addEventListener('close-marker-edit', function () {
            window.dispatchEvent(new CustomEvent('close-modal', { detail: 'marker-edit' }));
        });
    });
})();
