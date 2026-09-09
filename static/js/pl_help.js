/* Help tooltip lift.
 *
 * The .pl-help bubble is CSS only (hub.css): position: absolute under its "?" icon,
 * shown on hover and focus. Absolute positioning means any overflow: hidden or
 * overflow: auto ancestor (the .admin-table-wrap rounded corners, every scrolling
 * table) clips the bubble. This script, loaded once from hub/base.html, re-anchors
 * the bubble to the viewport (position: fixed) while it is shown, so it escapes every
 * clipping ancestor with no markup change anywhere. The phone CSS already pins the
 * bubble to the viewport gutters below 768px, so nothing here runs there.
 *
 * Delegated listeners on document only: nothing to bind per page, nothing to re-run
 * after an hx-boost swap. Plain ES5, no build step.
 */
(function () {
    "use strict";

    var PHONE_MAX_WIDTH = 768;
    var GUTTER = 12;
    var GAP = 8;
    var lifted = null;

    function closestHelp(node) {
        while (node && node !== document) {
            if (node.classList && node.classList.contains("pl-help")) { return node; }
            node = node.parentNode;
        }
        return null;
    }

    function bubbleOf(help) {
        return help.querySelector(".pl-help__bubble");
    }

    function clear() {
        if (!lifted) { return; }
        var bubble = bubbleOf(lifted);
        if (bubble) {
            bubble.style.position = "";
            bubble.style.top = "";
            bubble.style.left = "";
            bubble.style.right = "";
            bubble.style.width = "";
        }
        lifted.removeAttribute("data-help-lifted");
        lifted = null;
    }

    function lift(help) {
        if (window.innerWidth <= PHONE_MAX_WIDTH) { return; }
        var bubble = bubbleOf(help);
        if (!bubble) { return; }
        if (lifted === help) { return; }
        clear();
        var icon = help.querySelector(".pl-help__icon") || help;
        var rect = icon.getBoundingClientRect();
        var allowance = window.innerWidth - GUTTER * 2;

        /* Fixed first, then measure: the bubble is visibility: hidden (not display: none),
           so its natural width is real once it stops being clamped by the parent. */
        bubble.style.position = "fixed";
        bubble.style.right = "auto";
        bubble.style.width = "";
        bubble.style.top = (rect.bottom + GAP) + "px";
        var width = bubble.offsetWidth;
        if (width > allowance) {
            bubble.style.width = allowance + "px";
            width = allowance;
        }
        var left = rect.left;
        if (left + width > window.innerWidth - GUTTER) {
            left = rect.right - width;
        }
        if (left < GUTTER) { left = GUTTER; }
        bubble.style.left = left + "px";
        help.setAttribute("data-help-lifted", "");
        lifted = help;
    }

    function onEnter(event) {
        var help = closestHelp(event.target);
        if (help) { lift(help); }
    }

    function onLeave(event) {
        if (!lifted) { return; }
        var next = event.relatedTarget;
        if (next && closestHelp(next) === lifted) { return; }
        clear();
    }

    document.addEventListener("pointerover", onEnter);
    document.addEventListener("pointerout", onLeave);
    document.addEventListener("focusin", onEnter);
    document.addEventListener("focusout", onLeave);
    /* A lifted bubble is anchored to where the icon WAS; hide it rather than chase it. */
    document.addEventListener("scroll", clear, true);
    window.addEventListener("resize", clear);
})();
