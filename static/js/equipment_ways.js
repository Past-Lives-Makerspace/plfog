/* The Ways to Qualify editor on the equipment Details tab and Add Equipment (#747).
 *
 * Alpine component `plEquipWays(count)`, the <section data-ways-editor> in
 * templates/hub/partials/_equipment_form_fields.html; `count` is how many ways the server
 * rendered. The form is a plain POST with one Save, so this only shapes the page:
 * "+ Add Another Way" clones the <template x-ref="template"> (the formset's empty form),
 * replacing __prefix__ with the next index and bumping ways-TOTAL_FORMS; Remove on a way
 * added since load drops it and moves every later added way down one index, so the posted
 * forms stay contiguous; a ticked pill rewrites its way's summary line with the same words
 * the server writes (EquipmentWayForm.summary). Delete on a saved way is a plain onclick in
 * the template (flip DELETE, submit), the FRONTEND.md list editor idiom.
 *
 * Loaded once, deferred, from hub/base.html's <head> before Alpine (FRONTEND.md, Scripts
 * under hx-boost), and registers on whichever side of alpine:init it lands.
 */
(function () {
  "use strict";

  var NOTHING_TICKED = "Nothing ticked yet";

  function summary(names) {
    if (!names.length) return NOTHING_TICKED;
    if (names.length === 1) return names[0];
    if (names.length === 2) return "Both " + names[0] + " and " + names[1];
    return "All of " + names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
  }

  // Move one added way from index `from` to `to`: every ways-<from>- in its names, ids and labels.
  function reindex(row, from, to) {
    var before = "ways-" + from + "-";
    var after = "ways-" + to + "-";
    var attrs = ["name", "id", "for", "aria-labelledby"];
    var nodes = [row].concat(Array.prototype.slice.call(row.querySelectorAll("*")));
    nodes.forEach(function (node) {
      attrs.forEach(function (attr) {
        var value = node.getAttribute(attr);
        if (value && value.indexOf(before) !== -1) node.setAttribute(attr, value.split(before).join(after));
      });
    });
    row.dataset.wayIndex = String(to);
  }

  function register() {
    window.Alpine.data("plEquipWays", function (count) {
      return {
        count: count,

        total: function () {
          return this.$root.querySelector('input[name="ways-TOTAL_FORMS"]');
        },

        visibleRows: function () {
          return Array.prototype.filter.call(this.$refs.rows.querySelectorAll("[data-way-row]"), function (row) {
            return !row.hidden;
          });
        },

        renumber: function () {
          var rows = this.visibleRows();
          rows.forEach(function (row, position) {
            row.querySelector("[data-way-label]").textContent = "Way " + (position + 1);
          });
          this.count = rows.length;
        },

        add: function () {
          var total = this.total();
          var index = parseInt(total.value, 10);
          var holder = document.createElement("div");
          holder.innerHTML = this.$refs.template.innerHTML.split("__prefix__").join(String(index));
          while (holder.firstElementChild) this.$refs.rows.appendChild(holder.firstElementChild);
          total.value = String(index + 1);
          this.renumber();
        },

        remove: function (row) {
          var removed = parseInt(row.dataset.wayIndex, 10);
          row.remove();
          Array.prototype.forEach.call(this.$refs.rows.querySelectorAll("[data-way-row]"), function (other) {
            var index = parseInt(other.dataset.wayIndex, 10);
            if (index > removed) reindex(other, index, index - 1);
          });
          var total = this.total();
          total.value = String(parseInt(total.value, 10) - 1);
          this.renumber();
        },

        summarize: function (row) {
          var names = Array.prototype.map.call(row.querySelectorAll("[data-way-pill]:checked"), function (box) {
            return box.dataset.wayPill;
          });
          row.querySelector("[data-way-summary]").textContent = summary(names);
        },

        onClick: function (event) {
          var button = event.target.closest("[data-way-remove]");
          if (button) this.remove(button.closest("[data-way-row]"));
        },

        onChange: function (event) {
          if (event.target.matches("[data-way-pill]")) this.summarize(event.target.closest("[data-way-row]"));
        },
      };
    });
  }

  if (window.Alpine) register();
  else document.addEventListener("alpine:init", register);
})();
