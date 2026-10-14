/* The Add Inquiry member picker on Instructor Inquiries (#805).
 *
 * Alpine component `inquiryMemberPicker`, on the wrapper around the Member field of the
 * Add Inquiry modal (templates/classes/admin/partials/inquiry_add_form.html). The field is
 * a search box over a scrolling list of every member who may ask, drawn by the page as
 * role="option" rows (a native datalist is drawn by the browser, and Safari's cannot be
 * scrolled). Typing hides the rows that do not match, spaces collapsed and case ignored;
 * clicking a row, or Enter on the row the arrow keys moved to, puts its label in the box,
 * which is the value that posts, and marks the row selected. Enter in the box never
 * submits the form. On load the list narrows to whatever the box already holds, so a
 * failed save comes back with the picked member still shown.
 *
 * Loaded once, deferred, from hub/base.html's <head> with the other Alpine components
 * (FRONTEND.md, Scripts under hx-boost).
 */
(function () {
    "use strict";

    const normalise = (text) => text.split(/\s+/).filter(Boolean).join(" ").toLowerCase();

    const registerComponent = () => {
        window.Alpine.data("inquiryMemberPicker", () => ({
            active: null,

            init() {
                this.filter();
            },

            input() {
                return this.$root.querySelector("[data-member-picker-input]");
            },

            options() {
                return Array.from(this.$root.querySelectorAll("[data-member-option]"));
            },

            shown() {
                return this.options().filter((option) => !option.hidden);
            },

            /* Hide the rows that do not match the box, and mark the one it names. */
            filter() {
                const query = normalise(this.input().value);
                this.options().forEach((option) => {
                    const label = normalise(option.dataset.memberOption);
                    option.hidden = query !== "" && !label.includes(query);
                    option.setAttribute("aria-selected", query !== "" && label === query ? "true" : "false");
                });
                this.$root.querySelector("[data-member-picker-none]").hidden = this.shown().length > 0;
                this.setActive(null);
            },

            setActive(option) {
                if (this.active) {
                    this.active.classList.remove("is-active");
                }
                this.active = option;
                const input = this.input();
                if (option) {
                    option.classList.add("is-active");
                    option.scrollIntoView({ block: "nearest" });
                    input.setAttribute("aria-activedescendant", option.id);
                } else {
                    input.removeAttribute("aria-activedescendant");
                }
            },

            choose(option) {
                this.input().value = option.dataset.memberOption;
                this.filter();
            },

            /* A click anywhere on a row picks it. */
            pick(event) {
                const option = event.target.closest("[data-member-option]");
                if (option) {
                    this.choose(option);
                }
            },

            /* Arrows walk the rows still shown; Enter picks the one walked to, or the only one left. */
            onKey(event) {
                if (!event.target.hasAttribute("data-member-picker-input")) {
                    return;
                }
                const rows = this.shown();
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    if (!rows.length) {
                        return;
                    }
                    const step = event.key === "ArrowDown" ? 1 : -1;
                    const at = rows.indexOf(this.active);
                    const next = at === -1 ? (step === 1 ? 0 : rows.length - 1) : (at + step + rows.length) % rows.length;
                    this.setActive(rows[next]);
                } else if (event.key === "Enter") {
                    event.preventDefault();
                    const target = this.active || (rows.length === 1 ? rows[0] : null);
                    if (target) {
                        this.choose(target);
                    }
                }
            },
        }));
    };

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener("alpine:init", registerComponent);
    }
})();
