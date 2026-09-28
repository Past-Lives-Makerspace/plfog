/* Live count for the class description (issue #425).
 *
 * The readiness rule (description_length in classes/models.py) counts a description with its
 * whitespace runs collapsed and its ends trimmed, and nothing else dropped. Until #425 the box
 * gave no number at all: a 30 character description was refused with a hint that read as if
 * nothing had been typed, and nothing on the page said how much was needed. This paints
 * "N of M characters" under the box as the person types, and a short "long enough" line once N
 * reaches the minimum M.
 *
 * The rule is mirrored here, not restated: the same normalisation, and the count is code points
 * (Array.from), because Python's len counts code points and .length would count one emoji as two.
 * classes/spec/models/class_readiness_spec.py pins that this file carries the same expression.
 * The minimum is never written here: the template stamps it on the counter (data-description-min)
 * from READINESS_MIN_DESCRIPTION_CHARS, so one constant is the number everywhere.
 *
 * The box to count is named by the template too (data-description-for, the textarea's id), so
 * this file knows no field name; the composer keeps its field map in classes/composer.py.
 *
 * Loads in <body>, so hx-boost re-runs it on every arrival (FRONTEND.md, Scripts under hx-boost).
 * The first copy keeps the one document listener and hands every later run back to boot(), which
 * paints the DOM that just arrived. The listener looks its counters up on every input event, so
 * it never points at a page that has left.
 */
(function () {
    "use strict";
    if (window.plDescriptionCount) {
        window.plDescriptionCount.boot();
        return;
    }

    var COUNTER = "[data-description-count]";
    var FOR_ATTR = "data-description-for";
    var MIN_ATTR = "data-description-min";

    /* Mirrors description_length (classes/models.py) exactly: trim, collapse whitespace, count code points. */
    function length(text) {
        return Array.from(text.trim().split(/\s+/).filter(Boolean).join(" ")).length;
    }

    function paint(counter, field) {
        var min = parseInt(counter.getAttribute(MIN_ATTR), 10);
        var n = length(field.value);
        counter.textContent = n < min ? n + " of " + min + " characters" : n + " characters. Long enough.";
    }

    function counters() {
        return document.querySelectorAll(COUNTER);
    }

    function boot() {
        Array.prototype.forEach.call(counters(), function (counter) {
            var field = document.getElementById(counter.getAttribute(FOR_ATTR));
            if (field) paint(counter, field);
        });
    }

    document.addEventListener("input", function (event) {
        var target = event.target;
        if (!target || !target.id) return;
        Array.prototype.forEach.call(counters(), function (counter) {
            if (counter.getAttribute(FOR_ATTR) === target.id) paint(counter, target);
        });
    });

    window.plDescriptionCount = { boot: boot, length: length };
    boot();
})();
