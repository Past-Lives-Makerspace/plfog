/* Per step validation for the class composer (issue #368, item 1).
 *
 * The composer is one <form> with every step's fields in the DOM and one
 * pane on screen at a time. The browser's own required check cannot point at
 * a control inside a hidden pane (it refuses silently and logs "not
 * focusable"), so the form carries novalidate and this file does the pointing.
 * The Alpine root in templates/classes/_components/class_composer.html calls
 * into it from Next (the current step), and from Save Draft and the Submit /
 * Publish confirm (every step, in order). Back and the step tabs never call it.
 *
 * There is no list of fields here, on purpose. A step is the pane stamped
 * data-composer-step="N", its fields are whatever the server rendered inside
 * it, and the rules are the attributes Django put on those controls
 * (required, min, max, step, maxlength, type). The constraint validation API
 * reads those whether or not the form has novalidate, so a form change
 * changes what Next enforces with no second list to keep in step. The
 * step map stays in classes/composer.py, once.
 *
 * Formset rows (gallery, sessions, FAQ) carry no constraint attributes: Django
 * builds every formset form with use_required_attribute=False, and the
 * scheduler posts its rows as hidden inputs. So an added row left blank never
 * blocks Next, and a half filled one is the server's to refuse at save, on
 * the step that owns it, as it always was.
 *
 * A refusal renders the repo's field error markup (components/form_field.html)
 * right under the control, with aria-invalid and aria-describedby on the
 * control, and clears the moment the control validates again. A control inside
 * a collapsed section asks that section to open first (composer-reveal-field,
 * answered by collapsible_field.html), and focus waits two frames because
 * Alpine reveals an x-show pane on the next animation frame.
 *
 * Loads in <body>, so hx-boost re-runs it on every arrival; the guard keeps
 * one copy and one pair of document listeners.
 */
(function () {
    "use strict";
    if (window.plComposerValidation) return;

    var PANE = "[data-composer-step]";
    var CONTROLS = "input, select, textarea";
    var LIVE_ATTR = "data-live-invalid";
    var LIST_ATTR = "data-live-error";
    var REVEAL_EVENT = "composer-reveal-field";
    var REQUIRED_MESSAGE = "This field is required.";
    var counter = 0;

    function firstInvalid(pane) {
        var all = pane.querySelectorAll(CONTROLS);
        for (var i = 0; i < all.length; i++) {
            if (all[i].willValidate && !all[i].checkValidity()) return all[i];
        }
        return null;
    }

    // The first control, in step order, the server would refuse: every pane
    // under `root`, or only the pane numbered `only`.
    function firstInvalidStep(root, only) {
        var panes = root.querySelectorAll(PANE);
        for (var i = 0; i < panes.length; i++) {
            var step = Number(panes[i].getAttribute("data-composer-step"));
            if (only !== undefined && step !== only) continue;
            var control = firstInvalid(panes[i]);
            if (control) return { step: step, control: control };
        }
        return null;
    }

    function message(el) {
        return el.validity.valueMissing ? REQUIRED_MESSAGE : el.validationMessage;
    }

    // The reason goes right after the control, where form_field.html puts the
    // server's errors. A radio or checkbox sits inside its own <label>, so the
    // reason goes after the group that label belongs to.
    function anchorFor(el) {
        if (el.type === "radio" || el.type === "checkbox") {
            var label = el.closest("label");
            if (label && label.parentElement) return label.parentElement;
        }
        return el;
    }

    function errorId(el) {
        if (!el.id) el.id = "pl-live-control-" + (++counter);
        return el.id + "-error";
    }

    function tokens(el, attr) {
        return (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    }

    function addToken(el, attr, token) {
        var list = tokens(el, attr);
        if (list.indexOf(token) === -1) list.push(token);
        el.setAttribute(attr, list.join(" "));
    }

    function removeToken(el, attr, token) {
        var list = tokens(el, attr).filter(function (t) { return t !== token; });
        if (list.length) el.setAttribute(attr, list.join(" ")); else el.removeAttribute(attr);
    }

    function listFor(el) {
        return document.querySelector("[" + LIST_ATTR + '="' + errorId(el) + '"]');
    }

    function afterReveal(callback) {
        window.requestAnimationFrame(function () { window.requestAnimationFrame(callback); });
    }

    function flag(el) {
        var id = errorId(el);
        var list = listFor(el);
        if (!list) {
            list = document.createElement("ul");
            list.className = "pl-field-errors";
            list.setAttribute(LIST_ATTR, id);
            var item = document.createElement("li");
            item.className = "pl-field-error";
            item.id = id;
            item.setAttribute("role", "alert");
            list.appendChild(item);
            anchorFor(el).insertAdjacentElement("afterend", list);
        }
        list.firstElementChild.textContent = message(el);
        el.setAttribute("aria-invalid", "true");
        el.setAttribute(LIVE_ATTR, "");
        addToken(el, "aria-describedby", id);
        el.dispatchEvent(new CustomEvent(REVEAL_EVENT, { bubbles: true }));
        afterReveal(function () {
            el.focus({ preventScroll: true });
            el.scrollIntoView({ block: "center" });
        });
    }

    function clear(el) {
        var list = listFor(el);
        if (list) list.remove();
        el.removeAttribute("aria-invalid");
        el.removeAttribute(LIVE_ATTR);
        removeToken(el, "aria-describedby", errorId(el));
    }

    // Every flagged control in the form that validates again loses its reason.
    // Per form, not per target: a radio group's change fires on the radio just
    // picked, which is not the one carrying the flag.
    function reconcile(event) {
        var form = event.target && event.target.form;
        if (!form) return;
        Array.prototype.forEach.call(form.querySelectorAll("[" + LIVE_ATTR + "]"), function (el) {
            if (el.checkValidity()) clear(el);
        });
    }

    document.addEventListener("input", reconcile);
    document.addEventListener("change", reconcile);

    window.plComposerValidation = {
        firstInvalid: firstInvalid,
        firstInvalidStep: firstInvalidStep,
        flag: flag,
        clear: clear,
    };
})();
