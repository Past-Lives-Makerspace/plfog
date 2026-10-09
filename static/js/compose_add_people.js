/* The announcement composer's "Add people" picker (#729).
 *
 * Alpine component `composeAddPeople`, on the recipient checklist (#compose-recipients) of a guild
 * or class announcement. The add list is every member not already listed, as nameless
 * checkboxes (so they never post); a search box hides the rows that do not match, client side.
 * "Add selected" turns every ticked row into a checked `recipients` row in the added area (or
 * re-checks the one already there), so a lead adds nine people with one click. Rows returned by
 * the "Add email addresses" endpoint call adoptRow() as they arrive, which folds a duplicate into
 * the row already listed. Every change fires `change` on the checklist, which recounts.
 *
 * Loaded once, deferred, from hub/base.html's <head> with the other Alpine components: the
 * checklist arrives on a boosted navigation and by out of band swap on an audience change, and
 * Alpine must find the component already registered either way (FRONTEND.md, Scripts under
 * hx-boost).
 */
(function () {
    "use strict";

    const registerComponent = () => {
        window.Alpine.data("composeAddPeople", () => ({
            addOpen: false,
            addPicked: 0,

            recipientBoxes() {
                return Array.from(this.$root.querySelectorAll("input[name=recipients]"));
            },

            recount() {
                this.$root.dispatchEvent(new Event("change", { bubbles: false }));
            },

            pickRows() {
                return Array.from(this.$root.querySelectorAll("[data-compose-add-row]"));
            },

            /* Hide the add rows that do not match the search, and any already added. */
            filterAddRows() {
                const input = this.$root.querySelector("[data-compose-add-filter]");
                const query = input ? input.value.trim().toLowerCase() : "";
                let shown = 0;
                this.pickRows().forEach((row) => {
                    const hide = row.hasAttribute("data-added") || (query !== "" && !row.dataset.search.includes(query));
                    row.hidden = hide;
                    if (!hide) {
                        shown += 1;
                    }
                });
                const none = this.$root.querySelector("[data-compose-add-none]");
                if (none) {
                    none.hidden = shown > 0 || this.pickRows().length === 0;
                }
            },

            countAddPicked() {
                this.addPicked = this.$root.querySelectorAll("[data-compose-add-pick]:checked").length;
            },

            /* Check the listed row for `value`, or add a checked one to the added area. */
            addRecipient(value, label) {
                const listed = this.recipientBoxes().find((box) => box.value === value);
                if (listed) {
                    listed.checked = true;
                    return;
                }
                const area = this.$root.querySelector("#compose-added-recipients");
                const row = document.createElement("label");
                row.className = "pl-recipient-checklist__row";
                const box = document.createElement("input");
                box.type = "checkbox";
                box.name = "recipients";
                box.value = value;
                box.checked = true;
                box.className = "pl-recipient-checklist__box";
                const text = document.createElement("span");
                text.className = "pl-recipient-checklist__label";
                text.textContent = label;
                row.append(box, text);
                area.appendChild(row);
            },

            /* "Add selected": every ticked member joins the recipients, checked, in one go. */
            addSelected() {
                const picks = Array.from(this.$root.querySelectorAll("[data-compose-add-pick]:checked"));
                picks.forEach((pick) => {
                    const row = pick.closest("[data-compose-add-row]");
                    this.addRecipient(pick.value, row.querySelector(".pl-recipient-checklist__label").textContent);
                    pick.checked = false;
                    row.setAttribute("data-added", "");
                });
                this.addPicked = 0;
                this.filterAddRows();
                this.recount();
                if (picks.length) {
                    const noun = picks.length === 1 ? "person" : "people";
                    this.$dispatch("show-toast", { message: `Added ${picks.length} ${noun}.`, type: "success" });
                }
            },

            /* A row the address endpoint returned: keep it unless that person is already listed. */
            adoptRow(row) {
                const box = row.querySelector("input[name=recipients]");
                const twin = this.recipientBoxes().find((other) => other !== box && other.value === box.value);
                if (twin) {
                    twin.checked = true;
                    row.remove();
                }
                this.pickRows().forEach((pickRow) => {
                    const pick = pickRow.querySelector("[data-compose-add-pick]");
                    if (pick && pick.value === box.value) {
                        pick.checked = false;
                        pickRow.setAttribute("data-added", "");
                    }
                });
                this.filterAddRows();
                this.countAddPicked();
                this.recount();
            },
        }));
    };

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener("alpine:init", registerComponent);
    }
})();
