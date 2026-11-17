/* Report pages' tooltip (FRONTEND.md, Report Pages; first on the kiln Reports tab, #1234).
 *
 * Anything carrying data-tip-value (a split segment, a ranked row) shows one floating tip on hover
 * or keyboard focus: the value in bold, then data-tip-name, then data-tip-more, filled with
 * textContent only. It enhances; every value is also printed in the row.
 *
 * Loaded from the page body, so it re-runs on every boosted arrival: the document listeners are
 * bound once (a flag on window), and the tip element is looked up each time because a body swap
 * replaces it.
 */
(function () {
    if (window.plReportTipBound) return;
    window.plReportTipBound = true;

    function tipElement() {
        var tip = document.querySelector(".pl-report-tip");
        if (!tip) {
            tip = document.createElement("div");
            tip.className = "pl-report-tip";
            tip.setAttribute("role", "tooltip");
            document.body.appendChild(tip);
        }
        return tip;
    }

    function line(className, text) {
        var div = document.createElement("div");
        if (className) div.className = className;
        div.textContent = text || "";
        return div;
    }

    function show(target, x, y) {
        var tip = tipElement();
        tip.style.setProperty("--pl-c", getComputedStyle(target).getPropertyValue("--pl-c"));
        tip.replaceChildren(
            line("pl-report-tip__value", target.dataset.tipValue),
            line("pl-report-tip__name", target.dataset.tipName),
            line(null, target.dataset.tipMore)
        );
        tip.classList.add("is-on");
        var width = tip.offsetWidth;
        var height = tip.offsetHeight;
        tip.style.left = Math.max(8, Math.min(x + 14, window.innerWidth - width - 8)) + "px";
        tip.style.top = Math.max(8, y - height - 12 < 8 ? y + 18 : y - height - 12) + "px";
    }

    function hide() {
        var tip = document.querySelector(".pl-report-tip");
        if (tip) tip.classList.remove("is-on");
    }

    document.addEventListener("pointermove", function (event) {
        var target = event.target.closest && event.target.closest("[data-tip-value]");
        if (target) show(target, event.clientX, event.clientY);
        else hide();
    });
    document.addEventListener("focusin", function (event) {
        var target = event.target.closest && event.target.closest("[data-tip-value]");
        if (!target) return;
        var box = target.getBoundingClientRect();
        show(target, box.left + Math.min(box.width / 2, 160), box.top);
    });
    document.addEventListener("focusout", hide);
    document.addEventListener("htmx:beforeSwap", hide);
})();
