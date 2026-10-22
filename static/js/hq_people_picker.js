/* The share panel's people pickers on the Holiday Bizarre HQ (Can open and Team).
 *
 * Alpine component `hqPeoplePicker`, on the wrapper drawn by templates/hq/partials/_people_picker.html, in the
 * HQ page's Share modal and on the card edit page. It is the Add Inquiry member picker's pattern
 * (inquiry_member_picker.js: a search box over role="option" rows drawn by the page) made to keep several
 * people: the chosen are chips, each with a hidden input that posts it and a Remove button. Typing shows the
 * rows that match by name or email (spaces collapsed, case ignored), leaving out anyone already chosen; a
 * click on a row, or Enter on the row the arrow keys moved to (or the only one left), adds its chip and
 * clears the box. Escape clears the box. Enter in the box never submits the form.
 *
 * Loaded once, deferred, from hub/base.html's <head> with the other Alpine components
 * (FRONTEND.md, Scripts under hx-boost).
 */
(function () {
    "use strict";

    const normalise = (text) => text.split(/\s+/).filter(Boolean).join(" ").toLowerCase();

    const registerComponent = () => {
        window.Alpine.data("hqPeoplePicker", () => ({
            active: null,

            input() {
                return this.$root.querySelector("[data-hq-people-input]");
            },

            list() {
                return this.$root.querySelector("[data-hq-people-list]");
            },

            options() {
                return Array.from(this.$root.querySelectorAll("[data-hq-person-option]"));
            },

            chosen() {
                return new Set(
                    Array.from(this.$root.querySelectorAll("[data-hq-people-chips] [data-hq-person]")).map(
                        (chip) => chip.dataset.hqPerson,
                    ),
                );
            },

            shown() {
                return this.options().filter((option) => !option.hidden);
            },

            /* Show the rows that match the box and are not chosen yet; the list shows only while there is a query. */
            filter() {
                const query = normalise(this.input().value);
                const chosen = this.chosen();
                this.options().forEach((option) => {
                    const taken = chosen.has(option.dataset.hqPersonOption);
                    option.hidden = taken || query === "" || !normalise(option.dataset.search).includes(query);
                });
                const anyShown = this.shown().length > 0;
                this.list().hidden = !anyShown;
                this.input().setAttribute("aria-expanded", anyShown ? "true" : "false");
                this.$root.querySelector("[data-hq-people-nomatch]").hidden = query === "" || anyShown;
                this.$root.querySelector("[data-hq-people-none]").hidden = chosen.size > 0;
                this.setActive(null);
            },

            setActive(option) {
                if (this.active) {
                    this.active.classList.remove("is-active");
                    this.active.setAttribute("aria-selected", "false");
                }
                this.active = option;
                const input = this.input();
                if (option) {
                    option.classList.add("is-active");
                    option.setAttribute("aria-selected", "true");
                    option.scrollIntoView({ block: "nearest" });
                    input.setAttribute("aria-activedescendant", option.id);
                } else {
                    input.removeAttribute("aria-activedescendant");
                }
            },

            /* Add the row's person as a chip, cloned from the picker's blank chip, and clear the box. */
            choose(option) {
                const template = this.$root.querySelector("[data-hq-people-chip-template]");
                const chip = template.content.firstElementChild.cloneNode(true);
                const pk = option.dataset.hqPersonOption;
                const name = option.dataset.name;
                chip.dataset.hqPerson = pk;
                chip.querySelector("[data-hq-person-input]").value = pk;
                chip.querySelector("[data-hq-person-name]").textContent = name;
                chip.querySelector("[data-hq-person-email]").textContent = option.dataset.email;
                chip.querySelector("[data-hq-person-remove]").setAttribute("aria-label", `Remove ${name}`);
                this.$root.querySelector("[data-hq-people-chips]").appendChild(chip);
                const input = this.input();
                input.value = "";
                this.filter();
                input.focus();
            },

            /* A click anywhere on a row picks it. */
            pick(event) {
                const option = event.target.closest("[data-hq-person-option]");
                if (option) {
                    this.choose(option);
                }
            },

            /* Remove takes the chip (and its hidden input) away; the person is offered again. */
            remove(event) {
                const chip = event.target.closest("[data-hq-person]");
                if (chip) {
                    chip.remove();
                }
                this.filter();
                this.input().focus();
            },

            /* Arrows walk the rows shown; Enter picks the one walked to, or the only one left; Escape clears. */
            onKey(event) {
                if (!event.target.hasAttribute("data-hq-people-input")) {
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
                } else if (event.key === "Escape" && event.target.value) {
                    event.preventDefault();
                    event.stopPropagation();
                    event.target.value = "";
                    this.filter();
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
