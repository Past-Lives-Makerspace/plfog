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
 * A control with no name is skipped for the same reason: the browser never posts
 * it, so no server rule can reach it and no save can be refused for it. That is
 * what the scheduler's own date, time and duration pickers are (they write the
 * hidden sessions-N-* inputs), and a half typed date in that picker reports
 * badInput, which would otherwise hold Next on a step the server would take.
 *
 * Formset rows (gallery, sessions, FAQ) carry no constraint attributes: Django
 * builds every formset form with use_required_attribute=False, and the
 * scheduler posts its rows as hidden inputs. So an added row left blank never
 * blocks Next, and a half filled one is the server's to refuse at save, on
 * the step that owns it, as it always was. The one named exception is the
 * gallery's minimum of one photo (emptyGallery below, issue #424): no attribute
 * can say "at least one row", so the template marks the container and Next,
 * only Next, counts the cards inside it.
 *
 * A refusal renders the repo's field error markup (components/form_field.html)
 * right under the control, with aria-invalid and aria-describedby on the
 * control, and clears the moment the control validates again. A message the
 * server already rendered under that same control is taken down first, so one
 * reason is on screen at a time and one aria-invalid owns it. A control inside
 * a collapsed section asks that section to open first (composer-reveal-field,
 * answered by collapsible_field.html), and focus waits two frames because
 * Alpine reveals an x-show pane on the next animation frame.
 *
 * Loads in <body>, so hx-boost re-runs it on every arrival; the guard keeps
 * one copy and one set of document listeners.
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
    var GALLERY_ATTR = "data-composer-gallery";
    var GALLERY_MESSAGE_ATTR = "data-composer-gallery-message";
    var GALLERY_CARD = ".cls-image-cell";
    var GALLERY_EVENT = "composer-gallery-changed";
    var counter = 0;

    // The rule this walk enforces: hold the step only where the value the browser
    // would post is one the server would refuse. An unnamed control posts nothing,
    // so it can never be that, and it is skipped. That takes the scheduler's date,
    // time and duration pickers out of the walk (session_calendar.html: pure Alpine
    // UI that writes the hidden sessions-N-* inputs, and a half typed date there
    // reports badInput), along with every other helper control on a pane.
    //
    // One named control is still stricter than the server: the Details step's
    // optional whole number box. Type "1e" into it and the browser posts a blank,
    // which the form accepts, while reporting badInput, so this holds the step.
    // Left that way deliberately. The characters the person typed are going to be
    // dropped on save either way, and saying so beside the box beats discarding
    // them in silence. (No field named here on purpose: the guard spec keeps every
    // field name out of this file, so the step map stays the only mapping.)
    function firstInvalid(pane) {
        var all = pane.querySelectorAll(CONTROLS);
        for (var i = 0; i < all.length; i++) {
            if (!all[i].name) continue;
            if (all[i].willValidate && !all[i].checkValidity()) return all[i];
        }
        return null;
    }

    function isGallery(el) {
        return el.hasAttribute(GALLERY_ATTR);
    }

    // The one rule here that is not an attribute (issue #424): the gallery needs at
    // least one photo before the Photos step lets Next through. No constraint
    // attribute on any control can say "at least one row". On a saved class the file
    // input posts nothing (the upload is a fetch, and the card lands later), on a new
    // class it carries the picked files until save, and `required` on either would
    // refuse Save Draft too. So the template stamps the gallery container with
    // data-composer-gallery, keeps the refusal on the same element (copy stays in the
    // template, never here), and this counts the cards inside it.
    //
    // Next only. firstInvalidStep runs it when `only` is given; Save Draft and the
    // submit confirm (validateAll, no `only`) never do: a draft may be incomplete, and
    // Submit already has the server's readiness check plus a disabled button while
    // the class is unready. The server stays the gate; this is an earlier refusal.
    function emptyGallery(pane) {
        var gallery = pane.querySelector("[" + GALLERY_ATTR + "]");
        return gallery && !gallery.querySelector(GALLERY_CARD) ? gallery : null;
    }

    // The first control, in step order, the server would refuse: every pane
    // under `root`, or only the pane numbered `only` (where the gallery minimum
    // is also checked, after the attribute walk).
    function firstInvalidStep(root, only) {
        var panes = root.querySelectorAll(PANE);
        for (var i = 0; i < panes.length; i++) {
            var step = Number(panes[i].getAttribute("data-composer-step"));
            if (only !== undefined && step !== only) continue;
            var control = firstInvalid(panes[i]);
            if (!control && only !== undefined) control = emptyGallery(panes[i]);
            if (control) return { step: step, control: control };
        }
        return null;
    }

    // A gallery container has no validity of its own; its reason is the attribute the
    // template put on it. Everything else flag() does (the list, the tokens, the focus)
    // is the same for it as for a control.
    function message(el) {
        if (isGallery(el)) return el.getAttribute(GALLERY_MESSAGE_ATTR);
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

    // A bounced save renders Django's own .pl-field-errors under the control
    // (components/form_field.html, and the two hand written field templates that
    // copy it) and adds an aria-describedby token of its own, `<id>_error`. The
    // live reason replaces that rather than stacking above it: two lists under
    // one control is two messages fighting over one aria-invalid, and clear()
    // would then take the flag off while the server's message stayed on screen.
    // Scope is the anchor's own wrapper, which holds exactly one field. The next
    // server render brings the server's message back, as it always did.
    function dropServerErrors(el) {
        var parent = anchorFor(el).parentElement;
        if (!parent) return;
        Array.prototype.forEach.call(Array.prototype.slice.call(parent.children), function (node) {
            if (node.classList.contains("pl-field-errors") && !node.hasAttribute(LIST_ATTR)) node.remove();
        });
        removeToken(el, "aria-describedby", el.id + "_error");
    }

    function flag(el) {
        var id = errorId(el);
        dropServerErrors(el);
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
            // A flagged gallery container has no validity to re-check; reconcileGallery owns it.
            if (!isGallery(el) && el.checkValidity()) clear(el);
        });
    }

    // The gallery clears on its own event, which image_formset.html's updateCount()
    // dispatches on the container after every add or remove. Not on the file input's
    // change: on a saved class the upload is a fetch, and the card lands after that
    // event has come and gone.
    function reconcileGallery(event) {
        var gallery = event.target;
        // Only the gallery scripts dispatch this, on their container; a dispatcher on
        // document or a text node would have no attributes to read.
        if (!(gallery instanceof Element)) return;
        if (gallery.hasAttribute(LIVE_ATTR) && gallery.querySelector(GALLERY_CARD)) clear(gallery);
    }

    document.addEventListener("input", reconcile);
    document.addEventListener("change", reconcile);
    document.addEventListener(GALLERY_EVENT, reconcileGallery);

    window.plComposerValidation = {
        firstInvalid: firstInvalid,
        firstInvalidStep: firstInvalidStep,
        flag: flag,
        clear: clear,
    };
})();
