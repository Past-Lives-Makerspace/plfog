/* Admin placement editor for the space map.
 *
 * Two jobs, both plain DOM (no dependency, no build step):
 *
 *  1. Drag a marker (or drag out a box for a region) on the floor plan and POST the
 *     new {x, y, w, h} — percentages of the natural image — to the marker's position
 *     endpoint. Mirrors hero_cropper.js's shape: a permission-gated JSON save that
 *     touches only the coordinate columns, so it can never fight the structural
 *     formset below it.
 *  2. Clone the "+ Add" formset rows, and keep drag-and-drop image upload alive on
 *     cloned rows (cloned innerHTML never runs its own <script>, so the drop zones
 *     are driven from one delegated listener here).
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

    // ── Click-to-set-status ──────────────────────────────────────────────
    // A studio marker (one bound to a Space, marked with data-space-status) opens an
    // inline status control on a click or keyboard activation — distinct from a drag,
    // which is a pointer move. Picking a status POSTs to the marker's status endpoint;
    // Airtable is the system of record, so the endpoint pushes the change back there.

    var STATUS_CLASSES = ['available', 'occupied', 'maintenance', 'info'];

    function recolour(marker, status) {
        STATUS_CLASSES.forEach(function (name) {
            marker.classList.remove('pl-map-marker--' + name);
        });
        marker.classList.add('pl-map-marker--' + (status || 'info'));
        marker.setAttribute('data-space-status', status);
    }

    function highlightCurrent(control, status) {
        control.querySelectorAll('[data-set-status]').forEach(function (button) {
            var isCurrent = button.getAttribute('data-set-status') === status;
            button.classList.toggle('is-current', isCurrent);
            button.setAttribute('aria-pressed', isCurrent ? 'true' : 'false');
        });
    }

    function openStatusControl(root, marker) {
        var control = root.querySelector('[data-status-control]');
        if (!control) return;
        control.hidden = false;
        control.setAttribute('data-for-marker', marker.getAttribute('data-editor-marker'));
        var code = control.querySelector('[data-status-control-code]');
        if (code) code.textContent = marker.getAttribute('data-code') || '';
        highlightCurrent(control, marker.getAttribute('data-space-status'));
        var first = control.querySelector('[data-set-status]');
        if (first) first.focus();
    }

    function postStatus(root, marker, control, status) {
        var template = root.getAttribute('data-status-url-template') || '';
        var url = template.replace(/0\/status\/$/, marker.getAttribute('data-editor-marker') + '/status/');
        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': root.getAttribute('data-csrf') || ''
            },
            body: 'status=' + encodeURIComponent(status)
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok) {
                    setStatus(root, result.data.error || "That status couldn't be saved.", true);
                    return;
                }
                recolour(marker, result.data.availability_class);
                highlightCurrent(control, result.data.availability_class);
                if (result.data.warning) {
                    setStatus(root, result.data.warning, true);
                } else {
                    setStatus(root, 'Status set to ' + result.data.status_display + '.', false);
                }
            })
            .catch(function () {
                setStatus(root, "That status couldn't be saved — check your connection.", true);
            });
    }

    function initStatusControl(root) {
        var control = root.querySelector('[data-status-control]');
        if (!control) return;
        control.addEventListener('click', function (event) {
            var button = event.target.closest ? event.target.closest('[data-set-status]') : null;
            if (!button) return;
            var markerId = control.getAttribute('data-for-marker');
            var marker = root.querySelector('[data-editor-marker="' + markerId + '"]');
            if (!marker) return;
            postStatus(root, marker, control, button.getAttribute('data-set-status'));
        });
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
                // A click, not a drag: offer the status control for a space-bound marker,
                // and never re-save an unchanged position.
                if (marker.getAttribute('data-space-status') !== null) openStatusControl(root, marker);
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

        // Keyboard: Enter/Space on a focused studio marker opens the status control
        // (pointer clicks are handled on pointerup above, so this never double-fires).
        stage.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
            var marker = event.target.closest ? event.target.closest('[data-editor-marker]') : null;
            if (!marker || marker.getAttribute('data-space-status') === null) return;
            event.preventDefault();
            openStatusControl(root, marker);
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
            initStatusControl(root);
        });
        initAddButtons();
        initDropZones();
    });
})();
